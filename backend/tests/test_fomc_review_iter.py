"""Iteration-Review-Tests: FOMC-Setup, key-credits, AI-status + Regression."""
import os
import time
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://ki-trader-refactor-2.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"No token in {r.json()}"
    return tok


@pytest.fixture(scope="session")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------- Regression: Health & bestehende Endpoints ----------
def test_health():
    r = requests.get(f"{BASE}/api/health", timeout=15)
    assert r.status_code == 200


def test_ai_playbook_has_fomc_setup():
    r = requests.get(f"{BASE}/api/ai/playbook", timeout=30)
    assert r.status_code == 200
    data = r.json()
    # setup lib kann verschiedene Formen haben – suche nach 'fomc_event' irgendwo
    txt = str(data).lower()
    assert "fomc_event" in txt, "fomc_event setup not found in /api/ai/playbook"


def test_ai_diagnosis():
    r = requests.get(f"{BASE}/api/ai/diagnosis", params={"days": 14}, timeout=30)
    assert r.status_code == 200


def test_ai_rewards():
    r = requests.get(f"{BASE}/api/ai/rewards", timeout=30)
    assert r.status_code == 200


def test_ai_insights():
    r = requests.get(f"{BASE}/api/ai/insights", timeout=30)
    assert r.status_code == 200


def test_postmortem():
    r = requests.get(f"{BASE}/api/ai/postmortem/summary", timeout=60)
    assert r.status_code == 200, f"postmortem/summary status {r.status_code}: {r.text[:200]}"


def test_ai_playbook_has_fomc_setup_retry():
    # slow endpoint - long timeout, one retry
    for i in range(2):
        try:
            r = requests.get(f"{BASE}/api/ai/playbook", timeout=90)
            assert r.status_code == 200
            assert "fomc_event" in str(r.json()).lower()
            return
        except requests.exceptions.ReadTimeout:
            if i == 1:
                raise
    pytest.fail("timeout")


def test_ai_diagnosis_retry():
    for i in range(2):
        try:
            r = requests.get(f"{BASE}/api/ai/diagnosis", params={"days": 14}, timeout=90)
            assert r.status_code == 200
            return
        except requests.exceptions.ReadTimeout:
            if i == 1:
                raise


# ---------- FOMC status ----------
def test_fomc_status_public():
    r = requests.get(f"{BASE}/api/fomc/status", timeout=20)
    assert r.status_code == 200
    j = r.json()
    assert "phase" in j
    assert j["phase"] in ("none", "pre", "lock", "post")
    assert "next_decision_utc" in j
    assert "live_enabled" in j
    assert "validation" in j
    assert "engine" in j
    if j["engine"]:
        assert "fomc_interval_min" in j["engine"]
        assert "provider" in j["engine"]
        assert "model" in j["engine"]


# ---------- AI status enthält fomc + key_credits ----------
def test_ai_status_has_fomc_and_key_credits():
    r = requests.get(f"{BASE}/api/ai/status", timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert "fomc" in j, f"'fomc' fehlt in ai/status: keys={list(j.keys())}"
    assert "phase" in j["fomc"]
    assert "next_decision_utc" in j["fomc"]
    assert "key_credits" in j, f"'key_credits' fehlt in ai/status"


# ---------- FOMC backtest auth ----------
def test_fomc_backtest_requires_admin():
    r = requests.post(f"{BASE}/api/fomc/backtest", json={"years": 2}, timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ---------- FOMC config auth ----------
def test_fomc_config_requires_admin():
    r = requests.post(f"{BASE}/api/fomc/config", json={"live_enabled": True}, timeout=15)
    assert r.status_code in (401, 403)


# ---------- FOMC key-credits (admin) ----------
def test_fomc_key_credits(auth_headers):
    r = requests.get(f"{BASE}/api/fomc/key-credits", headers=auth_headers, timeout=60)
    assert r.status_code == 200, r.text[:300]
    j = r.json()
    assert "keys" in j
    assert "worst_level" in j
    for k in j["keys"]:
        assert "label" in k
        assert "key_masked" in k
        assert "level" in k
        assert k["level"] in ("ok", "warning", "critical", "free", "unknown")


# ---------- FOMC config toggle ----------
def test_fomc_config_toggle_live(auth_headers):
    # Enable
    r = requests.post(f"{BASE}/api/fomc/config", headers=auth_headers,
                      json={"live_enabled": True}, timeout=15)
    assert r.status_code == 200
    assert r.json().get("live_enabled") is True
    # Verify via GET status
    r2 = requests.get(f"{BASE}/api/fomc/status", timeout=15)
    assert r2.json().get("live_enabled") is True
    # Reset to False
    r3 = requests.post(f"{BASE}/api/fomc/config", headers=auth_headers,
                       json={"live_enabled": False}, timeout=15)
    assert r3.status_code == 200
    assert r3.json().get("live_enabled") is False
    r4 = requests.get(f"{BASE}/api/fomc/status", timeout=15)
    assert r4.json().get("live_enabled") is False


# ---------- FOMC backtest (Start + Poll) ----------
def test_fomc_backtest_start_and_poll(auth_headers):
    r = requests.post(f"{BASE}/api/fomc/backtest", headers=auth_headers,
                      json={"years": 2}, timeout=30)
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") in ("started", "busy"), j

    # Poll bis fertig (max 3 min)
    result = None
    running = True
    deadline = time.time() + 200
    while time.time() < deadline:
        rr = requests.get(f"{BASE}/api/fomc/backtest", timeout=30)
        assert rr.status_code == 200
        jr = rr.json()
        running = jr.get("running", False)
        result = jr.get("result")
        if not running and result:
            break
        time.sleep(10)

    assert not running, "Backtest ist nach 200s noch running"
    assert result, "Kein result nach Backtest"
    assert "aggregate" in result, f"aggregate fehlt: keys={list(result.keys())}"
    agg = result["aggregate"]
    for k in ("total", "in_sample", "out_of_sample"):
        assert k in agg, f"aggregate.{k} fehlt"
        for sk in ("trades", "winrate", "pnl"):
            assert sk in agg[k], f"aggregate.{k}.{sk} fehlt"
    assert "per_event" in result
    assert "validated" in result
    assert isinstance(result["validated"], bool)
    assert "validation_reason" in result
    assert "params_fixed" in result
    assert "trades" in result
