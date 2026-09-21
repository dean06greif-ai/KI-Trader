"""Regressionstests (unit, ohne Backend) für die 1.13.0-Änderungen:
- local_exec: Worker-Neustart erkennen -> Job automatisch neu einreihen bzw. mit
  klarer Meldung beenden (statt bis zu 6 h „hängen bei 10 %“)
- worker.py: absturzsichere Poll-Schleife, Ergebnis-Sicherung auf Platte
- regime_autopilot: Vollautomatik (Folge-Analyse) rein + mit FakeDB
"""
import asyncio
import re
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "analysis_regression"))
from _fakes import FakeDB  # noqa: E402

from services import local_exec, regime_autopilot as ap  # noqa: E402
from services import optimizer as opt  # noqa: E402

WORKER_PY = Path(__file__).resolve().parents[2] / "local_worker" / "worker.py"


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
    local_exec.COMPUTE_QUEUE.clear()


def _claimed(jid="t_lost", worker="w1", with_item=True, age=60):
    opt.JOBS[jid] = {"id": jid, "status": "running", "progress": 10,
                     "phase": "Endlos-Suche", "params": {}, "cancel": False,
                     "created_at": "2026-06-01T00:00:00+00:00", "result": None, "error": None}
    local_exec.WORKERS[worker] = {"last_seen": time.time(), "version": "1.13.0"}
    meta = {"kind": "optimizer", "state": "claimed", "worker_id": worker,
            "enqueued_at": time.time() - age, "claimed_at": time.time() - age,
            "last_update": time.time()}
    if with_item:
        meta["item"] = {"job_id": jid, "kind": "optimizer", "fn": "optimizer", "payload": {"x": 1}}
    local_exec.LOCAL_JOBS[jid] = meta
    return opt.JOBS[jid], meta


def _hb(worker="w1", running=()):
    local_exec.heartbeat(worker, {"running_jobs": list(running), "version": "1.13.0"})


# ---------------- Worker-Neustart -> Job verloren ----------------
def test_lost_job_is_requeued_after_worker_restart():
    job, meta = _claimed()
    for _ in range(local_exec.LOST_JOB_POLLS - 1):
        _hb()
    assert meta["state"] == "claimed"  # noch in der Karenz (Poll-Zähler)
    _hb()
    assert meta["state"] == "queued" and meta["requeued"] is True
    assert local_exec.COMPUTE_QUEUE and local_exec.COMPUTE_QUEUE[0]["job_id"] == "t_lost"
    assert job["status"] == "running" and "neu gestartet" in job["phase"]
    # zweiter Verlust -> kein endloses Re-Queue, sondern Fehler
    local_exec.COMPUTE_QUEUE.clear()
    meta.update({"state": "claimed", "claimed_at": time.time() - 60})
    for _ in range(local_exec.LOST_JOB_POLLS):
        _hb()
    assert job["status"] == "error" and "neu gestartet" in job["error"]
    assert "t_lost" not in local_exec.LOCAL_JOBS


def test_lost_job_without_payload_errors_clearly():
    job, meta = _claimed(with_item=False)
    for _ in range(local_exec.LOST_JOB_POLLS):
        _hb()
    assert job["status"] == "error" and "Worker" in job["error"]


def test_running_job_is_not_treated_as_lost():
    job, meta = _claimed()
    for _ in range(5):
        _hb(running=["t_lost"])
    assert meta["state"] == "claimed" and job["status"] == "running"
    assert meta["missing_polls"] == 0


def test_fresh_claim_has_grace_period():
    job, meta = _claimed(age=2)  # gerade erst vergeben
    for _ in range(5):
        _hb()
    assert meta["state"] == "claimed"


def test_other_worker_heartbeat_does_not_touch_job():
    job, meta = _claimed(worker="w1")
    local_exec.WORKERS["w2"] = {"last_seen": time.time(), "version": "1.13.0"}
    for _ in range(5):
        _hb("w2")
    assert meta["state"] == "claimed"


def test_restored_job_without_owner_only_when_single_worker_online():
    job, meta = _claimed()
    meta["worker_id"] = None  # nach Server-Neustart wiederhergestellt
    local_exec.WORKERS["w2"] = {"last_seen": time.time(), "version": "1.13.0"}
    for _ in range(5):
        _hb("w1")
    assert meta["state"] == "claimed"  # zwei Worker online -> nicht eindeutig
    local_exec.WORKERS.pop("w2")
    for _ in range(local_exec.LOST_JOB_POLLS):
        _hb("w1")
    assert meta["state"] == "queued"


def test_claim_keeps_payload_for_requeue():
    opt.JOBS["t_c"] = {"id": "t_c", "status": "running", "progress": 0, "phase": "",
                       "params": {}, "cancel": False, "created_at": "", "result": None, "error": None}
    local_exec.LOCAL_JOBS["t_c"] = {"kind": "optimizer", "state": "queued", "enqueued_at": time.time()}
    local_exec.COMPUTE_QUEUE.append({"job_id": "t_c", "kind": "optimizer", "fn": "optimizer", "payload": {}})
    local_exec.WORKERS["w1"] = {"last_seen": time.time(), "version": "1.13.0"}
    item = local_exec.claim("w1", want_compute=True, want_data=False)
    assert item and item["job_id"] == "t_c"
    meta = local_exec.LOCAL_JOBS["t_c"]
    assert meta["item"] is item and meta["claimed_at"] > 0 and meta["missing_polls"] == 0


def test_required_version_bumped():
    assert local_exec.REQUIRED_WORKER_VERSION == (1, 13, 0)
    assert local_exec.REQUIRED_WORKER_VERSION_STR == "1.13.0"


# ---------------- worker.py (Quelltext-Verträge) ----------------
def test_worker_source_contracts():
    src = WORKER_PY.read_text(encoding="utf-8")
    assert 'VERSION = "1.13.0"' in src
    # absturzsichere Poll-Schleife: Catch-all nach RequestException/KeyboardInterrupt
    main_src = src[src.index("def main():"):]
    assert "except Exception as e:" in main_src and "Unerwarteter Fehler in der Poll-Schleife" in main_src
    # Ergebnis-Sicherung + Nachlieferung
    assert "def flush_pending_results" in src and "pending_results" in src
    assert "flush_pending_results()" in main_src  # nach Reconnect UND beim Start
    assert src.count("flush_pending_results()") >= 2
    # 6 h Upload-Toleranz (wie Server OFFLINE_JOB_TIMEOUT)
    m = re.search(r"RESULT_RETRY_MAX = (\d+)", src)
    assert m and int(m.group(1)) * 5 >= local_exec.OFFLINE_JOB_TIMEOUT
    # Bedienung unverändert: Poll-Payload-Felder + Auto-Verbindung bleiben
    for key in ("running_jobs", "want_compute", "want_data", "USER_CONFIG_PATH", "maybe_auto_update"):
        assert key in src


# ---------------- Regime-Autopilot Vollautomatik ----------------
def _result(improved=True):
    return {"improved": improved, "symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "15m",
            "days": 200, "train_pct": 70,
            "best_engine_config": {"detector": "ema", "confidence_min": 0.6, "min_phase_days": 2.0,
                                   "ema_fast": 12}}


def test_followup_body_pure():
    body = ap.followup_analysis_body(_result(), {"execution": "local"})
    assert body["symbols"] == ["BTCUSDT", "ETHUSDT"] and body["timeframe"] == "15m"
    assert body["days"] == 200 and body["train_pct"] == 70
    assert body["engine"] == "v2" and body["scope"] == "both"
    assert body["engine_config"]["ema_fast"] == 12 and body["engine_config"]["min_phase_days"] == 2.0
    assert body["confidence_min"] == 60.0 and body["min_hold_days"] == 2.0
    assert body["execution"] == "local" and body["name"].startswith("Autopilot · ema")


def test_followup_body_skips_when_not_improved_or_disabled():
    assert ap.followup_analysis_body(_result(improved=False), {}) is None
    assert ap.followup_analysis_body(_result(), {"auto_chain": False}) is None
    r = _result()
    r["best_engine_config"] = {}
    r.pop("best", None)
    assert ap.followup_analysis_body(r, {}) is None


def test_followup_body_clamps_values():
    r = _result()
    r["best_engine_config"].update({"confidence_min": 0.2, "min_phase_days": 0})
    body = ap.followup_analysis_body(r, {})
    assert body["confidence_min"] == 50.0 and body["min_hold_days"] == 0.25


def test_schedule_followup_enqueues_series_item(monkeypatch):
    from services import job_series
    added = []

    async def _add(db, kind, body, label=None):
        added.append((kind, body, label))
        return {"id": "item1", "kind": kind, "label": label}
    monkeypatch.setattr(job_series, "add_item", _add)
    db = FakeDB()
    asyncio.run(db.regime_lab_runs.insert_one({"id": "ap1", "result": _result()}))
    item = asyncio.run(ap.schedule_followup(db, "ap1", _result(), {"execution": "cloud"}))
    assert item["id"] == "item1"
    assert added[0][0] == "regime_analysis" and added[0][1]["autopilot_job_id"] == "ap1"
    run = asyncio.run(db.regime_lab_runs.find_one({"id": "ap1"}))
    assert run["result"]["followup"]["series_item_id"] == "item1"
    # ohne Verbesserung: nichts eingereiht
    assert asyncio.run(ap.schedule_followup(db, "ap2", _result(False), {})) is None
    assert len(added) == 1
    assert asyncio.run(ap.schedule_followup(None, "ap3", _result(), {})) is None


def test_autopilot_param_keys_include_auto_chain():
    from routers import regime_lab
    assert "auto_chain" in regime_lab.AUTOPILOT_PARAM_KEYS


def test_worker_result_path_chains_followup(monkeypatch):
    """persist_worker_result (kind=autopilot) ruft die Vollautomatik auf."""
    from services import regime_lab as rlab
    called = []

    async def _sched(db, job_id, res, params):
        called.append((job_id, params))
        return {"id": "it", "label": "L"}
    monkeypatch.setattr(ap, "schedule_followup", _sched)
    db = FakeDB()
    res = {**_result(), "kind": "autopilot", "created_at": "2026-06-01T00:00:00+00:00"}
    job = {"id": "apw", "kind": "autopilot", "result": res,
           "params": {"execution": "local", "auto_chain": True}}
    asyncio.run(rlab.persist_worker_result(db, "apw", job))
    assert called and called[0][0] == "apw" and called[0][1]["auto_chain"] is True
    assert res["followup"]["series_item_id"] == "it"
