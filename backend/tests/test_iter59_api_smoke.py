"""API smoke tests for iteration 59 - Entry-Guard refactor.

READ-ONLY validation: health, auth, guarded endpoints, risk-budget round-trip,
trade-guard, autotrade read endpoints. NO orders opened/closed.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback for local pytest, read from frontend/.env
    try:
        with open("/app/frontend/.env") as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
    except Exception:
        pass

USER = "Admin"
PWD = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": USER, "password": PWD}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert "token" in data and isinstance(data["token"], str) and len(data["token"]) > 10
    return data["token"]


@pytest.fixture
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=60)
    assert r.status_code == 200
    assert r.json().get("status") == "alive"


def test_login_bad_password():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": USER, "password": "wrong-pw-xxx"}, timeout=30)
    assert r.status_code == 401


def test_autotrade_balance_requires_auth():
    r = requests.get(f"{BASE_URL}/api/autotrade/balance", timeout=30)
    assert r.status_code == 401


def test_autotrade_balance_with_auth(auth_headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/balance", headers=auth_headers, timeout=30)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)


def test_autotrade_capital_with_auth(auth_headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/capital", headers=auth_headers, timeout=30)
    assert r.status_code == 200


def test_autotrade_trades(auth_headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/trades", headers=auth_headers, timeout=60)
    assert r.status_code == 200
    body = r.json()
    # accept list or dict wrapper
    assert isinstance(body, (list, dict))


def test_autotrade_sync_status(auth_headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/sync-status", headers=auth_headers, timeout=30)
    assert r.status_code == 200


def test_autotrade_slippage_stats(auth_headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/slippage-stats?days=30",
                     headers=auth_headers, timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, dict)


def test_risk_budget_get(auth_headers):
    r = requests.get(f"{BASE_URL}/api/risk-budget", headers=auth_headers, timeout=30)
    assert r.status_code == 200
    body = r.json()
    assert "config" in body and "usage" in body
    cfg = body["config"]
    for k in ("enabled", "max_portfolio_risk_pct", "max_cluster_risk_pct"):
        assert k in cfg, f"missing config.{k}"
    usage = body["usage"]
    for k in ("mode", "open_risk_usdt", "by_cluster", "open_trades"):
        assert k in usage, f"missing usage.{k}"


def test_risk_budget_update_roundtrip(auth_headers):
    # capture current value
    cur = requests.get(f"{BASE_URL}/api/risk-budget",
                       headers=auth_headers, timeout=30).json()
    original = cur["config"].get("max_portfolio_risk_pct", 6)

    # set to 5
    r = requests.post(f"{BASE_URL}/api/risk-budget/config",
                      headers=auth_headers,
                      json={"max_portfolio_risk_pct": 5}, timeout=30)
    assert r.status_code == 200
    got = requests.get(f"{BASE_URL}/api/risk-budget",
                       headers=auth_headers, timeout=30).json()
    assert float(got["config"]["max_portfolio_risk_pct"]) == 5.0

    # restore to 6 (as requested by review)
    r2 = requests.post(f"{BASE_URL}/api/risk-budget/config",
                       headers=auth_headers,
                       json={"max_portfolio_risk_pct": 6}, timeout=30)
    assert r2.status_code == 200
    got2 = requests.get(f"{BASE_URL}/api/risk-budget",
                        headers=auth_headers, timeout=30).json()
    assert float(got2["config"]["max_portfolio_risk_pct"]) == 6.0
    _ = original


def test_trade_guard(auth_headers):
    r = requests.get(f"{BASE_URL}/api/trade-guard", headers=auth_headers, timeout=30)
    assert r.status_code == 200
    body = r.json()
    # tolerant schema: expect state or states or similar keys
    assert isinstance(body, dict)
    assert any(k in body for k in ("state", "states", "kill_switch", "enabled", "guard"))
