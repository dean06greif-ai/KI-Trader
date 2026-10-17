"""Iteration 44 API tests: Fine-tune autopilot + dynamic versions compare."""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
TIMEOUT = 60


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "PreviewAdmin123!"},
                      timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


# ---------------- Fine-tune autopilot ----------------
class TestFineTuneAutopilot:
    def _stop(self, headers, job_id):
        # Soft stop first (keeps best detection)
        try:
            requests.post(f"{BASE_URL}/api/regime-lab/autopilot/stop/{job_id}",
                          headers=headers, timeout=TIMEOUT)
        except Exception:
            pass
        # Hard cancel to free the slot quickly for the next test
        try:
            requests.post(f"{BASE_URL}/api/regime-lab/cancel/{job_id}",
                          headers=headers, timeout=TIMEOUT)
        except Exception:
            pass
        # Wait until no autopilot is active (max 60s)
        for _ in range(30):
            try:
                a = requests.get(f"{BASE_URL}/api/regime-lab/active",
                                 headers=headers, timeout=TIMEOUT)
                if a.status_code == 200 and not a.json().get("active"):
                    return
            except Exception:
                pass
            time.sleep(2)

    def test_fine_tune_start_contains_fine_block_and_caps(self, admin_headers):
        body = {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "4h",
            "days": 200,
            "execution": "cloud",
            "fine_tune": True,
            "max_minutes": 600,  # should cap to 30
        }
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                          json=body, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "started"
        job_id = data.get("job_id")
        assert job_id
        fine = data.get("fine")
        assert fine, f"expected fine block in response, got {data}"
        # Cap: 600 -> 30
        assert fine["max_minutes"] == 30.0
        assert fine["max_rounds"] == 400
        assert fine["plateau_rounds"] == 60
        assert "start" in fine and "source" in fine["start"]
        # stop immediately (soft stop)
        self._stop(admin_headers, job_id)

    def test_fine_tune_job_params_and_result(self, admin_headers):
        body = {
            "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "4h",
            "days": 200,
            "execution": "cloud",
            "fine_tune": True,
        }
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                          json=body, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        job_id = data["job_id"]
        assert data["fine"]["max_minutes"] == 15.0  # default

        # fetch active jobs and verify params.fine_mode
        try:
            # Give job a moment to be registered
            time.sleep(1)
            jr = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}",
                              headers=admin_headers, timeout=TIMEOUT)
            if jr.status_code == 200:
                job = jr.json()
                params = job.get("params") or job.get("job", {}).get("params") or {}
                # fine_mode should be true in params
                assert params.get("fine_mode") is True, f"params={params}"
                assert params.get("fine_start") is not None
        finally:
            # stop soft, then wait for finish to inspect result
            self._stop(admin_headers, job_id)

    def test_normal_autopilot_no_fine_block(self, admin_headers):
        # ensure no other autopilot runs; wait briefly for prior stop to drain
        time.sleep(2)
        body = {
            "symbols": ["BTCUSDT"],
            "timeframe": "4h",
            "days": 200,
            "execution": "cloud",
        }
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                          json=body, headers=admin_headers, timeout=TIMEOUT)
        # If a previous one is still running we may get 409; retry once after wait
        if r.status_code == 409:
            time.sleep(5)
            r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                              json=body, headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "started"
        assert "fine" not in data, f"did not expect fine block in normal autopilot: {data}"
        self._stop(admin_headers, data["job_id"])


# ---------------- Dynamic versions compare ----------------
class TestDynamicVersionsCompare:
    def test_compare_returns_phases_and_changed_count(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/dynamic/dyn_seed1/versions/compare?a=1&b=2",
                         headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "a" in data and "b" in data
        assert data["a"]["version"] == 1 and data["b"]["version"] == 2
        assert "phases" in data and isinstance(data["phases"], list) and len(data["phases"]) > 0
        assert "changed_count" in data and isinstance(data["changed_count"], int)
        for row in data["phases"]:
            assert "regime" in row and "label" in row
            assert "a" in row and "b" in row
            assert "changed" in row
            # metrics only present when side.is_optimized is True
            for side_key in ("a", "b"):
                side = row[side_key]
                if side.get("is_optimized"):
                    assert "metrics" in side  # may be None if not available, but key present
                else:
                    assert side.get("metrics") in (None, {}), (
                        f"side {side_key} is_optimized=False but has metrics: {side}")

    def test_compare_unknown_version_returns_404(self, admin_headers):
        r = requests.get(f"{BASE_URL}/api/dynamic/dyn_seed1/versions/compare?a=1&b=99999",
                         headers=admin_headers, timeout=TIMEOUT)
        assert r.status_code == 404, r.text
