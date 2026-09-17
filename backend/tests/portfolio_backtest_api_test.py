"""API tests for Portfolio-Backtest endpoints (Audit 3.5).
Runs against public preview URL from REACT_APP_BACKEND_URL.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---- auth ----
def test_portfolio_run_without_token_returns_401_403():
    r = requests.post(f"{BASE_URL}/api/portfolio-backtest/run",
                      json={"strategy_ids": ["rsi_only"], "symbols": ["BTCUSDT"]},
                      timeout=15)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# ---- status endpoint idle-safe ----
def test_status_no_id_no_500():
    r = requests.get(f"{BASE_URL}/api/portfolio-backtest/status", timeout=15)
    assert r.status_code == 200
    j = r.json()
    assert "status" in j


# ---- validation ----
def test_portfolio_run_empty_strategy_ids_returns_400(auth_headers):
    r = requests.post(f"{BASE_URL}/api/portfolio-backtest/run", headers=auth_headers,
                      json={"strategy_ids": [], "symbols": ["BTCUSDT"], "days": 3},
                      timeout=15)
    assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"


# ---- full run + poll ----
def test_portfolio_run_and_poll_until_done(auth_headers):
    body = {
        "strategy_ids": ["scalping_4_rules", "rsi_only"],
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "days": 7,
        "max_capital": 100,
        "portfolio": {"start_capital": 500, "max_open_trades": 3},
    }
    r = requests.post(f"{BASE_URL}/api/portfolio-backtest/run",
                      headers=auth_headers, json=body, timeout=30)
    assert r.status_code == 200, f"run failed: {r.status_code} {r.text}"
    j = r.json()
    assert j["status"] == "started"
    job_id = j["job_id"]
    assert job_id

    # Poll status
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        s = requests.get(f"{BASE_URL}/api/portfolio-backtest/status",
                         params={"job_id": job_id}, timeout=15)
        assert s.status_code == 200
        last = s.json()
        st = last.get("status")
        if st in ("done", "error", "cancelled"):
            break
        time.sleep(3)
    assert last and last.get("status") == "done", f"job did not finish: {last}"

    result = last.get("result") or {}
    # Required fields per review request
    port = result.get("portfolio")
    assert port is not None, "portfolio missing"
    for k in ("candidates", "trades", "pnl", "final_equity", "max_drawdown",
              "skipped", "max_concurrent", "equity_curve"):
        assert k in port, f"portfolio.{k} missing"
    skipped = port["skipped"]
    for k in ("max_positions", "kapital", "risikobudget", "cluster"):
        assert k in skipped, f"skipped.{k} missing"
    corr = result.get("correlation")
    assert corr is not None and "pairs" in corr and "avg_corr" in corr
    scen = result.get("scenarios")
    assert scen is not None and "slippage_stress" in scen
    # outage may be absent if no trades, but with 2 coins/2 strats/7 days should exist
    # allow missing outage if no trades
    if port["candidates"] > 0:
        assert "outage" in scen, "outage scenario missing"


# ---- regression: normal backtest run + status ----
def test_regression_normal_backtest_run_and_cancel(auth_headers):
    body = {
        "strategy_ids": ["rsi_only"],
        "symbols": ["BTCUSDT"],
        "days": 3,
        "max_capital": 100,
    }
    r = requests.post(f"{BASE_URL}/api/backtest/run",
                      headers=auth_headers, json=body, timeout=30)
    assert r.status_code == 200, f"normal backtest run failed: {r.status_code} {r.text}"
    j = r.json()
    assert j.get("status") == "started"
    job_id = j["job_id"]
    s = requests.get(f"{BASE_URL}/api/backtest/status/{job_id}", timeout=15)
    assert s.status_code == 200
    c = requests.post(f"{BASE_URL}/api/backtest/cancel/{job_id}",
                      headers=auth_headers, timeout=15)
    assert c.status_code == 200
