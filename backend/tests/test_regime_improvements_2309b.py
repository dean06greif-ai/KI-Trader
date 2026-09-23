"""Regressionstests 23.09 (b): Benchmark „sehr gut“, dynamische Shadow-Stichprobe,
manuelle Freigabe, Autopilot-Sweet-Spot + Referenz, Fortschritt mit Kommastelle,
Speicher-Reihenfolge lokaler Regime-Jobs (Liste nicht mehr „eins versetzt“)."""
import asyncio

from services import local_exec
from services import regime_autopilot as ap
from services import regime_lab as rlab
from services import regime_quality as rq
from services import regime_release as rr


def _entry(**kw):
    ag = {"holdout_direction_pct": 90.0, "direction_pct": 90.0, "trend_hit_pct": 80.0, "holdout_bars": 800}
    val = {"violation_bars_pct": 5.0, "avg_segment_days": 9.0, "passed": True}
    ref = {"direction_pct": 78.0, "holdout_direction_pct": 76.0, "mean_lag_days": 2.0, "missed_pct": 8.0}
    ag.update(kw.get("ag", {}))
    val.update(kw.get("val", {}))
    ref.update(kw.get("ref", {}))
    return {"live_agreement": ag, "validation": val, "corrections": {"avg_delay_days": 1.0}, "reference": ref}


# ---------------- Benchmark „sehr gut“ ----------------
def test_very_good_when_all_benchmarks_pass():
    q = rq.summarize_scope({"BTCUSDT": _entry()})
    assert q["overall"]["grade"] == "sehr gut"
    b = q["overall"]["benchmark"]
    assert b["passed"] == b["total"] == 7
    assert q["thresholds"]["very_good"]["sweet_spot_days"] == [5.0, 15.0]


def test_very_good_needs_every_criterion():
    for kw in ({"ref": {"holdout_direction_pct": 70.0}},      # Referenz knapp unter 72
               {"ref": {"mean_lag_days": 4.0}},               # Lag > ⅓ von 9 Tagen
               {"ref": {"missed_pct": 20.0}},
               {"val": {"avg_segment_days": 25.0}, "ref": {"mean_lag_days": 1.0}},  # zu träge
               {"ag": {"holdout_bars": 100}}):
        g = rq.summarize_scope({"BTCUSDT": _entry(**kw)})["overall"]["grade"]
        assert g in ("gut", "mittel"), (kw, g)


def test_legacy_analysis_without_reference_never_very_good():
    e = _entry()
    e.pop("reference")
    q = rq.summarize_scope({"BTCUSDT": e})
    assert q["overall"]["grade"] == "gut"
    assert not next(c for c in q["overall"]["benchmark"]["checks"] if c["key"] == "reference_holdout")["ok"]


def test_grade_for_classes_takes_weakest():
    doc = {"combined": {"per_symbol": {"BTCUSDT": _entry(), "EURUSD": _entry(ref={"holdout_direction_pct": 50.0,
                                                                                   "direction_pct": 50.0})}}}
    assert rq.grade_for_classes(doc, ["crypto"]) == "sehr gut"
    assert rq.grade_for_classes(doc, ["crypto", "forex"]) == "schwach"


# ---------------- dynamische Stichprobe + manuelle Freigabe ----------------
def test_activation_min_trades_by_grade():
    assert rr.activation_min_trades("sehr gut") == 10
    assert rr.activation_min_trades("gut") == 15
    assert rr.activation_min_trades("mittel") == 25
    assert rr.activation_min_trades(None) == rr.ACTIVATION_MIN_TRADES == 30
    rows = [{"regime": "bär", "trades": 12, "avg_reward": -0.5}, {"regime": "bulle", "trades": 11, "avg_reward": 0.2}]
    assert rr.validate_activation(rows, 10)[0] is True
    ok, reasons, info = rr.validate_activation(rows)          # Default bleibt 30
    assert not ok and info["min_trades"] == 30 and any("12/30" in r for r in reasons)


def test_override_blockers_policy():
    soft = ["Kalibrierung fehlt (gleiche Symbole/Timeframe)", "Ablation fehlt (gleiche Symbole/Timeframe)"]
    assert rr.override_blockers("shadow", soft, None) == []            # Shadow wirkungslos -> erlaubt
    assert rr.override_blockers("active", soft, "mittel") == []
    assert rr.override_blockers("active", soft, "schwach")              # zu riskant
    assert rr.override_blockers("active", soft, None)
    assert rr.override_blockers("shadow", soft + ["kein Modell für diesen Scope gespeichert"], "gut")
    assert rr.override_blockers("active", ["erst Stufe Shadow (Beobachten) freigeben"], "sehr gut")


def test_apply_stage_logs_override():
    rel = rr.apply_stage({"release": {}}, "shadow", {"scope": "combined"}, "Admin", "manuell",
                         override={"missing": ["Kalibrierung fehlt"], "grade": "mittel"})
    assert rel["manual_override"] is True
    h = rel["history"][-1]
    assert h["override"] and h["override_missing"] == ["Kalibrierung fehlt"] and h["quality_grade"] == "mittel"
    assert rr.apply_stage({"release": rel}, "none", {}, "Admin", "zurück")["manual_override"] is False


# ---------------- Autopilot: Sweet Spot + Referenz ----------------
def test_score_penalizes_too_long_phases_only_with_max():
    m = {"inner_direction_pct": 80.0, "train_direction_pct": 80.0, "avg_live_phase_days": 30.0}
    assert ap.score_metrics(m, 5.0) == 80.0                       # ohne Obergrenze: altes Verhalten
    assert ap.score_metrics(m, 5.0, 15.0) == 80.0 - ap.PHASE_PENALTY_MAX
    m["avg_live_phase_days"] = 10.0
    assert ap.score_metrics(m, 5.0, 15.0) == 80.0


def test_score_mixes_reference():
    m = {"inner_direction_pct": 98.0, "train_direction_pct": 98.0,
         "inner_reference_pct": 60.0, "train_reference_pct": 60.0, "avg_live_phase_days": 10.0}
    assert ap.score_metrics(m, 5.0, 15.0) == 79.0


def test_robustness_band_prefers_sweet_spot_not_longest():
    near = {"holdout_direction_pct": 70.0, "avg_live_phase_days": 12.0, "switches_live": 30}
    long_ = {"holdout_direction_pct": 70.0, "avg_live_phase_days": 40.0, "switches_live": 5}
    assert ap.robustness_key(long_) > ap.robustness_key(near)              # alt: länger gewinnt
    assert ap.robustness_key(near, (5.0, 15.0)) > ap.robustness_key(long_, (5.0, 15.0))


def test_collect_reference_train_share():
    ragg = {"reference_pct": [], "inner_reference_pct": [], "holdout_reference_pct": [],
            "train_reference_pct": [], "reference_lag_days": []}
    ap._collect_reference({"direction_pct": 70.0, "holdout_direction_pct": 50.0, "bars": 1000,
                           "holdout_bars": 250, "mean_lag_days": 1.5}, ragg)
    assert round(ragg["train_reference_pct"][0], 2) == 76.67 and ragg["reference_lag_days"] == [1.5]


# ---------------- Fortschritt + Speicher-Reihenfolge ----------------
def test_progress_keeps_one_decimal():
    jid = "t_dec_prog"
    rlab.JOBS[jid] = {"id": jid, "status": "running", "progress": 0, "phase": "x"}
    local_exec.LOCAL_JOBS[jid] = {"kind": "regime_lab", "last_update": 0}
    try:
        asyncio.run(local_exec.apply_progress(jid, {"progress": 67.46}, None))
        assert rlab.JOBS[jid]["progress"] == 67.5
    finally:
        rlab.JOBS.pop(jid, None)
        local_exec.LOCAL_JOBS.pop(jid, None)


def test_local_regime_result_done_only_after_persist(monkeypatch):
    jid = "t_persist_order"
    rlab.JOBS[jid] = {"id": jid, "status": "running", "progress": 90, "phase": "x", "params": {}}
    local_exec.LOCAL_JOBS[jid] = {"kind": "regime_lab"}
    seen = {}

    async def fake_persist(db, job_id, job):
        seen["status_during_persist"] = job["status"]

    monkeypatch.setattr(rlab, "persist_worker_result", fake_persist)
    monkeypatch.setattr(local_exec, "_delete_meta_bg", lambda _jid: None)
    try:
        asyncio.run(local_exec.apply_result(jid, {"status": "done", "result": {"kind": "analysis"}}, object()))
        assert seen["status_during_persist"] == "running"
        assert rlab.JOBS[jid]["status"] == "done" and "finalizing" not in rlab.JOBS[jid]
        # Doppel-Upload wird weiterhin ignoriert
        seen.clear()
        asyncio.run(local_exec.apply_result(jid, {"status": "done", "result": {"kind": "analysis"}}, object()))
        assert seen == {}
    finally:
        rlab.JOBS.pop(jid, None)
        local_exec.LOCAL_JOBS.pop(jid, None)
