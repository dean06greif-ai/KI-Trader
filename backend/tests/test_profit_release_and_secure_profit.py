"""Regressionstests: Gewinnsicherung mit Marge-Freisetzung (Hebel steigt) und
Bugfix secure_profit (in_profit-Erkennung + Ausführungszweig) – ohne DB/Netzwerk."""
import asyncio

import pytest

from services.ai_trade_manager import AITradeManager, DEFAULT_SETTINGS
from services.bitunix_trade import AutoTradeManager, profit_release_plan


# ---------------- profit_release_plan (reine Mathematik) ----------------
class TestProfitReleasePlan:
    def test_release_to_target_leverage(self):
        # 10 Stk @ 100 = 1000 USDT Notional, Hebel 10x -> Margin 100
        plan = profit_release_plan(10, 100.0, 10, 100)
        assert plan["new_leverage"] == 100.0
        assert plan["new_margin"] == 10.0
        assert plan["release"] == 90.0

    def test_max_reduction_caps_at_200x(self):
        plan = profit_release_plan(10, 100.0, 10, 999)
        assert plan["new_leverage"] == 200.0
        assert plan["new_margin"] == 5.0

    def test_no_release_when_target_not_higher(self):
        assert profit_release_plan(10, 100.0, 100, 100) is None
        assert profit_release_plan(10, 100.0, 100, 50) is None

    def test_no_release_on_empty_position(self):
        assert profit_release_plan(0, 100.0, 10, 100) is None


# ---------------- _release_secured_margin (Paper-Pfad) ----------------
class _FakeClient:
    def configured(self):
        return False


def _atm():
    m = AutoTradeManager.__new__(AutoTradeManager)
    m.client = _FakeClient()
    return m


def test_release_secured_margin_updates_lev_margin_liq():
    m = _atm()
    t = {"symbol": "BTCUSDT", "side": "LONG", "mode": "paper", "entry": 100.0,
         "leverage": 10.0, "profit_secure_max_leverage": 100}
    updates, events = {}, []
    asyncio.run(m._release_secured_margin(t, updates, events, qty_rem=10))
    assert updates["leverage"] == 100.0
    assert updates["margin_used"] == 10.0
    assert 0 < updates["liq_price"] < 100.0
    assert any("freigesetzt" in e for e in events)


def test_release_secured_margin_noop_when_lev_already_max():
    m = _atm()
    t = {"symbol": "BTCUSDT", "side": "LONG", "mode": "paper", "entry": 100.0,
         "leverage": 100.0, "profit_secure_max_leverage": 100}
    updates, events = {}, []
    asyncio.run(m._release_secured_margin(t, updates, events, qty_rem=10))
    # nichts freizusetzen -> als erledigt markieren (kein Endlos-Retry),
    # aber keine Hebel-/Margin-/SL-Änderung und kein Event
    assert updates == {"profit_margin_released": True} and events == []


# ---------------- secure_profit über apply_action ----------------
class _FakeColl:
    def __init__(self, docs):
        self.docs = docs
        self.updated = []

    async def find_one(self, q, *a, **k):
        for d in self.docs:
            if all(d.get(kk) == vv for kk, vv in q.items()):
                return dict(d)
        return None

    async def update_one(self, q, u, **k):
        self.updated.append((q, u))

    async def insert_one(self, doc):
        self.docs.append(doc)


class _FakeDB:
    def __init__(self, trades):
        self.auto_trades = _FakeColl(trades)
        self.ai_trade_actions = _FakeColl([])
        self.ai_chat = _FakeColl([])


class _FakeEngine:
    def __init__(self, db):
        self.db = db


class _FakeAutoTrader:
    def __init__(self, mark):
        self.mark = mark
        self.margin_calls = []

    async def _current_mark(self, symbol):
        return self.mark

    def ai_manage_allowed(self, sid, symbol):
        return True

    async def adjust_margin(self, trade_id, amount):
        self.margin_calls.append((trade_id, amount))
        return {"margin": 50.0, "leverage": 20.0, "liq_price": 99.0}


def _trade(**kw):
    t = {"id": "t1", "symbol": "BTCUSDT", "side": "LONG", "mode": "paper",
         "entry": 100.0, "qty": 10.0, "qty_remaining": 10.0, "leverage": 10.0,
         "status": "open", "ai_actions": 0, "ai_last_action_ts": 0,
         "strategy_id": "ai_trader"}
    t.update(kw)
    return t


def _manager(mark=110.0, trade=None):
    mgr = AITradeManager()
    mgr.engine = _FakeEngine(_FakeDB([trade or _trade()]))
    mgr.autotrader = _FakeAutoTrader(mark)
    mgr.settings = dict(DEFAULT_SETTINGS)
    return mgr


def test_secure_profit_executes_margin_removal_when_in_profit():
    mgr = _manager(mark=110.0)  # LONG im Gewinn
    res = asyncio.run(mgr.apply_action("t1", "secure_profit", value=50,
                                       reason="test", source="ki"))
    assert res["status"] == "ok", res
    # Margin 10*100/10 = 100 USDT -> 50% = 50 USDT ENTNAHME (negativ)
    assert mgr.autotrader.margin_calls == [("t1", -50.0)]


def test_secure_profit_blocked_when_not_in_profit():
    mgr = _manager(mark=95.0)  # LONG im Verlust
    res = asyncio.run(mgr.apply_action("t1", "secure_profit", value=50,
                                       reason="test", source="ki"))
    assert res["status"] == "blocked"
    assert "Gewinn" in res["detail"]
    assert mgr.autotrader.margin_calls == []


def test_remove_margin_gets_profit_lock_cap_when_in_profit():
    # Hebel-Deckel: max_leverage=20, im Gewinn gilt profit_lock_max_leverage=100.
    # 80 USDT entnehmen -> Resthebel 50x: nur im Gewinn erlaubt.
    mgr = _manager(mark=110.0)
    mgr.settings.update({"max_leverage": 20, "profit_lock_max_leverage": 100,
                         "profit_lock_min_margin_pct": 5})
    res = asyncio.run(mgr.apply_action("t1", "remove_margin", value=80,
                                       reason="test", source="ki"))
    assert res["status"] == "ok", res
    mgr2 = _manager(mark=95.0)
    mgr2.settings.update({"max_leverage": 20, "profit_lock_max_leverage": 100,
                          "profit_lock_min_margin_pct": 5})
    res2 = asyncio.run(mgr2.apply_action("t1", "remove_margin", value=80,
                                         reason="test", source="ki"))
    assert res2["status"] == "blocked"
