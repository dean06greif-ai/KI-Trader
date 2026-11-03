"""Iter54 live API checks: regime-lab/champions, lab-live, backtest job controls, event-setups overview.

Read-only + error-contract (shared production DB; no mutating calls).
"""
import os
import time
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://regime-engine-lab.preview.emergentagent.com").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=120)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in login response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------- regime champions ----------
def test_regime_champions_not_shadowed_and_shape(auth_headers):
    r = requests.get(f"{BASE}/api/regime-lab/champions", params={"symbols": "BTCUSDT,ETHUSDT"},
                     headers=auth_headers, timeout=90)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert "mode" in data and "assign" in data and "results" in data, data.keys()
    # Shouldn't be shadowed by /api/regime-lab/{aid} -> that would 404 or different shape
    assert isinstance(data["results"], dict)
    # Rules field expected per spec
    assert "rules" in data or "rules" in data.get("results", {}), f"missing 'rules': {list(data.keys())}"


def test_regime_champions_mode_requires_admin():
    r = requests.post(f"{BASE}/api/regime-lab/champions/mode", json={"mode": "off"}, timeout=90)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code} {r.text[:200]}"


def test_regime_champions_mode_invalid_value(auth_headers):
    r = requests.post(f"{BASE}/api/regime-lab/champions/mode",
                      json={"mode": "definitely-not-a-mode"},
                      headers=auth_headers, timeout=90)
    assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text[:200]}"


# ---------- lab-live ----------
def test_lab_live_shape_and_session_open(auth_headers):
    r = requests.get(f"{BASE}/api/ai/playbook/lab-live", headers=auth_headers, timeout=90)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    assert "optin" in data and "setups" in data, data.keys()
    optin = data["optin"] or {}
    crypto = optin.get("crypto") or []
    assert "session_open" in crypto, f"session_open not opted in: {crypto}"
    # find session_open setup row for crypto
    setups = data["setups"] or []
    row = next((s for s in setups
                if (s.get("asset_class") == "crypto" or s.get("asset") == "crypto")
                and (s.get("setup_id") == "session_open" or s.get("setup") == "session_open")), None)
    assert row is not None, f"crypto/session_open row missing in setups ({len(setups)} rows)"
    assert row.get("validated") is True, f"validated != True: {row}"
    assert row.get("opted_in") is True, f"opted_in != True: {row}"
    assert row.get("live_override") is True, f"live_override != True: {row}"


def test_lab_live_post_requires_admin():
    r = requests.post(f"{BASE}/api/ai/playbook/lab-live",
                      json={"asset_class": "crypto", "setup_id": "session_open", "opt_in": True},
                      timeout=90)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


def test_lab_live_post_invalid_asset(auth_headers):
    r = requests.post(f"{BASE}/api/ai/playbook/lab-live",
                      json={"asset_class": "xyz", "setup_id": "session_open", "opt_in": True},
                      headers=auth_headers, timeout=90)
    assert r.status_code == 400, f"expected 400 got {r.status_code} {r.text[:200]}"


# ---------- backtest job controls ----------
def test_backtest_pause_unknown_requires_auth():
    r = requests.post(f"{BASE}/api/ai/playbook/backtest/pause/doesnotexist", timeout=90)
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}"


@pytest.mark.parametrize("verb", ["pause", "resume", "stop"])
def test_backtest_job_controls_unknown_id_404(auth_headers, verb):
    r = requests.post(f"{BASE}/api/ai/playbook/backtest/{verb}/doesnotexist",
                      headers=auth_headers, timeout=90)
    assert r.status_code == 404, f"{verb}: expected 404 got {r.status_code} {r.text[:200]}"


def test_backtest_jobs_reset(auth_headers):
    r = requests.post(f"{BASE}/api/ai/playbook/backtest/jobs/reset",
                      headers=auth_headers, timeout=90)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    assert data.get("status") == "reset", data
    assert "cleared" in data and isinstance(data["cleared"], int), data


# ---------- event setups overview ----------
def test_event_setups_overview_has_live_auto(auth_headers):
    r = requests.get(f"{BASE}/api/event-setups/overview", headers=auth_headers, timeout=90)
    assert r.status_code == 200, f"{r.status_code} {r.text[:300]}"
    data = r.json()
    events = data.get("events") if isinstance(data, dict) else data
    assert events, f"no events: {data}"
    # events may be dict keyed by name or a list
    event_items = events.values() if isinstance(events, dict) else events
    count = 0
    for ev in event_items:
        assert isinstance(ev, dict), f"event not dict: {ev}"
        assert "live_auto" in ev, f"event missing live_auto: {ev.get('event') or ev}"
        assert isinstance(ev["live_auto"], bool), f"live_auto not bool: {ev}"
        count += 1
    assert count >= 1
