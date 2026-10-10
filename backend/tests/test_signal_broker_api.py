"""API tests for the Signal Broker endpoints and admin config.

Tests GET /api/ai/signal-broker, POST /api/ai/config (admin), GET /api/ai/setup-trigger,
POST /api/ai/setup-trigger/run (admin). Uses REACT_APP_BACKEND_URL from frontend/.env.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback: read from frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break
    except Exception:
        pass

ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "")
if not ADMIN_PASS:
    pytest.skip("ADMIN_PASSWORD nicht gesetzt (E2E-Test gegen laufendes Backend)", allow_module_level=True)


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- signal-broker GET ----------
def test_signal_broker_shape():
    r = requests.get(f"{BASE_URL}/api/ai/signal-broker", timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ["enabled", "config", "windows", "open", "history",
                "outcomes", "stats", "labels", "sources", "calibration"]:
        assert key in data, f"missing key {key}; got keys {list(data.keys())}"
    cfg = data["config"]
    assert cfg.get("signal_min_ki_trades") == 5
    assert abs(float(cfg.get("signal_live_min_r", 0)) - 0.05) < 1e-6
    assert cfg.get("signal_rule_paper_all") is True
    assert cfg.get("signal_review_daily_cap") == 60
    wins = data["windows"]
    assert wins.get("divergence") == 45
    assert wins.get("momentum_news") == 10
    assert wins.get("htf_range") == 60


# ---------- POST /api/ai/config persist + clamp ----------
def test_signal_broker_config_persist_and_clamp(admin_headers):
    payload = {
        "signal_min_ki_trades": 8,
        "signal_review_daily_cap": 999,
        "signal_window_overrides": {"divergence": 90},
    }
    r = requests.post(f"{BASE_URL}/api/ai/config", json=payload,
                      headers=admin_headers, timeout=20)
    assert r.status_code == 200, r.text

    g = requests.get(f"{BASE_URL}/api/ai/signal-broker", timeout=20).json()
    assert g["config"]["signal_min_ki_trades"] == 8
    # cap must clamp to 300
    assert g["config"]["signal_review_daily_cap"] == 300, g["config"]
    assert g["windows"]["divergence"] == 90

    # restore
    restore = {
        "signal_min_ki_trades": 5,
        "signal_review_daily_cap": 60,
        "signal_window_overrides": {},
    }
    r2 = requests.post(f"{BASE_URL}/api/ai/config", json=restore,
                       headers=admin_headers, timeout=20)
    assert r2.status_code == 200, r2.text
    g2 = requests.get(f"{BASE_URL}/api/ai/signal-broker", timeout=20).json()
    assert g2["config"]["signal_min_ki_trades"] == 5
    assert g2["config"]["signal_review_daily_cap"] == 60
    assert g2["windows"]["divergence"] == 45


# ---------- disable broker flag ----------
def test_signal_broker_enabled_toggle(admin_headers):
    r = requests.post(f"{BASE_URL}/api/ai/config",
                      json={"signal_broker_enabled": False},
                      headers=admin_headers, timeout=20)
    assert r.status_code == 200, r.text
    st = requests.get(f"{BASE_URL}/api/ai/setup-trigger", timeout=20).json()
    assert st.get("broker") is False, st

    r2 = requests.post(f"{BASE_URL}/api/ai/config",
                       json={"signal_broker_enabled": True},
                       headers=admin_headers, timeout=20)
    assert r2.status_code == 200
    st2 = requests.get(f"{BASE_URL}/api/ai/setup-trigger", timeout=20).json()
    assert st2.get("broker") is True


# ---------- setup-trigger endpoints ----------
def test_setup_trigger_get():
    r = requests.get(f"{BASE_URL}/api/ai/setup-trigger", timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert "stats" in data
    assert "recent" in data
    assert "broker" in data


def test_setup_trigger_run(admin_headers):
    r = requests.post(f"{BASE_URL}/api/ai/setup-trigger/run",
                      headers=admin_headers, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "ok", data
    assert "hits" in data
    assert isinstance(data["hits"], list)
