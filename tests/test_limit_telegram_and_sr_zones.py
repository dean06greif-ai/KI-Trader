"""Regressionstests: Telegram bei KI-Limit-Order-Fill/-Verfall + 4h/1d-S/R-Zonen.
Ohne Netzwerk/DB: Fake-Mongo-Collections + gepatchter Telegram-Versand."""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


class _Res:
    def __init__(self, n):
        self.modified_count = n


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *a, **k):
        return self

    async def to_list(self, n):
        return self.rows[:n]


class FakeColl:
    def __init__(self, rows=None):
        self.rows = {r["id"]: r for r in (rows or [])}
        self.inserted = []

    def find(self, q=None, proj=None):
        status = (q or {}).get("status")
        rows = [dict(r) for r in self.rows.values()
                if not isinstance(status, str) or r.get("status") == status]
        return _Cursor(rows)

    async def update_one(self, flt, update, upsert=False):
        row = self.rows.get(flt.get("id"))
        if not row or ("status" in flt and row.get("status") != flt["status"]):
            return _Res(0)
        row.update(update.get("$set") or {})
        return _Res(1)

    async def insert_one(self, doc):
        self.inserted.append(doc)

    async def delete_many(self, q):
        return None


class FakeDB:
    def __init__(self, orders):
        self.ai_limit_orders = FakeColl(orders)
        self.ai_chat = FakeColl()


def _row(**over):
    now = datetime.now(timezone.utc)
    base = {
        "id": "o1", "symbol": "BTCUSDT", "side": "LONG", "limit_price": 60000.0,
        "valid_min": 240, "status": "pending", "confidence": 78, "horizon": "swing",
        "setup": "range", "reason": "Retest der 4h-Unterstützung",
        "created_at": (now - timedelta(hours=3, minutes=12)).isoformat(),
        "expires_at": (now + timedelta(hours=1)).isoformat(),
        "signal": {"entry_price": 60000.0, "stop_loss": 59100.0,
                   "take_profit_1": 60900.0, "take_profit_full": 62400.0,
                   "ai_leverage": 20, "ai_max_capital": 100, "ai_capital_pct": 50,
                   "ai_reasoning": "Starke Nachfragezone, CRV > 2"},
    }
    base.update(over)
    return base


async def main():
    from services import key_level_limits as kl
    from services import notifications

    # ---------- 1) Nachrichten-Texte (detailliert: SL/TP, Größe, Begründung) ----
    txt = kl._tg_fill_text(_row(), True)
    assert "LIMIT-ORDER GEFÜLLT" in txt and "BTCUSDT" in txt and "LONG" in txt
    assert "59100" in txt and "60900" in txt and "62400" in txt      # SL/TP1/TP
    assert "(-1.50%)" in txt and "(+1.50%)" in txt and "(+4.00%)" in txt
    assert "Einsatz ~50 USDT" in txt and "Hebel 20x" in txt
    assert "Konfidenz 78" in txt and "swing" in txt
    assert "Retest der 4h-Unterstützung" in txt and "gewartet: 3h 12m" in txt
    txt = kl._tg_fill_text(_row(), False, "Kill-Switch aktiv")
    assert "Trade abgelehnt" in txt and "Kill-Switch aktiv" in txt
    txt = kl._tg_expired_text(_row(), 61234.0)
    assert "VERFALLEN" in txt and "240 Min" in txt and "61234" in txt
    assert "2.06% vom Level entfernt" in txt
    print("PASS 1: Telegram-Texte für Fill/Ablehnung/Verfall (detailliert)")

    # ---------- 2) Toggle limit_orders existiert und ist standardmäßig an ------
    assert notifications.DEFAULT_CONFIG.get("limit_orders") is True
    print("PASS 2: notifications.DEFAULT_CONFIG enthält limit_orders=True")

    # ---------- 3) check_fills: Verfall löst Telegram-Versand aus --------------
    sent = []

    async def _fake_tg(db, telegram, ntype, text, **kw):
        sent.append((ntype, text))
        return True

    orig = notifications.telegram_notify
    notifications.telegram_notify = _fake_tg
    try:
        expired_row = _row(id="exp1", expires_at=(datetime.now(timezone.utc)
                                                  - timedelta(minutes=5)).isoformat())
        db = FakeDB([expired_row])
        res = await kl.check_fills(db, {"BTCUSDT": 61000.0})
        assert res["expired"] == 1 and res["filled"] == 0
        assert db.ai_limit_orders.rows["exp1"]["status"] == "expired"
        assert len(sent) == 1 and sent[0][0] == "limit_orders"
        assert "VERFALLEN" in sent[0][1] and "BTCUSDT" in sent[0][1]
    finally:
        notifications.telegram_notify = orig
    print("PASS 3: check_fills sendet Telegram (Toggle limit_orders) bei Verfall")

    # ---------- 4) S/R-Zonen: Pivot-/Cluster-Analyse ---------------------------
    from services.sr_zones import compute_zones

    def _c(h, lo, c):
        return {"high": h, "low": lo, "close": c, "open": c, "volume": 1.0}

    candles = []
    for cycle in range(6):                      # oszilliert zwischen 100 und 110
        for p in [104, 102, 101, 100, 101, 102, 104, 106, 108, 109, 110, 109, 107, 105]:
            candles.append(_c(p + 0.5, p - 0.5, p))
    candles.append(_c(105.5, 104.5, 105.0))     # aktueller Preis in der Mitte
    zones = compute_zones(candles, "4h", 0.6)
    sup = [z for z in zones if z["kind"] == "support"]
    res = [z for z in zones if z["kind"] == "resistance"]
    assert sup and res, "Support- UND Resistance-Zonen erwartet"
    assert all(z["mid"] < 105.0 for z in sup)
    assert all(z["mid"] > 105.0 for z in res)
    assert all(z["low"] < z["high"] and z["tf"] == "4h" for z in zones)
    assert any(abs(z["mid"] - 99.5) < 1.5 for z in sup), "Hauptunterstützung ~100"
    assert any(abs(z["mid"] - 110.5) < 1.5 for z in res), "Hauptwiderstand ~110"
    assert all(1 <= z["strength"] <= 100 and z["touches"] >= 1 for z in zones)
    assert len(sup) <= 3 and len(res) <= 3
    assert compute_zones(candles[:5], "4h", 0.6) == []      # zu wenige Kerzen
    print("PASS 4: compute_zones liefert korrekte 4h-Support/Resistance-Zonen")

    print("\nALLE TESTS BESTANDEN")


if __name__ == "__main__":
    asyncio.run(main())
