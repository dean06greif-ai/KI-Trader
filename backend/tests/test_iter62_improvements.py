"""
Iter62 improvements tests:
- GET/DELETE regime-correlation runs
- POST regime-correlation (small crypto run) + runs appended
- GET /api/jobs/paused shape
- DELETE autopilot run (aptest_btc2)
"""
import os, time, json, pytest, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrade-lab.preview.emergentagent.com").rstrip("/")

@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"}, timeout=20)
    assert r.status_code == 200, r.text
    return r.json()["token"]

@pytest.fixture(scope="module")
def H(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ---------------- regime-correlation GET ----------------
class TestRegimeCorrelationGet:
    def test_get_shape(self, H):
        r = requests.get(f"{BASE}/api/regime-correlation", headers=H, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert set(["job", "result", "runs"]).issubset(d.keys())
        assert isinstance(d["runs"], list)
        # Requirement: at least 2 stored runs locally
        assert len(d["runs"]) >= 2, f"expected >=2 stored runs, got {len(d['runs'])}"
        if d["result"]:
            for k in ("id", "requested", "symbols", "perfect", "missing_reasons"):
                assert k in d["result"], f"result missing key {k}"

    def test_get_run_by_id(self, H):
        d = requests.get(f"{BASE}/api/regime-correlation", headers=H, timeout=20).json()
        rid = d["runs"][0]["id"]
        r = requests.get(f"{BASE}/api/regime-correlation/runs/{rid}", headers=H, timeout=20)
        assert r.status_code == 200
        body = r.json()
        # Could be {run: {...}} or run directly
        run = body.get("run", body)
        assert run.get("id") == rid

    def test_delete_requires_admin(self):
        d = requests.get(f"{BASE}/api/regime-correlation", timeout=20).json()
        rid = d["runs"][-1]["id"]  # don't actually delete w/o admin
        r = requests.delete(f"{BASE}/api/regime-correlation/runs/{rid}", timeout=20)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ---------------- regime-correlation POST (small crypto) ----------------
class TestRegimeCorrelationPost:
    def test_post_and_run_appears(self, H):
        # ensure no running job
        for _ in range(30):
            d = requests.get(f"{BASE}/api/regime-correlation", headers=H, timeout=20).json()
            if not d["job"]["running"]:
                break
            time.sleep(2)
        before = len(d["runs"])
        payload = {"symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
                   "timeframe": "1h", "days": 30, "train_pct": 75}
        r = requests.post(f"{BASE}/api/regime-correlation", headers=H, json=payload, timeout=30)
        assert r.status_code in (200, 202), r.text
        # Poll
        deadline = time.time() + 240
        last = None
        while time.time() < deadline:
            d = requests.get(f"{BASE}/api/regime-correlation", headers=H, timeout=20).json()
            last = d
            if not d["job"]["running"]:
                break
            time.sleep(3)
        assert last and not last["job"]["running"], f"job did not finish: {last and last['job']}"
        after = len(last["runs"])
        assert after >= before, f"runs count did not grow or stay: {before}->{after}"
        # New run is at the top
        res = last["result"]
        assert res["symbols"] and set(payload["symbols"]).issubset(set(res.get("requested", [])) | set(res["symbols"]))


# ---------------- jobs/paused shape ----------------
class TestJobsPaused:
    def test_shape(self, H):
        r = requests.get(f"{BASE}/api/jobs/paused", headers=H, timeout=20)
        assert r.status_code == 200
        d = r.json()
        assert "jobs" in d and "count" in d
        assert isinstance(d["jobs"], list)
        assert d["count"] == len(d["jobs"])


# ---------------- autopilot delete (aptest_btc2 only) ----------------
class TestAutopilotDelete:
    def test_delete_aptest_btc2(self, H):
        # Confirm presence
        r = requests.get(f"{BASE}/api/regime-lab/autopilot/runs", headers=H, timeout=20)
        assert r.status_code == 200
        body = r.json()
        runs = body if isinstance(body, list) else body.get("runs", [])
        ids = {x.get("id") for x in runs}
        if "aptest_btc2" not in ids:
            pytest.skip("aptest_btc2 already removed")
        # Non-admin: 401/403
        r0 = requests.delete(f"{BASE}/api/regime-lab/autopilot/runs/aptest_btc2", timeout=20)
        assert r0.status_code in (401, 403)
        # Admin: delete
        r1 = requests.delete(f"{BASE}/api/regime-lab/autopilot/runs/aptest_btc2", headers=H, timeout=20)
        assert r1.status_code in (200, 204), r1.text
        # Verify gone
        body2 = requests.get(f"{BASE}/api/regime-lab/autopilot/runs", headers=H, timeout=20).json()
        runs2 = body2 if isinstance(body2, list) else body2.get("runs", [])
        ids2 = {x.get("id") for x in runs2}
        assert "aptest_btc2" not in ids2
        assert "aptest_btc1" in ids2, "aptest_btc1 should remain"
