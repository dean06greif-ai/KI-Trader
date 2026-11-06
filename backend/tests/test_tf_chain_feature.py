"""Testing agent iteration: TF-Chain feature (regime autopilot).

Tests the new Timeframe-Chain feature in Regime-Autopilot:
- GET /api/regime-lab/autopilot/tf-chain preview endpoint
- POST /api/regime-lab/autopilot with tf_chain=true -> result has tf_chain{...}
- POST without tf_chain -> no tf_chain keys in result (regression)
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "LocalTest123!")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(api):
    r = api.post(f"{BASE_URL}/api/auth/login",
                 json={"username": ADMIN_USER, "password": ADMIN_PASS})
    if r.status_code != 200:
        pytest.skip(f"login failed: {r.status_code} {r.text[:200]}")
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok, f"no token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ----- GET tf-chain preview endpoint -----

class TestTfChainPreview:
    def test_default_1h(self, api):
        r = api.get(f"{BASE_URL}/api/regime-lab/autopilot/tf-chain",
                    params={"timeframe": "1h"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["timeframe"] == "1h"
        # Expected chain per spec: [1h, 30m, 2h, 4h]
        assert d["chain"] == ["1h", "30m", "2h", "4h"], f"got {d['chain']}"
        assert d["switch_after_stale"] == 60
        assert d["cross_tf_margin"] == 1.0

    def test_15m(self, api):
        r = api.get(f"{BASE_URL}/api/regime-lab/autopilot/tf-chain",
                    params={"timeframe": "15m"})
        assert r.status_code == 200
        d = r.json()
        # 15m is the lowest in LADDER -> no step down; selected first + up to 2 steps up
        assert d["chain"][0] == "15m"
        assert "30m" in d["chain"]
        assert "1h" in d["chain"]


# ----- POST autopilot: with and without tf_chain -----

def _poll_job(api, headers, job_id, timeout=180):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        r = api.get(f"{BASE_URL}/api/regime-lab/status/{job_id}", headers=headers)
        if r.status_code != 200:
            time.sleep(2)
            continue
        last = r.json()
        if last.get("status") in ("done", "failed", "cancelled", "error"):
            return last
        time.sleep(3)
    return last


class TestAutopilotTfChain:
    def test_regression_no_tf_chain(self, api, auth_headers):
        body = {
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "days": 120,
            "max_rounds": 2,
            "engine_config": {"version": "v2", "detector": "ema"},
        }
        r = api.post(f"{BASE_URL}/api/regime-lab/autopilot",
                     json=body, headers=auth_headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        final = _poll_job(api, auth_headers, job_id, timeout=240)
        assert final and final.get("status") == "done", f"job did not finish: {final}"
        result = final.get("result") or {}
        assert "tf_chain" not in result, f"tf_chain unexpectedly present: {result.get('tf_chain')}"
        assert "selected_timeframe" not in result
        assert result.get("timeframe") == "1h"
        settings = result.get("settings") or {}
        assert settings.get("tf_chain") in (False, None)

    def test_with_tf_chain(self, api, auth_headers):
        body = {
            "symbols": ["BTCUSDT"],
            "timeframe": "1h",
            "days": 120,
            "max_rounds": 2,
            "tf_chain": True,
            "auto_chain": False,
            "engine_config": {"version": "v2", "detector": "ema"},
        }
        r = api.post(f"{BASE_URL}/api/regime-lab/autopilot",
                     json=body, headers=auth_headers)
        assert r.status_code == 200, r.text
        job_id = r.json()["job_id"]
        # chain of 4 TFs -> may need more time
        final = _poll_job(api, auth_headers, job_id, timeout=420)
        assert final and final.get("status") == "done", f"job did not finish: {final}"
        result = final.get("result") or {}
        tc = result.get("tf_chain")
        assert tc, f"tf_chain missing in result keys: {list(result.keys())}"
        assert tc["chain"] == ["1h", "30m", "2h", "4h"]
        assert "order" in tc
        assert "per_tf" in tc
        assert "best_timeframe" in tc
        assert result.get("selected_timeframe") == "1h"
        assert result.get("timeframe") == tc["best_timeframe"]
        settings = result.get("settings") or {}
        assert settings.get("tf_chain") is True
