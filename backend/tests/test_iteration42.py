"""Tests for iteration 42: loop-health endpoint, trade list projection, admin login."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrader-autopilot-1.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "LocalTest123!"}, timeout=15)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:120]}")
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in {r.json()}"
    return tok


# --- Loop watchdog endpoint ---
def test_loop_health_structure():
    r = requests.get(f"{BASE_URL}/api/system/loop-health", timeout=10)
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ("enabled", "threshold_s", "current_lag_s", "max_lag_s", "blocks", "recent"):
        assert key in data, f"missing key {key}: {data}"
    assert data["enabled"] is True
    assert isinstance(data["recent"], list)
    assert isinstance(data["blocks"], int)


# --- Trade list projection (full flag) ---
INTERNAL_FIELDS = {"entry_market_snapshot", "entry_checks", "paper_exec", "bitunix_response", "guard_shadow", "policy_version"}


def test_trades_default_excludes_internal(admin_token):
    r = requests.get(f"{BASE_URL}/api/autotrade/trades?limit=200",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
    assert r.status_code == 200, r.text
    payload = r.json()
    trades = payload if isinstance(payload, list) else payload.get("trades") or payload.get("items") or []
    for t in trades:
        leaked = INTERNAL_FIELDS.intersection(t.keys())
        assert not leaked, f"trade exposes internal fields {leaked}: trade_id={t.get('id')}"


def test_trades_full_true_may_include_internal(admin_token):
    r = requests.get(f"{BASE_URL}/api/autotrade/trades?limit=200&full=true",
                     headers={"Authorization": f"Bearer {admin_token}"}, timeout=20)
    assert r.status_code == 200, r.text
    # Just verifying the endpoint accepts full=true and returns 200.
    payload = r.json()
    assert payload is not None


# --- Login works (sanity for admin creds) ---
def test_admin_login_ok():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "LocalTest123!"}, timeout=15)
    assert r.status_code == 200, r.text
    assert r.json().get("token") or r.json().get("access_token")
