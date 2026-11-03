"""Regression 10/2026: eine Note für Autopilot-Verlauf und Regime-Lab
(Erkennungsqualität), Live/Rückblick im dynamischen Backtest, Chart-freie Doc-Loads."""
import inspect

from services import dynamic_backtest as dbt
from services import regime_autopilot as ap
from services import regime_quality as q
from tests.test_autopilot_history_import import _analysis, _entry


def _vg_entry(skill=8.0, missed=10.0):
    e = _entry(direction=93.0, holdout=93.0, inner=92.0, f1=61.0, bars=20000, hbars=5000)
    e["reference"].update({"version": 2, "holdout_f1_pct": 61.0, "holdout_balanced_pct": 60.0,
                           "holdout_skill_pct": skill, "missed_pct": missed, "mean_lag_days": 1.0,
                           "holdout_direction_pct": 75.0, "direction_pct": 72.0,
                           "live_direction_phase_days": 6.0})
    e["validation"] = {"passed": True, "avg_segment_days": 5.0}
    return e


def _metrics(entries):
    acc = ap.MetricsAccumulator("1h")
    for e in entries:
        acc.add(e)
    return acc.result()


def test_accumulator_collects_missed_and_validation():
    m = _metrics([_vg_entry(missed=10.0), _vg_entry(missed=20.0)])
    assert m["reference_missed_pct"] == 15.0 and m["validation_passed"] is True


def test_grade_from_metrics_matches_analysis_quality():
    for skill, missed in ((8.0, 10.0), (-6.0, 10.0), (8.0, 16.0)):
        entries = {"BTCUSDT": _vg_entry(skill, missed), "ETHUSDT": _vg_entry(skill, missed)}
        analysis_grade = q.summarize_scope(entries)["overall"]["grade"]
        assert q.grade_from_metrics(_metrics(entries.values()))["grade"] == analysis_grade
    assert q.grade_from_metrics({"holdout_reference_f1_pct": 60}) is None


def test_rate_result_uses_quality_grade_and_explains():
    m = _metrics([_vg_entry(skill=-6.0)])
    r = ap.rate_result({"best": {"score": 62.7, "metrics": m}})
    assert r["label"] == "gut" and r["basis"] == "quality"
    assert "Skill" in r["why"] and "Such-Score 62.7" in r["why"]
    top = ap.rate_result({"best": {"score": 60.0, "metrics": _metrics([_vg_entry()])}})
    assert top["grade"] == "top" and top["label"] == "sehr gut"


def test_rate_result_import_uses_analysis_grade():
    from services import regime_history_import as imp
    run = imp.analysis_to_run(_analysis("a1"))
    run["result"]["imported_from"]["grade"] = "sehr gut"
    assert ap.rate_result(run["result"])["label"] == "sehr gut"


def test_rate_result_legacy_without_holdout_metrics():
    r = ap.rate_result({"best": {"score": 80, "metrics": {"holdout_reference_f1_pct": 56, "avg_live_phase_days": 8}},
                        "settings": {"min_phase_days_target": 5, "max_phase_days_target": 15}})
    assert r["basis"] == "legacy" and r["grade"] == "top"


def test_phase_labels_live_vs_retro(monkeypatch):
    calls = []
    monkeypatch.setattr(dbt.rg, "classify_series", lambda *a, **k: calls.append("live") or [0])
    monkeypatch.setattr(dbt.rg, "is_v2", lambda m: True)
    from services import regime_engine as eng
    monkeypatch.setattr(eng, "final_labels", lambda m, c: calls.append("final") or [1])
    assert dbt.phase_labels({}, [], "1h", 0.5, 1) == [0]
    assert dbt.phase_labels({}, [], "1h", 0.5, 1, retro=True) == [1]
    assert calls == ["live", "final"]


def test_backtest_router_passes_label_basis():
    from routers import backtest
    assert '"dynamic_label_basis"' in inspect.getsource(backtest)
    assert 'cfg.get("dynamic_label_basis")' in inspect.getsource(dbt.simulate_dynamic)


def test_job_start_doc_loads_skip_chart_data():
    from routers import regime_lab as rl
    from services import regime_opt
    assert "lab.NO_CHART" in inspect.getsource(rl._get_doc)
    assert "lab.NO_CHART" in inspect.getsource(regime_opt._load_doc)
    assert "chart_emas" in inspect.getsource(rl._slim_doc)
