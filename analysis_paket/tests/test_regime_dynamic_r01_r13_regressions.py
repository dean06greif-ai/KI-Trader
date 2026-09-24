"""Regressionstests für Regime-Lab/Dynamic-Live Hotspots R01-R13 (isoliert, ohne echte APIs)."""

import inspect
import os
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

SOURCE_DIR = Path(os.environ.get("KI_TRADER_SOURCE_DIR", "/app/review_source/backend")).resolve()
sys.path.insert(0, str(SOURCE_DIR))

if "telegram" not in sys.modules:
    telegram_mod = types.ModuleType("telegram")
    telegram_mod.Bot = object
    sys.modules["telegram"] = telegram_mod
    constants_mod = types.ModuleType("telegram.constants")
    constants_mod.ParseMode = SimpleNamespace(HTML="HTML")
    sys.modules["telegram.constants"] = constants_mod

from services import dynamic_live
from services import dynamic_strategy
from services import regime_lab
from services import timeframes
from services import backtester
from services.backtester import simulate_pair
from routers import dynamic as dynamic_router


class _Rows:
    def __init__(self, rows=None):
        self.rows = rows or []

    async def to_list(self, _n):
        return list(self.rows)


class _Collection:
    def __init__(self):
        self.docs = {}
        self.updates = []
        self.inserts = []
        self.last_filter = None

    async def find_one(self, filt):
        self.last_filter = filt
        _id = filt.get("_id") if isinstance(filt, dict) else None
        return self.docs.get(_id)

    async def replace_one(self, filt, doc, upsert=False):
        _id = (filt or {}).get("_id")
        if _id is not None:
            self.docs[_id] = doc

    async def update_one(self, filt, upd, upsert=False):
        self.updates.append((filt, upd, upsert))

    async def insert_one(self, doc):
        self.inserts.append(doc)

    def find(self, filt):
        self.last_filter = filt
        return _Rows(self.docs.get("rows", []))

    async def delete_one(self, filt):
        return SimpleNamespace(deleted_count=1)

    async def delete_many(self, filt):
        self.last_filter = filt
        return SimpleNamespace(deleted_count=0)


@pytest.mark.anyio
async def test_r01_apply_active_ignores_sub_strategy_definitions_when_no_regime_mapping(monkeypatch):
    db = SimpleNamespace(
        strategy_coin_configs=_Collection(),
        dynamic_strategies=_Collection(),
    )
    monkeypatch.setattr(dynamic_live.state, "db", db)
    monkeypatch.setattr(dynamic_live.autotrader, "config", {})
    doc = {
        "id": "dyn1",
        "strategy_id": "base_strat",
        "sub_strategies": {"1": {"definition": {"id": "custom_a"}, "rules": [{"x": 1}]}, "2": {"definition": {"id": "custom_b"}, "rules": [{"x": 2}]}},
        "last_state": {"per_symbol": {"BTCUSDT": {"regime": 1, "label": "R1", "active_config": {"leverage": 3}}}},
    }

    applied = await dynamic_live.apply_active(doc)
    key = "base_strat_BTCUSDT"
    merged = db.strategy_coin_configs.docs[key]["config"]

    assert applied[0]["regime"] == 1
    assert merged.get("leverage") == 3
    assert "params" not in merged


@pytest.mark.anyio
async def test_r02_apply_configs_baseline_keeps_stale_overrides(monkeypatch):
    db = SimpleNamespace(
        strategy_coin_configs=_Collection(),
        dynamic_strategies=_Collection(),
    )
    monkeypatch.setattr(dynamic_live.state, "db", db)
    monkeypatch.setattr(dynamic_live.autotrader, "config", {})
    doc = {
        "id": "dyn2",
        "strategy_id": "s1",
        "last_state": {"per_symbol": {"BTCUSDT": {"regime": 1, "label": "A", "active_config": {"leverage": 5, "tp1_crv": 2}}}},
    }
    await dynamic_live.apply_configs(doc)
    assert db.strategy_coin_configs.docs["s1_BTCUSDT"]["config"]["leverage"] == 5

    doc["last_state"] = {"per_symbol": {"BTCUSDT": {"regime": 2, "label": "B", "active_config": {}}}}
    applied = await dynamic_live.apply_configs(doc)

    assert applied[0]["baseline"] is True
    assert db.strategy_coin_configs.docs["s1_BTCUSDT"]["config"]["leverage"] == 5
    assert db.strategy_coin_configs.docs["s1_BTCUSDT"]["config"]["tp1_crv"] == 2


@pytest.mark.anyio
async def test_r03_transition_protect_close_open_not_scoped_to_strategy(monkeypatch):
    auto_trades = _Collection()
    auto_trades.docs["rows"] = [
        {"id": "t1", "symbol": "BTCUSDT", "status": "open", "strategy_id": "a"},
        {"id": "t2", "symbol": "BTCUSDT", "status": "open", "strategy_id": "b"},
    ]
    db = SimpleNamespace(auto_trades=auto_trades, dynamic_transition_locks=_Collection())
    monkeypatch.setattr(dynamic_live.state, "db", db)
    monkeypatch.setattr(dynamic_live.scanner, "current_price", lambda _s: 100.0)
    closed = []

    async def _close(tid, price):
        closed.append((tid, price))
        return {"ok": True}

    monkeypatch.setattr(dynamic_live.autotrader, "manual_close", _close)
    doc = {"id": "dyn", "name": "D", "settings": {"transition_mode": "close_open", "transition_protection_enabled": True}}
    res = await dynamic_live.transition_protect(doc, ["BTCUSDT"], {"per_symbol": {"BTCUSDT": {"label": "x"}}})

    assert auto_trades.last_filter == {"symbol": {"$in": ["BTCUSDT"]}, "status": "open"}
    assert {c[0] for c in closed} == {"t1", "t2"}
    assert set(res["closed"]) == {"t1", "t2"}


@pytest.mark.anyio
async def test_r03_check_one_calls_transition_protect_even_without_auto_apply(monkeypatch):
    db = SimpleNamespace(dynamic_strategies=_Collection())
    monkeypatch.setattr(dynamic_live.state, "db", db)
    monkeypatch.setattr(dynamic_live, "refresh_state", AsyncMock(return_value={"per_symbol": {"BTCUSDT": {"regime": 2}}}))
    monkeypatch.setattr(dynamic_live, "_switched_symbols", lambda _d, _s: ["BTCUSDT"])
    monkeypatch.setattr(dynamic_live, "log_switches", AsyncMock(return_value=1))
    tp = AsyncMock(return_value={"mode": "block_new"})
    monkeypatch.setattr(dynamic_live, "transition_protect", tp)
    monkeypatch.setattr(dynamic_live, "apply_active", AsyncMock())

    doc = {"id": "dyn", "last_state": {"per_symbol": {"BTCUSDT": {"regime": 1}}}, "settings": {"require_confirmation": True}}
    await dynamic_live.check_one(doc, days=30, auto_apply=False)

    assert tp.await_count == 1


@pytest.mark.anyio
async def test_r04_check_one_updates_last_state_before_failed_apply_and_skips_retry(monkeypatch):
    db = SimpleNamespace(dynamic_strategies=_Collection(), dynamic_switch_log=_Collection())
    monkeypatch.setattr(dynamic_live.state, "db", db)
    new_state = {"per_symbol": {"BTCUSDT": {"regime": 2, "label": "R2", "confidence": 0.9}}, "checked_at": "x"}
    monkeypatch.setattr(dynamic_live, "refresh_state", AsyncMock(return_value=new_state))
    monkeypatch.setattr(dynamic_live, "transition_protect", AsyncMock(return_value=None))
    apply = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(dynamic_live, "apply_active", apply)

    doc = {"id": "dyn", "name": "d", "last_state": {"per_symbol": {"BTCUSDT": {"regime": 1}}}, "settings": {"require_confirmation": False}}
    first = await dynamic_live.check_one(doc, days=30, auto_apply=True)
    second = await dynamic_live.check_one(doc, days=30, auto_apply=True)

    assert first["switches"] == 1
    assert second["switches"] == 0
    assert apply.await_count == 1


def test_r05_symbol_payload_uses_final_labels_and_regime_ranges_reads_segments(monkeypatch):
    candles = [{"timestamp": i * 60_000, "close": 100 + i} for i in range(30)]
    monkeypatch.setattr(regime_lab.rg, "is_v2", lambda _m: True)
    monkeypatch.setattr(regime_lab.eng, "reactive_payload", lambda _m, _c: {"final_labels": [1] * 10 + [2] * 20, "live_labels": [1] * 30, "report": {}})
    monkeypatch.setattr(regime_lab, "_validation_payload", lambda *_a, **_k: None)
    monkeypatch.setattr(regime_lab, "_current_payload", lambda *_a, **_k: None)
    monkeypatch.setattr(regime_lab, "_ideal_payload", lambda *_a, **_k: None)
    _labels, entry = regime_lab._symbol_payload({"config": {}}, candles, "1m", 0.7, 2, False)

    assert any(s["regime"] == 2 for s in entry["segments"])
    assert all(s["regime"] == 1 for s in entry["live_segments"])

    doc = {"combined": {"per_symbol": {"BTCUSDT": {"segments": entry["segments"], "live_segments": entry["live_segments"]}}}, "bounds": {"BTCUSDT": {}}}
    ranges = regime_lab.regime_ranges(doc, "combined", None, "BTCUSDT", regime_id=2, only_train=False)
    assert len(ranges) > 0


@pytest.mark.anyio
async def test_r06_fetch_histories_end_anchor_can_shrink_data_when_source_rolls(monkeypatch):
    call_n = {"n": 0}

    async def _fetch(_session, _sym, days, job=None):
        call_n["n"] += 1
        shift = call_n["n"] - 1
        return [{"timestamp": (1000 + shift + i) * 60_000, "open": 1, "high": 1, "low": 1, "close": 1} for i in range(days)]

    monkeypatch.setattr("services.backtester.fetch_history", _fetch)
    monkeypatch.setattr("services.timeframes.aggregate_candles", lambda x, _tf: x)

    out1 = await regime_lab.fetch_histories(["BTCUSDT"], 120, "1m", end_ts={"BTCUSDT": (1100) * 60_000})
    out2 = await regime_lab.fetch_histories(["BTCUSDT"], 120, "1m", end_ts={"BTCUSDT": (1100) * 60_000})

    l1 = len(out1.get("BTCUSDT", []))
    l2 = len(out2.get("BTCUSDT", []))
    assert l2 < l1 or ("BTCUSDT" in out1 and "BTCUSDT" not in out2)


def test_r07_segment_boundary_overlap_between_segments_payload_and_segments_from_ranges():
    candles = [{"timestamp": i, "open": 1, "high": 1, "low": 1, "close": 1} for i in range(25)]
    labels = [1] * 10 + [2] * 10 + [2] * 5
    segs = regime_lab._segments_payload(candles, labels)
    r1 = [x for x in segs if x["regime"] == 1]
    r2 = [x for x in segs if x["regime"] == 2]
    s1 = regime_lab.segments_from_ranges(candles, r1, 1, warmup_bars=0)[0]
    s2 = regime_lab.segments_from_ranges(candles, r2, 2, warmup_bars=0)[0]

    ts1 = {c["timestamp"] for c in s1["candles"]}
    ts2 = {c["timestamp"] for c in s2["candles"]}
    assert len(ts1 & ts2) >= 1


class _DummyStrategy:
    STRATEGY_NAME = "dummy"

    def check_signal(self, window, symbol, settings):
        return None


def _mk_candles(n=120, base=100.0):
    out = []
    for i in range(n):
        out.append({"timestamp": i * 60_000, "open": base, "high": base + 0.2, "low": base - 0.2, "close": base, "volume": 1})
    return out


def test_r08_open_position_at_segment_end_is_dropped_from_metrics():
    candles = _mk_candles()

    def provider(i):
        if i == 85:
            return {"type": "LONG", "entry_price": 100}
        return None

    res = simulate_pair(
        _DummyStrategy(),
        candles,
        "BTCUSDT",
        {},
        {"max_capital": 100, "leverage": 2, "sl_mode": "fixed", "sl_fixed_percent": 20, "tp_mode": "percent", "tp1_percent": 20, "tp_full_percent": 25},
        None,
        True,
        None,
        provider,
    )
    assert res["trades"] == 0
    assert res["fees"] == 0


@pytest.mark.parametrize("side,entry,tp_mult", [("LONG", 100, 1.01), ("SHORT", 100, 0.99)])
def test_r08_tp1_and_tpf_same_bar_skips_tp1_partial(side, entry, tp_mult, monkeypatch):
    candles = _mk_candles()
    hit_idx = 86
    if side == "LONG":
        for i in range(hit_idx, len(candles)):
            candles[i]["high"] = entry * 1.05
    else:
        for i in range(hit_idx, len(candles)):
            candles[i]["low"] = entry * 0.95

    def provider(i):
        if i == 85:
            return {"type": side, "entry_price": entry}
        return None

    def _levels(_cfg, s, ent, _window):
        if s == "LONG":
            return 90.0, 101.0, 101.0, 10.0, 1.0
        return 110.0, 99.0, 99.0, 10.0, 1.0

    monkeypatch.setattr(backtester, "compute_levels", _levels)

    res = simulate_pair(
        _DummyStrategy(),
        candles,
        "BTCUSDT",
        {},
        {
            "max_capital": 100,
            "leverage": 2,
            "sl_mode": "fixed",
            "sl_fixed_percent": 20,
            "tp_mode": "percent",
            "tp1_percent": 1,
            "tp_full_percent": 1,
            "tp1_close_percent": 50,
        },
        None,
        True,
        None,
        provider,
    )
    assert res["trades"] == 1
    assert res["all_trades"][0]["tp1_done"] is False


def test_r09_keep_assign_do_not_invalidate_walkforward_and_build_has_no_verdict_guard():
    src_keep = inspect.getsource(dynamic_router) + inspect.getsource(regime_lab)
    src_build = inspect.getsource(__import__("routers.regime_lab", fromlist=["build_dynamic"]))

    assert "walkforward" in src_keep
    assert "fingerprint" not in src_build
    assert "verdict" in src_build
    assert "passed" not in src_build


def test_r10_selection_logic_uses_holdout_for_best_choice_contract():
    src = inspect.getsource(regime_lab)
    assert "best = max((r for r in rows if r.get(\"holdout_direction_pct\") is not None)" in src
    assert "score = (hold or 0.0) - 4.0 * out_band" in src


def test_r11_drop_partial_default_false_and_callers_use_default():
    candles = [
        {"timestamp": 0, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"timestamp": 30 * 60_000, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
    ]
    keep_partial = timeframes.aggregate_candles(candles, "1h")
    drop_partial = timeframes.aggregate_candles(candles, "1h", drop_partial=True)
    assert len(keep_partial) == 1
    assert len(drop_partial) == 0

    src_live = inspect.getsource(dynamic_live)
    assert "aggregate_candles(raw, timeframe)" in src_live
    assert "aggregate_candles(raw, tf)" in src_live


@pytest.mark.anyio
async def test_r12_eval_dynamic_falls_back_to_base_and_apply_regime_strategies_disables_unmapped(monkeypatch):
    picked = []

    async def _fake_rows_for(strat_fn, segs, settings_fn, cfg_fn, should_stop):
        out = []
        for sym, seg in segs:
            picked.append(strat_fn(seg))
            out.append((seg, [{"opened": "2024-01-01T00:00:00+00:00", "closed": "2024-01-01T00:01:00+00:00", "pnl": 1, "fees": 0}]))
        return out

    monkeypatch.setattr(dynamic_strategy, "_rows_for", _fake_rows_for)
    monkeypatch.setattr(dynamic_strategy, "metrics_from_rows", lambda rows, _c: {"trades": len(rows), "pnl": sum(r.get("pnl", 0) for r in rows)})
    segs = {"BTCUSDT": [{"regime": 2, "start_ts": 0}]}
    m, _rows = await dynamic_strategy.eval_dynamic("BASE", segs, configs={}, base_cfg={"max_capital": 100}, settings={}, strategies_by_regime={1: "R1"})
    assert picked == ["BASE"]
    assert m["trades"] == 1

    db = SimpleNamespace(strategy_coin_configs=_Collection(), dynamic_strategies=_Collection())
    monkeypatch.setattr(dynamic_live.state, "db", db)
    monkeypatch.setattr(dynamic_live.autotrader, "config", {})
    toggles = []

    async def _set_t(sid, symbol, enabled):
        toggles.append((sid, symbol, enabled))

    monkeypatch.setattr(dynamic_live, "_set_toggle", _set_t)
    monkeypatch.setattr(dynamic_live, "_apply_strategy_params", AsyncMock())
    doc = {
        "id": "d",
        "regime_strategies": {"1": "STRAT_A"},
        "configs": {},
        "last_state": {"per_symbol": {"BTCUSDT": {"regime": 2, "label": "x", "confidence": 0.6}}},
    }
    applied = await dynamic_live.apply_regime_strategies(doc)
    assert applied[0]["strategy_id"] is None
    assert toggles == [("STRAT_A", "BTCUSDT", False)]


@pytest.mark.anyio
async def test_r13_dynamic_confirm_uses_last_state_and_delete_only_removes_doc_and_log(monkeypatch):
    doc = {
        "id": "dyn1",
        "pending_switch": {"per_symbol": {"BTCUSDT": {"regime": 1}}},
        "last_state": {"per_symbol": {"BTCUSDT": {"regime": 2, "active_config": {"leverage": 9}}}},
        "strategy_id": "s",
    }
    monkeypatch.setattr(dynamic_router, "_get_doc", AsyncMock(return_value=doc))
    apply = AsyncMock(return_value=[{"symbol": "BTCUSDT", "regime": 2}])
    monkeypatch.setattr(dynamic_router.dynamic_live, "apply_active", apply)
    db = SimpleNamespace(dynamic_strategies=_Collection(), dynamic_switch_log=_Collection())
    monkeypatch.setattr(dynamic_router.state, "db", db)

    res = await dynamic_router.dynamic_confirm("dyn1", True)
    assert res["status"] == "success"
    assert apply.await_args.args[0]["last_state"]["per_symbol"]["BTCUSDT"]["regime"] == 2

    delete_res = await dynamic_router.dynamic_delete("dyn1", True)
    assert delete_res["status"] == "deleted"
    src = inspect.getsource(dynamic_router.dynamic_delete)
    assert "strategy_coin_configs" not in src
    assert "dynamic_transition_locks" not in src
