"""Iteration 31 review tests: optimizer/backtest status DB-fallback + local_jobs
restore after server restart, auth, localworker status, core regression."""
import os
from datetime import datetime, timezone

import pytest
import requests
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
_benv = dotenv_values("/app/backend/.env")
MONGO_URL = _benv.get("MONGO_URL")
DB_NAME = _benv.get("DB_NAME")
TIMEOUT = 120
RESTORE_ID = "tst_restore_x1"


@pytest.fixture(scope="session")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(client):
    r = client.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                    json={"username": "Admin", "password": "Dean06Greif!/Admin"})
    if r.status_code != 200:
        pytest.fail(f"Admin login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token")
    assert isinstance(tok, str) and len(tok) > 10
    return tok


# --- Auth ---------------------------------------------------------------
class TestAuth:
    def test_login_and_verify(self, client, admin_token):
        r = client.get(f"{BASE_URL}/api/auth/verify", timeout=TIMEOUT,
                       headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("valid") is True

    def test_login_bad_password(self, client):
        r = client.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                        json={"username": "Admin", "password": "wrong"})
        assert r.status_code in (401, 403), r.status_code


# --- Optimizer status DB-fallback --------------------------------------
class TestOptimizerStatusFallback:
    def test_old_finished_run_returns_done(self, client):
        r = client.get(f"{BASE_URL}/api/optimizer/results?limit=1", timeout=TIMEOUT)
        assert r.status_code == 200
        results = r.json().get("results") or []
        if not results:
            pytest.skip("no optimizer runs in DB")
        job_id = results[0]["id"]
        s = client.get(f"{BASE_URL}/api/optimizer/status/{job_id}", timeout=TIMEOUT)
        assert s.status_code == 200, s.text[:300]
        d = s.json()
        assert d["status"] == "done"
        assert d["progress"] == 100
        assert d.get("result") is not None
        assert d.get("error") is None

    def test_unknown_job_returns_404(self, client):
        s = client.get(f"{BASE_URL}/api/optimizer/status/nonexistent", timeout=TIMEOUT)
        assert s.status_code == 404, s.text[:200]


# --- Backtest status DB-fallback ---------------------------------------
class TestBacktestStatusFallback:
    def test_old_finished_backtest(self, client):
        r = client.get(f"{BASE_URL}/api/backtest/results?limit=1", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        rows = body.get("results") if isinstance(body, dict) else body
        if not rows:
            pytest.skip("no backtests in DB")
        job_id = rows[0].get("id")
        if not job_id:
            pytest.skip("backtest row without id")
        s = client.get(f"{BASE_URL}/api/backtest/status/{job_id}", timeout=TIMEOUT)
        assert s.status_code == 200, s.text[:300]
        assert s.json()["status"] == "done"

    def test_unknown_backtest_404(self, client):
        s = client.get(f"{BASE_URL}/api/backtest/status/nope_xyz", timeout=TIMEOUT)
        assert s.status_code == 404


# --- Restart restore from db.local_jobs --------------------------------
class TestLocalJobRestore:
    @pytest.fixture(scope="class", autouse=True)
    def seed_local_job(self):
        from pymongo import MongoClient
        cli = MongoClient(MONGO_URL, serverSelectionTimeoutMS=20000)
        col = cli[DB_NAME].local_jobs
        now = datetime.now(timezone.utc).isoformat()
        col.delete_one({"_id": RESTORE_ID})
        col.insert_one({"_id": RESTORE_ID, "kind": "optimizer",
                        "params": {"mode": "params"}, "progress": 25,
                        "created_at": now, "updated_at": now})
        yield
        col.delete_one({"_id": RESTORE_ID})
        cli.close()

    def test_status_restores_running_job(self, client):
        s = client.get(f"{BASE_URL}/api/optimizer/status/{RESTORE_ID}", timeout=TIMEOUT)
        assert s.status_code == 200, s.text[:300]
        d = s.json()
        assert d["status"] == "running", d
        assert d["progress"] == 25, d
        assert d.get("execution") == "local", d

    def test_active_lists_restored_job(self, client):
        a = client.get(f"{BASE_URL}/api/optimizer/active", timeout=TIMEOUT)
        assert a.status_code == 200
        act = a.json().get("active")
        assert act is not None, "restored job not reported as active"
        assert act["id"] == RESTORE_ID, act

    def test_cleanup_reset(self, client, admin_token):
        r = client.post(f"{BASE_URL}/api/optimizer/reset", timeout=TIMEOUT,
                        headers={"Authorization": f"Bearer {admin_token}"})
        assert r.status_code == 200, r.text[:300]
        a = client.get(f"{BASE_URL}/api/optimizer/active", timeout=TIMEOUT)
        assert a.status_code == 200
        assert (a.json().get("active") or {}).get("id") != RESTORE_ID


# --- Local worker status ------------------------------------------------
class TestLocalWorker:
    def test_status_ok(self, client):
        r = client.get(f"{BASE_URL}/api/localworker/status", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert "online" in d
        assert d["online"] is False


# --- Core regression ----------------------------------------------------
class TestCoreRegression:
    def test_health(self, client):
        r = client.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        assert r.elapsed.total_seconds() < 10, (
            f"/api/health too slow: {r.elapsed.total_seconds():.1f}s")

    def test_strategies(self, client):
        r = client.get(f"{BASE_URL}/api/strategies", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        rows = body.get("strategies") if isinstance(body, dict) else body
        assert isinstance(rows, list) and len(rows) > 0

    def test_optimizer_results(self, client):
        r = client.get(f"{BASE_URL}/api/optimizer/results?limit=3", timeout=TIMEOUT)
        assert r.status_code == 200
        res = r.json()["results"]
        assert isinstance(res, list)
        for row in res:
            assert "_id" not in row
            assert row.get("id")

    def test_optimizer_history(self, client):
        r = client.get(f"{BASE_URL}/api/optimizer/history?limit=5", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        items = body.get("history") if isinstance(body, dict) else body
        assert isinstance(items, list)
        for row in items:
            assert "_id" not in row
