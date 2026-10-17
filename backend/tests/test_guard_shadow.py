"""Regressionstests: Wächter-Schattentrades + autonome Fee-Wächter-Kalibrierung
(services/guard_shadow.py) – rein, ohne Mongo (FakeDB)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "analysis_regression"))
from _fakes import FakeDB  # noqa: E402

from services import guard_shadow as gs  # noqa: E402


@pytest.fixture(autouse=True)
def _reset():
    gs._last_shadow_ts.clear()
    yield
    gs._last_shadow_ts.clear()


# ---------------- reine Logik ----------------
def test_verdict():
    assert gs.verdict(1.2) == "block_wrong"
    assert gs.verdict(-0.4) == "block_right"
    assert gs.verdict(0) == "neutral"
    assert gs.verdict(None) == "neutral"


def test_limiting_factor():
    assert gs.limiting_factor("SL 0.3% < ATR-Minimum 0.5%") == "atr"
    assert gs.limiting_factor("SL zu eng: 0.2% < 0.3% (3× Fee)") == "fee"


def _reviews(n_wrong, n_right, limiting="fee", pnl_w=1.0, pnl_r=-0.8):
    rows = []
    for i in range(n_wrong):
        rows.append({"id": f"w{i}", "guard": "fee_guard", "limiting": limiting,
                     "verdict": "block_wrong", "realized_pnl": pnl_w, "ts": "2026-06-10T00:00:00+00:00"})
    for i in range(n_right):
        rows.append({"id": f"r{i}", "guard": "fee_guard", "limiting": limiting,
                     "verdict": "block_right", "realized_pnl": pnl_r, "ts": "2026-06-10T00:00:00+00:00"})
    return rows


def test_aggregate_splits_fee_guard_by_limiting():
    agg = gs.aggregate(_reviews(3, 1, "fee") + _reviews(1, 2, "atr"))
    assert agg["fee_guard"]["n"] == 7
    assert agg["fee_guard:fee"]["wrong"] == 3 and agg["fee_guard:fee"]["right"] == 1
    assert agg["fee_guard:atr"]["wrong_rate"] == pytest.approx(1 / 3, abs=0.01)


def test_plan_loosens_when_blocks_mostly_wrong():
    agg = gs.aggregate(_reviews(9, 3))
    plan = gs.plan_adjustment(agg, {"fee_guard_mult": 3.0}, {"baseline": {"fee_guard_mult": 3.0}}, 12)
    assert plan and plan["key"] == "fee_guard_mult" and plan["direction"] == "loosen"
    assert plan["to"] == 2.75


def test_plan_needs_min_samples_and_positive_pnl():
    agg = gs.aggregate(_reviews(9, 2))  # 11 < 12
    assert gs.plan_adjustment(agg, {"fee_guard_mult": 3.0}, {}, 12) is None
    # viele „falsche“ Blocks, aber Netto negativ (Mini-Gewinne, große Verluste) -> nichts
    agg = gs.aggregate(_reviews(9, 3, pnl_w=0.1, pnl_r=-5))
    assert gs.plan_adjustment(agg, {"fee_guard_mult": 3.0}, {}, 12) is None


def test_plan_tightens_only_back_towards_baseline():
    state = {"baseline": {"fee_guard_mult": 3.0}}
    agg = gs.aggregate(_reviews(2, 10))
    # nie gelockert -> kein blindes Verschärfen
    assert gs.plan_adjustment(agg, {"fee_guard_mult": 3.0}, state, 12) is None
    plan = gs.plan_adjustment(agg, {"fee_guard_mult": 2.5}, state, 12)
    assert plan["direction"] == "tighten" and plan["to"] == 2.75
    plan = gs.plan_adjustment(agg, {"fee_guard_mult": 2.9}, state, 12)
    assert plan["to"] == 3.0  # nicht über die Baseline hinaus


def test_plan_respects_guardrails():
    state = {"baseline": {"fee_guard_mult": 2.5}}
    agg = gs.aggregate(_reviews(12, 0))
    # Baseline 2.5, MAX_DEVIATION 1.5 -> Untergrenze 1.0; aktueller Wert 1.0 -> keine Änderung
    assert gs.plan_adjustment(agg, {"fee_guard_mult": 1.0}, state, 12) is None
    plan = gs.plan_adjustment(agg, {"fee_guard_mult": 1.2}, state, 12)
    assert plan["to"] == 1.0


def test_plan_atr_branch_uses_atr_key():
    agg = gs.aggregate(_reviews(12, 0, "atr"))
    plan = gs.plan_adjustment(agg, {"fee_guard_atr_mult": 4.0}, {}, 12)
    assert plan["key"] == "fee_guard_atr_mult" and plan["to"] == 3.75


# ---------------- Hook: Block -> Schattentrade ----------------
class _Mgr:
    def __init__(self, db):
        self.db = db
        self.calls = []

    async def _on_signal_impl(self, signal, candles):
        self.calls.append(signal)
        return {"id": "shadow1", **signal}


def _signal(**kw):
    s = {"symbol": "BTCUSDT", "type": "LONG", "strategy_id": "ai_trader",
         "entry_price": 100.0, "stop_loss": 99.5}
    s.update(kw)
    return s


def test_maybe_open_shadow_replays_as_collection_with_bypass():
    db = FakeDB()
    mgr = _Mgr(db)
    sig = _signal()
    trade = asyncio.run(gs.maybe_open_shadow(mgr, sig, [], "fee_guard", "SL zu eng ... ATR-Minimum",
                                             snapshot={"sl_dist_pct": 0.5}))
    assert trade and trade["id"] == "shadow1"
    shadow = mgr.calls[0]
    assert shadow["data_collection"] is True
    assert shadow["_shadow_bypass"] == ["fee_guard"]
    assert shadow["guard_shadow"]["guard"] == "fee_guard"
    assert shadow["guard_shadow"]["snapshot"] == {"sl_dist_pct": 0.5}
    assert shadow["collection_reason"] == "guard_shadow:fee_guard"
    # Original-Signal bleibt unverändert (kein data_collection)
    assert not sig.get("data_collection") and sig["_shadow_trade_id"] == "shadow1"


def test_maybe_open_shadow_cooldown_and_disable():
    db = FakeDB()
    mgr = _Mgr(db)
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(), [], "fee_guard", "x"))
    # gleicher Coin/Seite/Wächter innerhalb 15 min -> kein zweiter Schatten
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(), [], "fee_guard", "x")) is None
    # andere Seite -> erlaubt
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(type="SHORT"), [], "fee_guard", "x"))
    assert len(mgr.calls) == 2
    # per Config aus
    gs._last_shadow_ts.clear()
    asyncio.run(db.settings.insert_one({"_id": "ai_trader_config", "guard_shadow_enabled": False}))
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(symbol="ETHUSDT"), [], "fee_guard", "x")) is None


def test_maybe_open_shadow_never_for_collection_or_other_strategies():
    mgr = _Mgr(FakeDB())
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(data_collection=True), [], "fee_guard", "x")) is None
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(strategy_id="breakout"), [], "fee_guard", "x")) is None
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(), [], "risk_budget", "x")) is None
    assert mgr.calls == []


def test_maybe_open_shadow_limits_open_shadows():
    db = FakeDB()
    for i in range(gs.MAX_OPEN_SHADOWS):
        asyncio.run(db.auto_trades.insert_one({"id": f"t{i}", "status": "open",
                                               "guard_shadow": {"guard": "fee_guard"}}))
    mgr = _Mgr(db)
    assert asyncio.run(gs.maybe_open_shadow(mgr, _signal(), [], "fee_guard", "x")) is None


# ---------------- Hook: Close -> Urteil -> Autotune ----------------
def _patch_engine(monkeypatch, cfg):
    calls = []

    async def _upd(updates):
        cfg.update(updates)
        return dict(cfg)
    fake = SimpleNamespace(config=cfg, update_config=_upd, calls=calls)
    import services.ai_engine as ae
    monkeypatch.setattr(ae, "ai_engine", fake)
    return fake


def test_on_trade_closed_records_review_and_autotunes(monkeypatch):
    db = FakeDB()
    cfg = {"fee_guard_mult": 3.0, "fee_guard_atr_mult": 2.5, "guard_autotune_enabled": True,
           "guard_autotune_min_samples": 5}
    _patch_engine(monkeypatch, cfg)
    for i in range(5):
        t = {"id": f"s{i}", "symbol": "BTCUSDT", "side": "LONG", "realized_pnl": 0.9,
             "max_capital": 100, "result": "win", "ai_setup": "breakout",
             "guard_shadow": {"guard": "fee_guard", "reason": "SL zu eng (Fee)"}}
        row = asyncio.run(gs.on_trade_closed(db, t))
        assert row["verdict"] == "block_wrong" and row["limiting"] == "fee"
    assert cfg["fee_guard_mult"] == 2.75  # gelockert um einen Schritt
    log = db.guard_calibration_log.rows
    assert len(log) == 1 and log[0]["direction"] == "loosen"
    assert all(r["consumed"] for r in db.guard_shadow_reviews.rows)
    state = asyncio.run(db.settings.find_one({"_id": gs.STATE_ID}))
    assert state["baseline"]["fee_guard_mult"] == 3.0 and state["last_adjust_ts"]
    # Governance-Eintrag im KI-Chat
    assert any("Fee-Wächter autonom gelockert" in r["text"] for r in db.ai_chat.rows)
    # innerhalb 12 h keine zweite Anpassung
    for i in range(5):
        asyncio.run(gs.on_trade_closed(db, {"id": f"x{i}", "realized_pnl": 0.9,
                                            "guard_shadow": {"guard": "fee_guard", "reason": "Fee"}}))
    assert cfg["fee_guard_mult"] == 2.75


def test_on_trade_closed_ignores_non_shadow_and_autotune_off(monkeypatch):
    db = FakeDB()
    cfg = {"fee_guard_mult": 3.0, "guard_autotune_enabled": False, "guard_autotune_min_samples": 1}
    _patch_engine(monkeypatch, cfg)
    assert asyncio.run(gs.on_trade_closed(db, {"id": "n", "realized_pnl": 5})) is None
    row = asyncio.run(gs.on_trade_closed(db, {"id": "s", "realized_pnl": 5,
                                              "guard_shadow": {"guard": "fee_guard", "reason": "Fee"}}))
    assert row["verdict"] == "block_wrong"
    assert cfg["fee_guard_mult"] == 3.0  # Autotune aus -> Statistik, keine Anpassung


def test_stats_shape(monkeypatch):
    db = FakeDB()
    cfg = {"fee_guard_mult": 3.0, "guard_autotune_enabled": False}
    _patch_engine(monkeypatch, cfg)
    asyncio.run(gs.on_trade_closed(db, {"id": "a", "realized_pnl": -1,
                                        "guard_shadow": {"guard": "fee_guard", "reason": "ATR-Minimum"}}))
    asyncio.run(gs.on_trade_closed(db, {"id": "b", "realized_pnl": 2,
                                        "guard_shadow": {"guard": "trade_guard", "reason": "Kill-Switch"}}))
    asyncio.run(db.auto_trades.insert_one({"id": "o", "status": "open", "guard_shadow": {"guard": "fee_guard"}}))
    st = asyncio.run(gs.stats(db, 14))
    assert st["open_shadows"] == 1 and st["closed"] == 2
    keys = {g["key"]: g for g in st["guards"]}
    assert keys["fee_guard"]["right"] == 1 and keys["trade_guard"]["wrong"] == 1
    assert st["fee_guard_detail"]["atr"]["n"] == 1
    assert "_id" not in st["recent"][0]


# ---------------- Verdrahtung ----------------
def test_wiring_in_bitunix_trade_and_config():
    import inspect
    from services import bitunix_trade, ai_engine, entry_guard
    src = inspect.getsource(bitunix_trade)
    assert "guard_shadow.maybe_open_shadow" in src
    assert "guard_shadow.on_trade_closed" in src
    assert 'trade["guard_shadow"]' in src
    assert "fee_guard" in src and "_shadow_bypass" in src
    assert ai_engine.DEFAULT_AI_CONFIG["guard_shadow_enabled"] is True
    assert ai_engine.DEFAULT_AI_CONFIG["guard_autotune_enabled"] is True
    assert "skip" in inspect.signature(entry_guard.check_entry).parameters
