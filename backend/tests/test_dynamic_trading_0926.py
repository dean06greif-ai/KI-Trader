"""Regressionstests 09/2026: dynamische Strategie als handelbare Strategie,
Dynamik-Werkbank, Regime-Autopilot-Fixes (Phase/Score-Drift/Balken) und
Worker-Datenordner ohne fremden globalen Pfad. Rein (ohne DB/Netz)."""
import asyncio

import pytest

from services import dynamic_runtime as rt
from services import dynamic_workbench as wb
from services import local_exec
from services import regime_autopilot as ap
from services.dynamic_live import _owned_strategy_ids
from strategies.registry import registry

DOC = {"id": "dyn_test0001", "name": "Test Dyn", "timeframe": "1h", "strategy_id": "nnfx_trend",
       "symbols": ["BTCUSDT"],
       "model": {"regimes": [{"id": 0, "label": "Auf"}, {"id": 1, "label": "Seit"},
                             {"id": 2, "label": "Ab"}]},
       "configs": {"0": {"tp_full_crv": 2.5, "leverage": 25}},
       "regime_strategies": {"0": "nnfx_trend", "1": "mr_zscore"},
       "settings": {}, "release": {"status": "legacy"}}


@pytest.fixture
def dyn():
    strat = registry.upsert_dynamic(dict(DOC))
    yield strat
    registry.remove_dynamic(DOC["id"])
    rt.STATE.pop(DOC["id"], None)


def _candles(n=400):
    out, p = [], 100.0
    for i in range(n):
        p *= 1.001 if (i // 40) % 2 == 0 else 0.999
        out.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": p, "high": p * 1.002,
                    "low": p * 0.998, "close": p, "volume": 1000 + i})
    return out


def test_registry_dynamic_roundtrip(dyn):
    assert registry.is_dynamic(DOC["id"])
    meta = next(m for m in registry.list_all() if m["id"] == DOC["id"])
    assert meta["is_dynamic"] is True and meta["timeframe"] == "1h"
    regs = {r["id"]: r for r in meta["dynamic"]["regimes"]}
    assert regs[0]["traded"] and regs[1]["traded"] and not regs[2]["traded"]
    registry.remove_dynamic(DOC["id"])
    assert registry.get(DOC["id"]) is None


def test_analyze_idle_without_regime(dyn):
    res = dyn.analyze(_candles(), "BTCUSDT", {})
    assert res["signal_type"] is None
    assert "unbekannt" in res["dynamic"]["status"]


def test_analyze_unmapped_regime_trades_nothing(dyn):
    rt.STATE[DOC["id"]] = {"BTCUSDT": {"regime": 2, "label": "Ab", "confidence": 80}}
    res = dyn.analyze(_candles(), "BTCUSDT", {})
    assert res["signal_type"] is None and "nicht gehandelt" in res["dynamic"]["status"]


def test_analyze_delegates_to_regime_strategy(dyn):
    rt.STATE[DOC["id"]] = {"BTCUSDT": {"regime": 0, "label": "Auf", "confidence": 80}}
    res = dyn.analyze(_candles(), "BTCUSDT", {})
    assert res["dynamic"]["sub_strategy_id"] == "nnfx_trend"
    assert res["dynamic"]["regime"] == 0
    # Geld-/Hebel-Keys bleiben beim Nutzer, SL/TP kommt aus der Regime-Optimierung
    assert res["cfg_overrides"] == {"tp_full_crv": 2.5}


def test_unreleased_draft_never_signals(dyn):
    draft = {**DOC, "release": {"status": "draft"}}
    strat = registry.upsert_dynamic(draft)
    rt.STATE[DOC["id"]] = {"BTCUSDT": {"regime": 0, "label": "Auf"}}
    res = strat.analyze(_candles(), "BTCUSDT", {})
    assert res["signal_type"] is None and res["dynamic"].get("block_reason")


def test_regime_trade_cfg():
    dyn_info = {"regime": 1, "label": "Seit"}
    scc = {"regime_configs": {"1": {"max_capital": 50, "mode": "live", "enabled": True},
                              "0": {"enabled": False}}}
    block, rc = rt.regime_trade_cfg(scc, dyn_info)
    assert block is None and rc == {"max_capital": 50}
    block, rc = rt.regime_trade_cfg(scc, {"regime": 0, "label": "Auf"})
    assert block and rc == {}
    assert rt.regime_trade_cfg({}, dyn_info) == (None, {})


def test_owned_ids_include_dynamic_itself():
    assert "dyn_x" in _owned_strategy_ids({"id": "dyn_x", "strategy_id": "a"})


def test_phase_penalty_uses_shorter_phase():
    # Richtungs-Phase 3,1d im Sweet Spot, echte Regime-Phase 1,4d -> Strafe
    m = {"live_direction_phase_days": 3.1, "avg_live_phase_days": 1.4}
    assert ap.phase_penalty_for(m, 3.0) > 0
    assert ap.phase_penalty_for({"live_direction_phase_days": 3.5, "avg_live_phase_days": 3.2}, 3.0) == 0
    # Obergrenze mit dem längeren Maß
    assert ap.phase_penalty_for({"live_direction_phase_days": 20, "avg_live_phase_days": 5}, 3.0, 10.0) > 0


def test_score_metrics_penalizes_short_regime_phase():
    base = {"train_direction_pct": 80.0, "inner_direction_pct": 80.0}
    good = ap.score_metrics({**base, "live_direction_phase_days": 3.1, "avg_live_phase_days": 3.1}, 3.0)
    short = ap.score_metrics({**base, "live_direction_phase_days": 3.1, "avg_live_phase_days": 1.4}, 3.0)
    assert short < good


def test_autopilot_best_score_never_drifts_down(monkeypatch):
    """Endlos-Suche: der Bestwert darf über die Runden nie sinken und der Balken
    zeigt exakt den Bestwert-Score."""
    from services import regime_lab as lab
    # jede neue Variante ist 0,04 schlechter, aber "robuster" (längere Phase) –
    # früher wurde sie wegen der Gleichstands-Toleranz übernommen -> Drift nach unten
    rounds = iter(range(0, 200))

    def fake_eval(cfg, *a, **k):
        i = next(rounds)
        s = 60.0 - 0.04 * i
        return {"train_direction_pct": s, "inner_direction_pct": s, "switches_live": 1,
                "avg_live_phase_days": 4.0 + 0.1 * i}

    async def fake_hist(*a, **k):
        return {"BTCUSDT": _candles(300)}
    monkeypatch.setattr(ap, "evaluate_config", fake_eval)
    monkeypatch.setattr(lab, "fetch_histories", fake_hist)
    jid = lab.create_job("autopilot", {})
    asyncio.run(ap.run_autopilot(jid, {"symbols": ["BTCUSDT"], "max_rounds": 30,
                                       "engine_config": {"detector": "reactive"},
                                       "plateau_rounds": 0, "min_phase_days_target": 3.0}, None))
    job = lab.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    best = job["result"]["best"]["score"]
    assert best >= 60.0 - 1e-9
    assert job["progress"] == 100  # Job fertig


def test_worker_gets_no_foreign_global_data_dir():
    local_exec._settings_cache = {**local_exec.DEFAULT_SETTINGS, "data_dir": r"C:\Users\dean0\Desktop\x",
                                  "data_dirs": {"w-dean": r"C:\Users\dean0\Desktop\x"}}
    try:
        s = asyncio.run(local_exec.get_settings_for_worker(None, "w-kumpel"))
        assert s["data_dir"] == ""
        s = asyncio.run(local_exec.get_settings_for_worker(None, "w-dean"))
        assert s["data_dir"].endswith("x")
    finally:
        local_exec._settings_cache = None


def test_workbench_helpers():
    assert wb.candidate_rank({"validation_passed": True, "score": 1}) > \
        wb.candidate_rank({"validation_passed": False, "score": 99})
    assert wb.candidate_rank(None) < wb.candidate_rank({"score": -5})
    res = {"mode": "discovery", "definition": {"long_rules": []}, "discovery": {"rules": ["r"]}}
    c = wb.candidate_from_result(res, {"trade_params": {"tp1_crv": 1}, "score": 3}, "j1")
    assert c["rules"] == ["r"] and c["trade_params"] == {"tp1_crv": 1} and c["source_job_id"] == "j1"
    assert wb.scope_of({"settings": {"scope_key": "per_coin:ETHUSDT"}}) == ("per_coin", "ETHUSDT")
    assert wb.scope_of({"settings": {}}) == ("combined", None)
    t = wb.targets_for_refine(DOC, [0, 2], "params")
    assert t[0]["strategy_id"] == "nnfx_trend" and t[2]["strategy_id"] == "nnfx_trend"


def test_refine_own_rules_regime_uses_combo():
    doc = {**DOC, "regime_strategies": {}, "strategy_id": "custom_x",
           "sub_strategies": {"1": {"rules": ["r"], "definition": {"long_rules": [{}]}}}}
    t = wb.targets_for_refine(doc, [0, 1], "params")
    assert t[0]["mode"] == "params" and t[0]["strategy_id"] == "custom_x"
    assert t[1]["mode"] == "combo" and t[1]["strategy_id"] is None
