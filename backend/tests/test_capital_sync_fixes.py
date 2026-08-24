"""Regressionstests: Kapitalberechnung & Bitunix-Positions-Sync (Bug-Report).

Abgedeckte Bugs:
  1. Freies Kapital driftete ins Minus: used_margin() zählte stur das
     ursprüngliche max_capital – Teil-Closes und Margen-/Hebel-Änderungen
     wurden nie eingerechnet -> KI-Trader lehnte Live-Trades mit
     'kein verfügbares Kapital' ab.
  2. Externe Änderungen an Bitunix (Marge reduzieren + Hebel erhöhen,
     Partial-TP/SL direkt an der Börse) wurden nicht in den lokalen Trade
     gespiegelt (neu: AutoTradeManager.sync_position_state, vom Watchdog
     aufgerufen).
  3. Live-freies Kapital nutzt jetzt das ECHTE verfügbare Börsen-Guthaben
     als Wahrheit (free_capital), statt nur allocated - used_db.

Alle Tests laufen ohne DB, LLM oder Netzwerk (Stubs wie in
test_fix_custom_ai_trades).
"""
import asyncio

import pytest

from services.bitunix_trade import AutoTradeManager


# ---------------- Stubs ----------------
class FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def __aiter__(self):
        self._it = iter([dict(d) for d in self._docs])
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration

    async def to_list(self, *a, **kw):
        return [dict(d) for d in self._docs]


class FakeCollection:
    def __init__(self, docs=None):
        self.docs = [dict(d) for d in (docs or [])]

    def _match(self, d, q):
        for k, v in (q or {}).items():
            if isinstance(v, dict):
                if "$ne" in v and d.get(k) == v["$ne"]:
                    return False
            elif d.get(k) != v:
                return False
        return True

    def find(self, q=None, *a, **kw):
        return FakeCursor([d for d in self.docs if self._match(d, q)])

    async def find_one(self, q, *a, **kw):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None

    async def update_one(self, q, upd, **kw):
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))
                return


class FakeDB:
    def __init__(self, trades=None):
        self.auto_trades = FakeCollection(trades)
        self.settings = FakeCollection()


class FakeClient:
    """Bitunix-Client-Stub: Balance & Mark-Preis konfigurierbar."""

    def __init__(self, available=500.0, frozen=0.0, margin=100.0, mark=100.0,
                 is_configured=True):
        self._bal = {"available": available, "frozen": frozen, "margin": margin}
        self._mark = mark
        self._configured = is_configured

    def configured(self):
        return self._configured

    async def get_balance(self):
        return {"code": 0, "data": dict(self._bal)}

    async def get_mark_price(self, symbol):
        return self._mark


def _trade(**over):
    t = {"id": "t1", "symbol": "BTC", "side": "LONG", "mode": "live",
         "status": "open", "entry": 100.0, "qty": 10.0, "qty_remaining": 10.0,
         "leverage": 10.0, "max_capital": 100.0, "realized_pnl": 0.0,
         "fees_paid": 0.0, "fee_percent": 0.06, "sl": 95.0, "events": []}
    t.update(over)
    return t


def _mgr(client=None, trades=None):
    m = AutoTradeManager(client or FakeClient())
    m.set_db(FakeDB(trades))
    m.set_config({"mode": "live"})
    return m


def _run(coro):
    return asyncio.run(coro)


# ---------------- trade_bound_margin ----------------
def test_bound_margin_full_position():
    # 10 Stk * 100 USDT / 10x = 100 USDT Marge
    assert AutoTradeManager.trade_bound_margin(_trade()) == pytest.approx(100.0)


def test_bound_margin_scales_with_partial_close():
    # Nach TP1 (50% zu): nur noch 50 USDT gebunden – vorher fälschlich 100
    t = _trade(qty_remaining=5.0)
    assert AutoTradeManager.trade_bound_margin(t) == pytest.approx(50.0)


def test_bound_margin_follows_leverage_change():
    # Hebel 10x -> 20x: gebundene Marge halbiert sich
    t = _trade(leverage=20.0)
    assert AutoTradeManager.trade_bound_margin(t) == pytest.approx(50.0)


def test_bound_margin_fallback_margin_used():
    t = _trade(leverage=0, margin_used=80.0)
    assert AutoTradeManager.trade_bound_margin(t) == pytest.approx(80.0)
    t = _trade(leverage=0, margin_used=80.0, qty_remaining=5.0)
    assert AutoTradeManager.trade_bound_margin(t) == pytest.approx(40.0)


def test_bound_margin_fallback_max_capital():
    t = _trade(leverage=0, qty_remaining=2.5)
    assert AutoTradeManager.trade_bound_margin(t) == pytest.approx(25.0)


def test_bound_margin_closed_rest_zero():
    assert AutoTradeManager.trade_bound_margin(_trade(qty_remaining=0)) == 0.0


# ---------------- used_margin ----------------
def test_used_margin_mixed_trades():
    trades = [
        _trade(id="a"),                                  # 100 gebunden
        _trade(id="b", qty_remaining=5.0),               # 50 (Teil-Close)
        _trade(id="c", leverage=20.0),                   # 50 (Hebel erhöht)
        _trade(id="d", mode="paper"),                    # anderer Modus
        _trade(id="e", status="closed"),                 # geschlossen
    ]
    m = _mgr(trades=trades)
    assert _run(m.used_margin("live")) == pytest.approx(200.0)
    assert _run(m.used_margin("paper")) == pytest.approx(100.0)


# ---------------- free_capital ----------------
def test_free_capital_live_full_uses_exchange_available():
    # Allokation 'full': freies Kapital = echtes verfügbares Börsen-Guthaben,
    # egal wie sehr die lokale Margen-Summe driftet.
    m = _mgr(FakeClient(available=321.0, margin=100.0),
             trades=[_trade(max_capital=9999.0, leverage=0, qty=0, qty_remaining=0,
                            id="x"), _trade(id="a")])
    fc = _run(m.free_capital("live"))
    assert fc["free"] == pytest.approx(321.0)
    assert fc["exchange_available"] == pytest.approx(321.0)


def test_free_capital_live_fixed_caps_at_available():
    m = _mgr(FakeClient(available=30.0, margin=100.0), trades=[_trade(id="a")])
    m.config["capital_allocation"] = {"live": {"mode": "fixed", "value": 400.0}}
    fc = _run(m.free_capital("live"))
    # alloc-used = min(400, 600) - 100 = 300, aber Börse hat nur 30 frei
    assert fc["free"] == pytest.approx(30.0)


def test_free_capital_paper_unchanged():
    m = _mgr(trades=[_trade(id="a", mode="paper")])
    m.config["capital_allocation"] = {"paper": {"mode": "full", "base_balance": 1000.0}}
    fc = _run(m.free_capital("paper"))
    assert fc["allocated"] == pytest.approx(1000.0)
    assert fc["free"] == pytest.approx(900.0)
    assert "exchange_available" not in fc


# ---------------- sync_position_state ----------------
def test_sync_external_partial_close_books_pnl():
    t = _trade()
    m = _mgr(FakeClient(mark=110.0), trades=[t])
    pos = {"qty": 5.0, "margin": 0, "leverage": 10.0}
    changes = _run(m.sync_position_state(t, pos))
    assert changes and "teilgeschlossen" in changes[0]
    saved = _run(m.db.auto_trades.find_one({"id": "t1"}))
    assert saved["qty_remaining"] == pytest.approx(5.0)
    # PnL: 5 * (110-100) = 50 brutto, Fee 5*110*0.0006 = 0.33
    assert saved["realized_pnl"] == pytest.approx(50 - 0.33, abs=0.01)
    assert saved["status"] == "open"


def test_sync_external_margin_and_leverage_change():
    # Nutzer-Szenario: Marge reduziert + Hebel erhöht direkt bei Bitunix.
    t = _trade()  # lokal: 100 USDT Marge @ 10x
    m = _mgr(trades=[t])
    pos = {"qty": 10.0, "margin": 25.0, "leverage": 40.0}
    changes = _run(m.sync_position_state(t, pos))
    assert changes and "Marge/Hebel extern" in changes[0]
    saved = _run(m.db.auto_trades.find_one({"id": "t1"}))
    assert saved["leverage"] == pytest.approx(40.0)
    assert saved["margin_used"] == pytest.approx(25.0)
    assert saved["liq_price"] is not None
    # Kapitalberechnung folgt sofort: nur noch 25 USDT gebunden
    assert AutoTradeManager.trade_bound_margin(saved) == pytest.approx(25.0)


def test_sync_ignores_manually_increased_position():
    # Misch-Position (manuell aufgestockt) darf NICHT angefasst werden
    t = _trade()
    m = _mgr(trades=[t])
    assert _run(m.sync_position_state(t, {"qty": 20.0, "margin": 50.0,
                                          "leverage": 40.0})) == []


def test_sync_ignores_paper_and_tiny_diffs():
    m = _mgr(trades=[_trade(mode="paper")])
    assert _run(m.sync_position_state(_trade(mode="paper"),
                                      {"qty": 5.0, "margin": 1.0})) == []
    # Menge/Marge quasi identisch -> kein Sync-Rauschen
    t = _trade()
    m2 = _mgr(trades=[t])
    assert _run(m2.sync_position_state(t, {"qty": 9.95, "margin": 100.4,
                                           "leverage": 10.0})) == []


def test_sync_no_flapping_with_leverage_setting():
    """Bitunix liefert 'leverage' nur als SETTING (z.B. 200x) – nach einer
    Margen-Anpassung ist der effektive Hebel anders. Die Marge ist die
    Wahrheit; das Setting darf den Sync nicht zurückkippen (Flattern)."""
    t = _trade()  # 100 USDT @ 10x
    m = _mgr(trades=[t])
    pos = {"qty": 10.0, "margin": 25.0, "leverage": 200.0}
    assert _run(m.sync_position_state(t, pos))  # 1. Sync übernimmt Marge
    saved = _run(m.db.auto_trades.find_one({"id": "t1"}))
    assert saved["margin_used"] == pytest.approx(25.0)
    assert saved["leverage"] == pytest.approx(40.0)  # effektiv, nicht 200
    # 2. Sync mit identischer Börsen-Lage: NICHTS ändert sich mehr
    assert _run(m.sync_position_state(saved, pos)) == []


def test_free_capital_recovers_after_sync():
    """End-to-End (Kernszenario des Bug-Reports): Nach externem
    Marge-runter/Hebel-hoch meldet free_capital wieder freies Kapital."""
    t = _trade(max_capital=450.0, qty=45.0, qty_remaining=45.0, leverage=10.0)  # 450 gebunden
    client = FakeClient(available=475.0, margin=25.0)
    m = _mgr(client, trades=[t])
    m.config["capital_allocation"] = {"live": {"mode": "fixed", "value": 500.0}}
    before = _run(m.free_capital("live"))
    assert before["used"] == pytest.approx(450.0)
    # Börse: Marge auf 25 USDT reduziert (Hebel 180x)
    _run(m.sync_position_state(t, {"qty": 45.0, "margin": 25.0, "leverage": 180.0}))
    m._avail_cache = (0.0, None)  # Cache invalidieren (im Betrieb: 10s)
    after = _run(m.free_capital("live"))
    assert after["used"] == pytest.approx(25.0)
    assert after["free"] == pytest.approx(475.0)
