"""Regressionstests: Pause/Fortsetzen (services/job_control) und
Verbindungs-Resilienz des lokalen Workers (services/local_exec).
Reine Unit-Tests ohne Backend/Netzwerk."""
import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import job_control, local_exec  # noqa: E402
from services import optimizer as opt  # noqa: E402
from core.utils import _job_public  # noqa: E402


def _job(**over):
    j = {"id": "j1", "status": "running", "progress": 40, "phase": "Suche",
         "params": {}, "best": None, "cancel": False,
         "created_at": "2026-06-01T00:00:00+00:00", "result": None, "error": None}
    j.update(over)
    return j


def test_pause_requires_running_job():
    assert job_control.request_pause(_job(status="done")) is False
    assert job_control.request_pause(_job(cancel=True)) is False
    j = _job()
    assert job_control.request_pause(j) is True and j["pause"] is True
    assert job_control.request_resume(j) is True and j["pause"] is False


def test_wait_if_paused_blocks_task_until_resume():
    j = _job()
    job_control.request_pause(j)
    seen = {}

    async def worker():
        await job_control.wait_if_paused(j)
        seen["done"] = time.time()

    async def main():
        t0 = time.time()
        task = asyncio.create_task(worker())
        await asyncio.sleep(0.8)
        assert j["paused"] is True
        assert j["phase"].startswith(job_control.PAUSE_PREFIX)
        job_control.request_resume(j)
        await asyncio.wait_for(task, timeout=3)
        assert seen["done"] - t0 >= 0.7
        assert j["paused"] is False and j["phase"] == "Suche"
        assert j["paused_total_s"] >= 0.7

    asyncio.run(main())


def test_stop_check_blocks_only_in_worker_threads():
    j = _job()
    job_control.request_pause(j)
    should_stop = job_control.stop_check(j)

    # Auf einem Event-Loop-Thread darf NIE blockiert werden
    async def on_loop():
        t0 = time.time()
        assert should_stop() is False
        assert time.time() - t0 < 0.2
    asyncio.run(on_loop())
    assert j.get("paused") is not True

    # In einem Worker-Thread wartet die Callback bis zum Fortsetzen
    result = {}

    def in_thread():
        t0 = time.time()
        result["stop"] = should_stop()
        result["dt"] = time.time() - t0
    th = threading.Thread(target=in_thread)
    th.start()
    time.sleep(0.7)
    assert j["paused"] is True
    job_control.request_resume(j)
    th.join(timeout=3)
    assert result["stop"] is False and result["dt"] >= 0.6

    # Abbruch beendet eine Pause ebenfalls
    job_control.request_pause(j)
    j["cancel"] = True
    assert should_stop() is True


def test_job_public_exposes_pause_state_and_hides_internals():
    j = _job(paused=True, paused_at=time.time() - 10, phase_before_pause="x", _conn_lost=True)
    pub = _job_public(j)
    assert pub["paused"] is True and pub["paused_total_s"] >= 9
    assert "phase_before_pause" not in pub and "_conn_lost" not in pub
    assert "eta_seconds" not in pub  # keine Restzeit während der Pause


def test_apply_progress_relays_pause_and_remote_state():
    jid = "optpause1"
    opt.JOBS[jid] = _job(id=jid)
    local_exec.LOCAL_JOBS[jid] = {"kind": "optimizer", "state": "claimed",
                                  "enqueued_at": time.time(), "last_update": time.time(),
                                  "worker_id": "w1"}
    try:
        resp = asyncio.run(local_exec.apply_progress(jid, {"progress": 55, "paused": False}))
        assert resp["pause"] is False and opt.JOBS[jid]["progress"] == 55
        job_control.request_pause(opt.JOBS[jid])
        resp = asyncio.run(local_exec.apply_progress(jid, {"progress": 55, "paused": True}))
        assert resp["pause"] is True and opt.JOBS[jid]["paused"] is True
    finally:
        opt.JOBS.pop(jid, None)
        local_exec.LOCAL_JOBS.pop(jid, None)


def test_connection_loss_marks_error_but_revives_on_progress():
    jid = "optlost1"
    opt.JOBS[jid] = _job(id=jid)
    local_exec.LOCAL_JOBS[jid] = {"kind": "optimizer", "state": "claimed",
                                  "enqueued_at": time.time() - 99999,
                                  "last_update": time.time() - local_exec.OFFLINE_JOB_TIMEOUT - 5,
                                  "worker_id": "ghost"}
    try:
        local_exec.check_stale()
        j = opt.JOBS[jid]
        assert j["status"] == "error" and j.get("_conn_lost") is True
        assert local_exec.CONN_LOST_MSG in j["error"]
        # Worker meldet sich wieder -> Job läuft weiter (Meta wurde behalten)
        local_exec.LOCAL_JOBS[jid] = {"kind": "optimizer", "state": "claimed",
                                      "enqueued_at": time.time(), "last_update": time.time(),
                                      "worker_id": "ghost"}
        resp = asyncio.run(local_exec.apply_progress(jid, {"progress": 61, "phase": "weiter"}))
        assert resp["cancel"] is False
        assert j["status"] == "running" and j["error"] is None and "_conn_lost" not in j
        assert j["progress"] == 61
    finally:
        opt.JOBS.pop(jid, None)
        local_exec.LOCAL_JOBS.pop(jid, None)


def test_short_offline_only_sets_hint_not_error():
    jid = "optoff1"
    opt.JOBS[jid] = _job(id=jid)
    local_exec.LOCAL_JOBS[jid] = {"kind": "optimizer", "state": "claimed",
                                  "enqueued_at": time.time() - 600,
                                  "last_update": time.time() - 240,   # 4 min offline
                                  "worker_id": "ghost"}
    try:
        local_exec.check_stale()
        j = opt.JOBS[jid]
        assert j["status"] == "running"          # vorher: nach 180 s Fehler
        assert "unterbrochen" in j["phase"]
    finally:
        opt.JOBS.pop(jid, None)
        local_exec.LOCAL_JOBS.pop(jid, None)


def test_worker_version_requirement_bumped():
    assert local_exec.REQUIRED_WORKER_VERSION >= (1, 10, 0)


def test_job_public_endless_autopilot_progress_is_best_score():
    # Endlos-Suche (kein Limit): Balken = Bestwert-Score, auch wenn ein
    # (veralteter) Worker nur 95 gemeldet hat; keine ETA (Score != Restzeit).
    j = _job(kind="autopilot", progress=95,
             params={"max_minutes": 0, "max_rounds": 0},
             best={"score": 98.8})
    pub = _job_public(j)
    assert pub["progress"] == 99
    assert "eta_seconds" not in pub
    # Score 100: Balken voll, Suche läuft weiter
    j["best"] = {"score": 100.0}
    assert _job_public(j)["progress"] == 100


def test_job_public_limited_autopilot_keeps_reported_progress():
    j = _job(kind="autopilot", progress=42,
             params={"max_minutes": 30, "max_rounds": 0},
             best={"score": 98.8})
    pub = _job_public(j)
    assert pub["progress"] == 42 and "eta_seconds" in pub
