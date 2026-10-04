import asyncio
import aiohttp
import pytest

from services import history_sources as hs
from services import local_exec


class _Resp:
    def __init__(self, status, body=b"x", headers=None):
        self.status, self._b, self.headers = status, body, headers or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def read(self):
        return self._b


class _Sess:
    def __init__(self, seq):
        self.seq, self.calls = list(seq), 0

    def get(self, *a, **k):
        self.calls += 1
        it = self.seq.pop(0)
        if isinstance(it, Exception):
            raise it
        return it


@pytest.fixture(autouse=True)
def _fast_sleep(monkeypatch):
    async def _s(_):
        return None
    monkeypatch.setattr(hs.asyncio, "sleep", _s)


def test_duka_day_retries_connector_error_and_503_then_succeeds():
    from datetime import datetime, timezone
    err = aiohttp.ClientConnectionError("ssl reset")
    s = _Sess([err, _Resp(503, headers={"Retry-After": "5"}), _Resp(200, b"OK")])
    out = asyncio.run(hs._duka_day(s, "XAUUSD", datetime(2025, 1, 2, tzinfo=timezone.utc)))
    assert out == b"OK" and s.calls == 3


def test_fetch_backup_cooldown_retries_day_before_giving_up(monkeypatch):
    calls = {"n": 0}

    async def fake_day(session, ref, day_dt, attempts=6):
        calls["n"] += 1
        return None if calls["n"] == 1 else b""
    monkeypatch.setattr(hs, "_duka_day", fake_day)
    end = 1735776000000
    asyncio.run(hs.fetch_backup(None, "GOLD", end - 2 * 86400000, end))
    assert calls["n"] >= 2  # erster Fehler -> Abkühlen + erneuter Versuch statt Abbruch


def test_ai_seed_replay_failure_marks_job_error(monkeypatch):
    from services.setup_backtest import runner, local_run
    jid = "test_ai_seed_fail"
    runner.JOBS[jid] = {"id": jid, "status": "running", "progress": 50}
    local_exec.LOCAL_JOBS[jid] = {"kind": "ai_seed", "state": "claimed"}

    async def boom(db, log):
        raise RuntimeError("mongo down")
    monkeypatch.setattr(local_run, "replay", boom)
    monkeypatch.setattr(local_exec, "_delete_meta_bg", lambda *_: None)
    asyncio.run(local_exec.apply_result(jid, {"kind": "ai_seed", "status": "done", "log": []}, object()))
    job = runner.JOBS[jid]
    assert job["status"] == "error" and "mongo down" in job["error"] and not job.get("finalizing")
