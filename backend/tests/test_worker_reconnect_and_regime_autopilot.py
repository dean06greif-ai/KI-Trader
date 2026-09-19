"""Regressionstests (unit, ohne Backend): Worker-Reconnect-Anzeige, Heartbeat als
Lebenszeichen, Autopilot-Suchraum/Score, Serien-Kinds für Regime-Lab-Werkzeuge."""
import asyncio
import random
import time

import pytest

from services import job_series, local_exec, regime_autopilot as ap
from services import optimizer as opt


@pytest.fixture(autouse=True)
def _clean_state():
    local_exec.LOCAL_JOBS.clear()
    local_exec.WORKERS.clear()
    local_exec.COMPUTE_QUEUE.clear()
    for k in [k for k in opt.JOBS if k.startswith("t_")]:
        opt.JOBS.pop(k, None)
    yield
    local_exec.LOCAL_JOBS.clear()
    local_exec.WORKERS.clear()


def _claimed_job(jid="t_job1", worker="w1"):
    opt.JOBS[jid] = {"id": jid, "status": "running", "progress": 40,
                     "phase": "Endlos-Suche · 120 Kombis", "params": {}, "cancel": False,
                     "created_at": "2026-06-01T00:00:00+00:00", "result": None, "error": None}
    local_exec.WORKERS[worker] = {"last_seen": time.time(), "version": "1.12.0"}
    local_exec.LOCAL_JOBS[jid] = {"kind": "optimizer", "state": "claimed", "worker_id": worker,
                                  "enqueued_at": time.time(), "last_update": time.time()}
    return opt.JOBS[jid], local_exec.LOCAL_JOBS[jid]


# ---------------- Offline-Hinweis verschwindet nach Reconnect ----------------
def test_offline_hint_cleared_by_heartbeat_running_jobs():
    job, meta = _claimed_job()
    local_exec.WORKERS["w1"]["last_seen"] = time.time() - 60
    meta["last_update"] = time.time() - 60
    local_exec.check_stale()
    assert meta.get("offline_hint") is True
    assert job["phase"] == local_exec.OFFLINE_HINT
    # Worker meldet sich per Heartbeat zurück und listet den Job als laufend
    local_exec.heartbeat("w1", {"running_jobs": ["t_job1"], "version": "1.12.0"})
    assert not meta.get("offline_hint")
    assert job["phase"] == "Endlos-Suche · 120 Kombis"


def test_offline_hint_cleared_by_check_stale_when_worker_online_again():
    job, meta = _claimed_job()
    local_exec.WORKERS["w1"]["last_seen"] = time.time() - 60
    meta["last_update"] = time.time() - 60
    local_exec.check_stale()
    assert job["phase"] == local_exec.OFFLINE_HINT
    local_exec.WORKERS["w1"]["last_seen"] = time.time()
    local_exec.check_stale()
    assert job["phase"] == "Endlos-Suche · 120 Kombis"
    assert "offline_hint" not in meta


def test_progress_without_phase_restores_previous_phase():
    job, meta = _claimed_job()
    local_exec.WORKERS["w1"]["last_seen"] = time.time() - 60
    meta["last_update"] = time.time() - 60
    local_exec.check_stale()
    assert job["phase"] == local_exec.OFFLINE_HINT
    resp = asyncio.run(local_exec.apply_progress("t_job1", {"progress": 55}, None))
    assert resp["cancel"] is False
    assert job["phase"] == "Endlos-Suche · 120 Kombis"
    assert job["progress"] == 55


def test_heartbeat_keeps_job_alive_without_progress():
    """Fortschritts-Meldungen bleiben aus, Heartbeat listet Job -> kein 6h-Fehler."""
    job, meta = _claimed_job()
    meta["last_update"] = time.time() - local_exec.OFFLINE_JOB_TIMEOUT - 10
    local_exec.heartbeat("w1", {"running_jobs": ["t_job1"]})
    local_exec.check_stale()
    assert job["status"] == "running"


def test_revive_sets_kind_so_job_is_found_again():
    job, _meta = _claimed_job()
    local_exec.LOCAL_JOBS.clear()
    job["status"] = "error"
    job["_conn_lost"] = True
    local_exec._revive_if_conn_lost("t_job1", job, "optimizer")
    assert job["status"] == "running"
    assert local_exec.LOCAL_JOBS["t_job1"]["kind"] == "optimizer"
    local_exec.check_stale()  # darf den Job nicht verwerfen
    assert "t_job1" in local_exec.LOCAL_JOBS


def test_required_version_and_autopilot_support():
    assert local_exec.REQUIRED_WORKER_VERSION_STR == "1.12.0"
    local_exec.WORKERS["old"] = {"last_seen": time.time(), "version": "1.11.0"}
    assert local_exec.worker_supports_autopilot() is False
    local_exec.WORKERS["new"] = {"last_seen": time.time(), "version": "1.12.0"}
    assert local_exec.worker_supports_autopilot() is True


def test_claim_skips_autopilot_for_old_worker():
    from services import regime_lab as lab
    jid = lab.create_job("autopilot", {})
    payload = {"kind": "regime_lab", "args": {"fn": "autopilot", "body": {}}}
    local_exec.enqueue_compute("regime_lab", jid, payload)
    local_exec.WORKERS["old"] = {"last_seen": time.time(), "version": "1.11.0"}
    assert local_exec.claim("old") is None
    assert len(local_exec.COMPUTE_QUEUE) == 1
    local_exec.WORKERS["new"] = {"last_seen": time.time(), "version": "1.12.0"}
    item = local_exec.claim("new")
    assert item and item["job_id"] == jid
    lab.JOBS.pop(jid, None)


# ---------------- Autopilot: Suchraum & Score ----------------
def test_mutate_stays_in_space_and_changes_something():
    rng = random.Random(7)
    base = {"detector": "kombi", "kombi_thr": 0.18}
    for _ in range(50):
        cand = ap.mutate(base, rng, search_detectors=False, stale_rounds=0)
        assert cand["detector"] == "kombi"
        assert ap.config_diff(base, cand)
        for k, v in cand.items():
            spec = ap.space_for("kombi").get(k)
            if spec is None or isinstance(spec, list):
                continue
            assert spec[0] - 1e-9 <= v <= spec[1] + 1e-9


def test_mutate_can_switch_detector():
    rng = random.Random(3)
    dets = {ap.mutate({"detector": "ema"}, rng, True, 0)["detector"] for _ in range(200)}
    assert dets >= {"ema", "reactive", "kombi"}


def test_score_penalizes_short_phases():
    good = {"inner_direction_pct": 70.0, "avg_live_phase_days": 5.0}
    flicker = {"inner_direction_pct": 70.0, "avg_live_phase_days": 0.5}
    assert ap.score_metrics(good, 3.0) == 70.0
    assert ap.score_metrics(flicker, 3.0) < 70.0
    assert ap.score_metrics(None, 3.0) is None
    assert ap.score_metrics({"direction_pct": 60.0}, 3.0) == 60.0


# ---------------- Serie: Regime-Lab-Werkzeuge einreihbar ----------------
def test_series_kinds_and_labels():
    for k in ("regime_calibration", "regime_kombi", "regime_ablation",
              "regime_ema_compare", "regime_autopilot"):
        assert k in job_series.KINDS
        assert k in job_series.REGIME_STARTERS
        lbl = job_series.default_label(k, {"symbols": ["BTCUSDT"], "timeframe": "15m", "days": 360})
        assert job_series.REGIME_LABELS[k] in lbl
    from routers import regime_lab as rl
    for fn in job_series.REGIME_STARTERS.values():
        assert callable(getattr(rl, fn))


def test_series_summary_autopilot():
    s = job_series.summarize("regime_autopilot", {
        "best": {"detector": "kombi", "metrics": {"holdout_direction_pct": 66.6,
                                                   "inner_direction_pct": 70.1}},
        "improvements": 3, "tested": 40, "improved": True})
    assert s["holdout_pct"] == 66.6 and s["detector"] == "kombi" and s["tested"] == 40
