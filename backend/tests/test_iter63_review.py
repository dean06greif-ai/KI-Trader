"""
Iteration 63 review tests:
- regime-correlation local vs cloud execution
- ai/diagnosis edge block
- storage retention endpoint still works
"""
import os
import time
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://ai-daytrade-pro.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"username": "Admin", "password": "Dean06Greif!/Admin"}, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _wait_done(hdr, timeout=90):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = requests.get(f"{BASE}/api/regime-correlation", headers=hdr, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        last = data
        job = data.get("job") or {}
        if job.get("status") == "done" and not job.get("running"):
            return data
        if job.get("status") == "error":
            pytest.fail(f"job errored: {job}")
        time.sleep(2)
    pytest.fail(f"timeout waiting for job done: {last}")


def _ensure_idle(hdr):
    # wait for any running job to finish before starting a new one
    deadline = time.time() + 90
    while time.time() < deadline:
        r = requests.get(f"{BASE}/api/regime-correlation", headers=hdr, timeout=15)
        job = (r.json() or {}).get("job") or {}
        if not job.get("running"):
            return
        time.sleep(2)
    pytest.fail("existing job never finished")


class TestRegimeCorrelation:
    def test_local_worker_connected(self, hdr):
        r = requests.get(f"{BASE}/api/localworker/status", headers=hdr, timeout=15)
        assert r.status_code == 200
        data = r.json()
        assert data.get("online") is True
        workers = data.get("workers") or []
        names = [w.get("name") for w in workers]
        assert "testpc" in names, f"testpc not found; got {names}"
        tp = next(w for w in workers if w.get("name") == "testpc")
        assert tp.get("version") == "1.24.0"
        assert tp.get("online") is True

    def test_fewer_than_two_symbols_400(self, hdr):
        _ensure_idle(hdr)
        r = requests.post(f"{BASE}/api/regime-correlation", headers=hdr,
                          json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 30, "execution": "local"},
                          timeout=15)
        assert r.status_code == 400, r.text

    def test_local_run_and_result(self, hdr):
        _ensure_idle(hdr)
        r = requests.post(f"{BASE}/api/regime-correlation", headers=hdr,
                          json={"symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "days": 30, "execution": "local"},
                          timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("status") == "started"
        assert body.get("execution") == "local"

        # 409 while running
        r2 = requests.post(f"{BASE}/api/regime-correlation", headers=hdr,
                           json={"symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "days": 30, "execution": "local"},
                           timeout=15)
        assert r2.status_code == 409, f"expected 409 while running, got {r2.status_code}: {r2.text}"

        data = _wait_done(hdr, timeout=120)
        job = data["job"]
        assert job.get("execution") == "local"
        result = data.get("result") or {}
        assert result.get("execution") == "local", f"result.execution={result.get('execution')}"

    def test_cloud_run_default(self, hdr):
        _ensure_idle(hdr)
        r = requests.post(f"{BASE}/api/regime-correlation", headers=hdr,
                          json={"symbols": ["BTCUSDT", "ETHUSDT"], "timeframe": "1h", "days": 30},
                          timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("status") == "started"
        # cloud is default
        assert body.get("execution") in (None, "cloud")
        data = _wait_done(hdr, timeout=180)
        result = data.get("result") or {}
        assert result.get("execution") == "cloud", f"result.execution={result.get('execution')}"


class TestAIDiagnosisEdge:
    def test_edge_block_present(self, hdr):
        r = requests.get(f"{BASE}/api/ai/diagnosis?days=14", headers=hdr, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "edge" in data, f"keys={list(data.keys())}"
        edge = data["edge"]
        overall = edge.get("overall") or {}
        for k in ("exp_r", "gross_r", "fee_r", "tp1_rate"):
            assert k in overall, f"missing {k} in overall: {overall}"
        for sect in ("by_source", "by_confidence", "by_hold", "by_setup"):
            assert sect in edge, f"missing {sect}"
        findings = data.get("findings") or []
        assert findings and (findings[0].get("frage") == "edge"), f"first finding not edge: {findings[:1]}"


class TestStorage:
    def test_storage_endpoint(self, hdr):
        # Try a few candidate endpoints without destruction
        tried = []
        for path in ("/api/maintenance/storage", "/api/maintenance/retention"):
            r = requests.get(f"{BASE}{path}", headers=hdr, timeout=20)
            tried.append((path, r.status_code))
            if r.status_code == 200:
                # non-destructive: just verify JSON parses
                j = r.json()
                assert isinstance(j, dict)
                return
        pytest.fail(f"no storage endpoint returned 200: {tried}")
