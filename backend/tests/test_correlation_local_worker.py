"""Asset-Korrelation auf dem lokalen Worker (fn="correlation", Worker >= 1.24.0)."""
import asyncio
import time

import pytest
from fastapi import HTTPException

from services import asset_correlation as ac
from services import local_exec
from services import regime_lab as lab
from tests.test_asset_correlation import _FakeDb, _candles, _wave


def _fake_fetch_factory():
    data = {"AUSDT": _candles(1500, 1, _wave), "BUSDT": _candles(1500, 2, _wave)}

    async def fake_fetch(symbols, days, tf, skipped=None, **kw):
        return {symbols[0]: data[symbols[0]]}
    return fake_fetch


def test_worker_run_returns_doc_and_server_persists(monkeypatch):
    monkeypatch.setattr(lab, "fetch_histories", _fake_fetch_factory())
    jid = lab.create_job(ac.JOB_KIND, {})
    asyncio.run(ac.run(jid, {"symbols": ["AUSDT", "BUSDT"], "timeframe": "1h", "days": 60}, None))
    job = lab.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    doc = job["result"]["doc"]
    assert doc["execution"] == "local" and doc["symbols"] == ["AUSDT", "BUSDT"]
    db = _FakeDb()
    asyncio.run(lab.persist_worker_result(db, jid, job))
    assert db.regime_correlation.doc["_id"] == doc["_id"]
    assert "doc" not in job["result"]          # schlankes Job-Ergebnis im Server-RAM
    assert asyncio.run(ac.latest(db))["execution"] == "local"


def test_cloud_run_marks_cloud_and_keeps_result_small(monkeypatch):
    monkeypatch.setattr(lab, "fetch_histories", _fake_fetch_factory())
    db = _FakeDb()
    jid = lab.create_job(ac.JOB_KIND, {})
    asyncio.run(ac.run(jid, {"symbols": ["AUSDT", "BUSDT"], "timeframe": "1h", "days": 60}, db))
    assert "doc" not in lab.JOBS[jid]["result"]
    assert db.regime_correlation.doc["execution"] == "cloud"


def test_fn_min_version_gate():
    assert local_exec.FN_MIN_VERSION["correlation"] == (1, 24)


def _clear_running():
    for j in lab.JOBS.values():
        if j.get("status") == "running":
            j["status"] = "done"


def test_router_local_requires_worker(monkeypatch):
    from routers import regime_lab as rl
    _clear_running()
    monkeypatch.setattr(local_exec, "WORKERS", {})
    with pytest.raises(HTTPException) as e:
        asyncio.run(rl.start_correlation({"symbols": ["AUSDT", "BUSDT"], "execution": "local"}, True))
    assert e.value.status_code == 503


def test_router_local_old_worker_is_rejected(monkeypatch):
    from routers import regime_lab as rl
    _clear_running()
    monkeypatch.setattr(local_exec, "WORKERS", {"w1": {"last_seen": time.time(), "version": "1.23.0"}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(rl.start_correlation({"symbols": ["AUSDT", "BUSDT"], "execution": "local"}, True))
    assert e.value.status_code == 409 and "1.24.0" in e.value.detail


def test_router_local_enqueues_for_worker(monkeypatch):
    from routers import regime_lab as rl
    _clear_running()
    monkeypatch.setattr(local_exec, "WORKERS", {"w1": {"last_seen": time.time(), "version": "1.24.0"}})
    queued = []
    monkeypatch.setattr(local_exec, "enqueue_compute",
                        lambda kind, job_id, payload, worker_id=None: queued.append((kind, job_id, payload)))
    res = asyncio.run(rl.start_correlation(
        {"symbols": ["AUSDT", "BUSDT"], "execution": "local", "worker_id": "w1", "timeframe": "4h"}, True))
    try:
        assert res["execution"] == "local"
        kind, job_id, payload = queued[0]
        assert kind == "regime_lab" and job_id == res["job_id"]
        args = payload["args"]
        assert args["fn"] == "correlation" and args["body"]["worker_id"] == "w1"
        assert args["body"]["timeframe"] == "4h"
        assert lab.JOBS[job_id]["params"]["execution"] == "local"
    finally:
        lab.JOBS[res["job_id"]]["status"] = "done"
