"""Tests Key-Level-Limit-Orders (services/key_level_limits.py):
Validierung (Seite/Distanz), Platzieren + Ersetzen, Ablauf, Fill über die
Pipeline (gemockt), Neu-Bewertung je Zyklus (cancel_limit / Gegenrichtung /
Market-Ersatz) und Feld-Parsing aus der KI-Entscheidung."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def _sig(sym="BTCUSDT", side="LONG", entry=99.0):
    return {"symbol": sym, "type": side, "entry_price": entry,
            "stop_loss": entry * 0.99, "take_profit_1": entry * 1.01,
            "take_profit_full": entry * 1.02, "strategy_id": "ai_trader"}


def _dec(**kw):
    d = {"id": "dec1", "action": "LONG", "price": 100.0, "confidence": 80,
         "horizon": "scalp", "setup": "pullback", "limit_valid_min": 60,
         "levels_reason": "Order-Block 99", "reasoning": "Test"}
    d.update(kw)
    return d


async def main():
    from services import key_level_limits as kll

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"] + "_test_kll"]
    await db.ai_limit_orders.delete_many({})
    await db.ai_chat.delete_many({})

    # ---------- 1) Feld-Parsing (robust gegen kaputtes LLM-JSON) ----------
    f = kll.parse_decision_fields({"entry_type": "LIMIT", "limit_price": "99.5",
                                   "limit_valid_min": 9999, "cancel_limit": 1})
    assert f["entry_type"] == "limit" and f["limit_price"] == 99.5
    assert f["limit_valid_min"] == kll.MAX_VALID_MIN and f["cancel_limit"] is True
    f = kll.parse_decision_fields({"entry_type": "quatsch", "limit_price": "abc",
                                   "limit_valid_min": "xx"})
    assert f["entry_type"] == "market" and f["limit_price"] is None
    assert f["limit_valid_min"] == kll.DEFAULT_VALID_MIN
    print("PASS 1: parse_decision_fields normalisiert defensiv")

    # ---------- 2) Validierung: Seite + Distanz ----------
    ok, _ = kll.validate("LONG", 99.0, 100.0)
    assert ok
    ok, why = kll.validate("LONG", 101.0, 100.0)
    assert not ok and "UNTER" in why
    ok, why = kll.validate("SHORT", 99.0, 100.0)
    assert not ok and "ÜBER" in why
    ok, why = kll.validate("LONG", 90.0, 100.0)  # 10% > 2.5% (scalp)
    assert not ok and "zu weit" in why
    ok, _ = kll.validate("LONG", 93.0, 100.0, horizon="swing")  # 7% < 8%
    assert ok
    ok, why = kll.validate("LONG", 99.99, 100.0)  # 0.01% < 0.05%
    assert not ok and "zu nah" in why
    ok, why = kll.validate("LONG", None, 100.0)
    assert not ok
    print("PASS 2: validate() Seite/Distanz scalp+swing")

    # ---------- 3) Platzieren + Ersetzen (Symbol+Richtung) ----------
    oid1 = await kll.place(db, _sig(entry=99.0), _dec())
    assert oid1
    oid2 = await kll.place(db, _sig(entry=98.5), _dec())  # ersetzt oid1
    rows = await db.ai_limit_orders.find({}).to_list(10)
    by_id = {r["id"]: r for r in rows}
    assert by_id[oid1]["status"] == "cancelled"
    assert by_id[oid2]["status"] == "pending" and by_id[oid2]["limit_price"] == 98.5
    print("PASS 3: place() ersetzt bestehende Order gleicher Richtung")

    # ---------- 4) Fill über die Pipeline (gemockt) ----------
    import core.pipeline as pipeline
    orig_emit = pipeline.emit_ai_signal
    emitted = []

    async def fake_emit(sig):
        emitted.append(sig)
        sig["_trade_opened"] = True
        sig["id"] = "sig-x"
        return True

    pipeline.emit_ai_signal = fake_emit
    try:
        # Preis über Limit -> kein Fill
        r = await kll.check_fills(db, {"BTCUSDT": 99.2})
        assert r["filled"] == 0 and not emitted
        # Preis berührt Limit -> Fill via Pipeline
        r = await kll.check_fills(db, {"BTCUSDT": 98.4})
        assert r["filled"] == 1 and len(emitted) == 1
        assert emitted[0]["ai_limit_fill"] is True
        assert emitted[0]["entry_price"] == 98.5
        row = await db.ai_limit_orders.find_one({"id": oid2})
        assert row["status"] == "filled" and row["touch_price"] == 98.4
        # Kein Doppel-Fill
        r = await kll.check_fills(db, {"BTCUSDT": 98.0})
        assert r["filled"] == 0 and len(emitted) == 1
        print("PASS 4: Fill bei Level-Berührung, exakt einmal, via Pipeline")

        # ---------- 5) Ablauf (expiry) ----------
        oid3 = await kll.place(db, _sig(entry=97.0), _dec())
        past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
        await db.ai_limit_orders.update_one({"id": oid3},
                                            {"$set": {"expires_at": past}})
        r = await kll.check_fills(db, {"BTCUSDT": 99.0})
        assert r["expired"] == 1
        row = await db.ai_limit_orders.find_one({"id": oid3})
        assert row["status"] == "expired"
        print("PASS 5: abgelaufene Orders verfallen automatisch")

        # ---------- 6) Abgelehnter Fill (Guards) wird sauber verbucht ----------
        async def fake_emit_reject(sig):
            sig["_trade_opened"] = False
            sig["_reject_reason"] = "Kapital-Limit erreicht"
            return True

        pipeline.emit_ai_signal = fake_emit_reject
        oid4 = await kll.place(db, _sig(entry=98.0), _dec())
        await kll.check_fills(db, {"BTCUSDT": 97.9})
        row = await db.ai_limit_orders.find_one({"id": oid4})
        assert row["status"] == "rejected" and "Kapital" in row["reject_reason"]
        print("PASS 6: Guard-Ablehnung beim Fill -> status rejected + Grund")
    finally:
        pipeline.emit_ai_signal = orig_emit

    # ---------- 7) Neu-Bewertung je Zyklus ----------
    a = await kll.place(db, _sig(side="LONG", entry=99.0), _dec())
    b = await kll.place(db, _sig(sym="BTCUSDT", side="SHORT", entry=101.0),
                        _dec(action="SHORT"))
    # HOLD ohne cancel_limit -> nichts passiert
    n = await kll.reevaluate(db, "BTCUSDT", {"action": "HOLD"})
    assert n == 0
    # Gegenrichtung LONG -> SHORT-Order fliegt
    n = await kll.reevaluate(db, "BTCUSDT", {"action": "LONG", "entry_type": "limit"})
    assert n == 1
    assert (await db.ai_limit_orders.find_one({"id": b}))["status"] == "cancelled"
    assert (await db.ai_limit_orders.find_one({"id": a}))["status"] == "pending"
    # Market gleicher Richtung, wirklich eröffnet -> Limit obsolet
    n = await kll.reevaluate(db, "BTCUSDT", {"action": "LONG", "entry_type": "market",
                                             "signaled": True})
    assert n == 1
    assert (await db.ai_limit_orders.find_one({"id": a}))["status"] == "cancelled"
    # cancel_limit räumt alles ab
    c = await kll.place(db, _sig(entry=99.0), _dec())
    n = await kll.reevaluate(db, "BTCUSDT", {"action": "HOLD", "cancel_limit": True})
    assert n == 1
    assert (await db.ai_limit_orders.find_one({"id": c}))["status"] == "cancelled"
    print("PASS 7: Neu-Bewertung: Gegenrichtung/Market-Ersatz/cancel_limit")

    # ---------- 8) Kontext-Block + API-Liste + manueller Cancel ----------
    d = await kll.place(db, _sig(entry=99.0), _dec())
    txt = await kll.pending_context(db)
    assert "WARTENDE LIMIT-ORDERS" in txt and "BTCUSDT" in txt and "cancel_limit" in txt
    listing = await kll.list_orders(db)
    assert len(listing["orders"]) == 1
    assert listing["orders"][0]["expires_in_s"] > 0
    assert "signal" not in listing["orders"][0]
    assert await kll.cancel(db, d, "Test") is True
    assert await kll.cancel(db, d, "Test") is False  # schon storniert
    assert await kll.pending_context(db) == ""
    print("PASS 8: Prompt-Kontext, API-Liste, manueller Cancel")

    await client.drop_database(os.environ["DB_NAME"] + "_test_kll")
    print("ALLE KEY-LEVEL-LIMIT-TESTS GRÜN")


def test_key_level_limits():
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
