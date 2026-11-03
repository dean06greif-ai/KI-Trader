"""Regression 10/2026 (2): Kurze Feinsuche, Versionsvergleich dynamischer Strategien."""
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import dynamic_versions as dv  # noqa: E402
from services import regime_autopilot as ap  # noqa: E402
from services import regime_finetune as ft  # noqa: E402


# ---------------- Kurze Feinsuche ----------------
def test_fine_settings_caps_and_defaults():
    s = ft.fine_settings({})
    assert s["max_minutes"] == ft.FINE_MAX_MINUTES and s["max_rounds"] == ft.FINE_MAX_ROUNDS
    assert s["plateau_rounds"] == ft.FINE_PLATEAU_ROUNDS and s["fine_mode"] is True
    assert s["search_detectors"] is False and s["tf_chain"] is False and s["warm_start"] is False
    s2 = ft.fine_settings({"max_minutes": 600, "max_rounds": 5000, "plateau_rounds": 20})
    assert s2["max_minutes"] == ft.FINE_MAX_MINUTES_CAP and s2["max_rounds"] == ft.FINE_MAX_ROUNDS
    assert s2["plateau_rounds"] == 20


def test_fine_mutation_stays_close():
    rng = random.Random(1)
    base = {"detector": "ema", "ema_regime_days": 10.0, "ema_regime_thr": 0.2}
    space = ap.space_for("ema")
    for stale in (0, 5, 50):
        for _ in range(50):
            cand = ap.mutate(base, rng, search_detectors=True, stale_rounds=stale, fine=True)
            assert cand["detector"] == "ema"  # kein Grundgerüst-Wechsel
            diff = ap.config_diff(base, cand)
            assert 1 <= len(diff) <= 2
            for k, v in diff.items():
                spec = space[k]
                if isinstance(spec, list):
                    continue
                cur = base.get(k, ap.eng.DEFAULT_CONFIG.get(k))
                if cur is not None:
                    assert abs(float(v) - float(cur)) <= spec[2] + 1e-9  # ±1 Schritt


def test_normal_mutation_unchanged_default():
    rng = random.Random(2)
    base = {"detector": "ema", "ema_regime_days": 10.0}
    cand = ap.mutate(base, rng, False, 0)
    assert cand["detector"] == "ema"


def test_pick_start_prefers_best_graded_analysis():
    analyses = [
        {"id": "a1", "name": "alt gut", "timeframe": "1h", "symbols": ["BTCUSDT"], "created_at": "2026-01",
         "engine_config": {"detector": "ema"}, "grade": "gut"},
        {"id": "a2", "name": "sehr gut", "timeframe": "4h", "symbols": ["BTCUSDT", "ETHUSDT"], "created_at": "2025-12",
         "engine_config": {"detector": "kombi"}, "grade": "sehr gut"},
        {"id": "a3", "name": "fremd", "timeframe": "1h", "symbols": ["XAUUSD"], "created_at": "2026-02",
         "engine_config": {"detector": "jump"}, "grade": "sehr gut"},
    ]
    st = ft.pick_start(analyses, [], ["BTCUSDT", "ETHUSDT"], "1h")
    assert st["analysis_id"] == "a2" and st["grade"] == "sehr gut"


def test_pick_start_falls_back_to_best_run_then_none():
    runs = [{"id": "r1", "symbols": ["BTCUSDT"], "timeframe": "1h", "score": 50, "engine_config": {"detector": "ema"}},
            {"id": "r2", "symbols": ["BTCUSDT"], "timeframe": "1h", "score": 62, "engine_config": {"detector": "kombi"}}]
    st = ft.pick_start([], runs, ["BTCUSDT"], "1h")
    assert st["run_id"] == "r2"
    assert ft.pick_start([], [], ["BTCUSDT"], "1h") is None
    weak = [{"id": "w", "symbols": ["BTCUSDT"], "timeframe": "1h", "engine_config": {"detector": "ema"}, "grade": "mittel"}]
    assert ft.pick_start(weak, runs, ["BTCUSDT"], "1h")["run_id"] == "r2"  # Lauf schlägt „mittel“
    assert ft.pick_start(weak, [], ["BTCUSDT"], "1h")["analysis_id"] == "w"


# ---------------- Versionsvergleich ----------------
def _v(version, mapping, subs=None, summary_strat=None):
    snap = {"regime_strategies": mapping, "sub_strategies": subs or {}}
    summary = [{"regime": 0, "label": "Auf", "traded": bool(mapping.get("0")),
                "strategy_name": summary_strat or mapping.get("0"), "own_rules": bool(subs),
                "trade_params": {"tp1_crv": 2}, "strategy_params": {}},
               {"regime": 1, "label": "Ab", "traded": False, "strategy_name": None, "own_rules": False,
                "trade_params": {}, "strategy_params": {}}]
    return {"version": version, "created_at": "x", "reason": f"r{version}", "release_status": "draft",
            "snapshot": snap, "summary": summary}


def test_compare_marks_changes_and_optimized_metrics():
    opt = {0: {"strategy_id": "ema", "metrics": {"pnl": 12.0, "trades": 9}, "score": 61.0}}
    a, b = _v(1, {"0": "ema"}), _v(2, {"0": None})
    res = dv.compare(a, b, opt)
    row0 = next(r for r in res["phases"] if r["regime"] == 0)
    assert row0["changed"] and row0["a"]["is_optimized"] and row0["a"]["metrics"]["pnl"] == 12.0
    assert row0["b"]["traded"] is False and row0["b"]["metrics"] is None
    assert res["changed_count"] == 1 and res["a"]["version"] == 1


def test_compare_own_rules_optimized_only_with_same_definition():
    defin = {"long_rules": [1]}
    opt = {0: {"strategy_id": "base", "definition": defin, "metrics": {"pnl": 1}}}
    same = _v(1, {"0": "base"}, subs={"0": {"definition": defin}}, summary_strat="Eigene Regeln")
    other = _v(2, {"0": "base"}, subs={"0": {"definition": {"long_rules": [2]}}}, summary_strat="Eigene Regeln")
    res = dv.compare(same, other, opt)
    row0 = res["phases"][0]
    assert row0["a"]["is_optimized"] is True and row0["b"]["is_optimized"] is False
