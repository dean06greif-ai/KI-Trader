"""Iteration 30: AI Diagnosis + Copilot Weekly Report backend tests."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', '').rstrip('/')
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---- AI Diagnosis ---------------------------------------------------------
class TestAIDiagnosis:
    @pytest.mark.parametrize("days", [7, 14, 30])
    def test_diagnosis_days(self, days):
        r = requests.get(f"{BASE_URL}/api/ai/diagnosis", params={"days": days}, timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        # required keys
        for k in ("findings", "live", "paper", "collection", "setups",
                  "guards", "data_quality", "slippage"):
            assert k in data, f"missing key '{k}' for days={days}"
        assert isinstance(data["findings"], list)
        for f in data["findings"]:
            assert "severity" in f
            # question or frage
            assert any(x in f for x in ("question", "frage"))
            assert "text" in f
        setups = data["setups"]
        assert isinstance(setups, list)
        # Expect up to 10 setups
        assert len(setups) <= 10
        if setups:
            s = setups[0]
            assert "live_ready" in s
            assert "live_reason" in s
        # data_quality symbols with fields
        dq = data["data_quality"]
        symbols = dq.get("symbols") if isinstance(dq, dict) else dq
        assert isinstance(symbols, list)
        if symbols:
            row = symbols[0]
            assert "last_candle_age_min" in row
            assert "orderflow_real" in row


# ---- Copilot Weekly -------------------------------------------------------
class TestCopilotWeekly:
    def test_get_weekly(self):
        r = requests.get(f"{BASE_URL}/api/copilot/weekly", timeout=15)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        for k in ("enabled", "weekday", "hour", "last_sent"):
            assert k in data, f"missing key {k}"

    def test_set_weekly_persistence(self, auth_headers):
        # Set to enabled=true, weekday=2, hour=18
        r = requests.post(f"{BASE_URL}/api/copilot/weekly",
                          json={"enabled": True, "weekday": 2, "hour": 18},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text[:300]
        # Verify persistence via GET
        r2 = requests.get(f"{BASE_URL}/api/copilot/weekly", timeout=15)
        d = r2.json()
        assert d["enabled"] is True
        assert d["weekday"] == 2
        assert d["hour"] == 18

        # Reset to default
        r3 = requests.post(f"{BASE_URL}/api/copilot/weekly",
                           json={"enabled": False, "weekday": 0, "hour": 9},
                           headers=auth_headers, timeout=15)
        assert r3.status_code == 200
        r4 = requests.get(f"{BASE_URL}/api/copilot/weekly", timeout=15)
        d2 = r4.json()
        assert d2["enabled"] is False
        assert d2["weekday"] == 0
        assert d2["hour"] == 9

    def test_weekly_send_once(self, auth_headers):
        """Real LLM call - only run ONCE. sent may be true or false; report must be non-empty."""
        r = requests.post(f"{BASE_URL}/api/copilot/weekly/send",
                          headers=auth_headers, timeout=120)
        assert r.status_code == 200, r.text[:500]
        data = r.json()
        assert "sent" in data
        assert "report" in data
        assert "sent_at" in data
        assert isinstance(data["report"], str)
        assert len(data["report"].strip()) > 20, f"report too short: {data['report'][:100]}"


# ---- Regression -----------------------------------------------------------
class TestRegression:
    def test_copilot_status(self):
        r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert d.get("ready") is True
        assert d.get("key_source") == "shared"

    def test_regime_phase(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/regime_phase/BTCUSDT", timeout=15)
        assert r.status_code == 200
        d = r.json()
        assert "phase" in d
        assert "label" in d

    def test_ai_status(self):
        r = requests.get(f"{BASE_URL}/api/ai/status", timeout=15)
        assert r.status_code == 200
