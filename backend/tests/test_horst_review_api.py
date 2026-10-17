"""API regression tests for the new 'horst_vwap_obv' strategy + backtest E2E."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/") or "http://localhost:8001"
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_strategies_list_contains_horst_with_metadata():
    r = requests.get(f"{BASE_URL}/api/strategies", timeout=30)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    strategies = data if isinstance(data, list) else (
        data.get("strategies") or data.get("items") or []
    )
    active = data.get("active") if isinstance(data, dict) else None
    ids = [s["id"] for s in strategies]
    assert "horst_vwap_obv" in ids, f"missing horst_vwap_obv; got {ids}"
    horst = next(s for s in strategies if s["id"] == "horst_vwap_obv")
    assert horst.get("timeframe") == "1m"
    assert "name" in horst and "description" in horst
    params = horst.get("params") or {}
    for key in ["band_window", "band_mult", "obv_rsi_period",
                "obv_rsi_long_max", "obv_rsi_short_min",
                "min_dev_pct", "sl_pct", "tp_pct"]:
        assert key in params, f"missing param {key} in horst metadata"

    # Regression: legacy strategies still there
    for legacy in ["scalping_4_rules", "rsi_only", "vwap_reversion",
                   "pbd_model", "ai_trader"]:
        assert legacy in ids, f"legacy {legacy} missing"
    if active is not None:
        assert active in ids, f"active {active} not in ids"


def _run_backtest(headers, strategy_id, symbol="BTCUSDT", days=2, timeout=180):
    r = requests.post(f"{BASE_URL}/api/backtest/run",
                      headers=headers,
                      json={"strategy_ids": [strategy_id],
                            "symbols": [symbol], "days": days},
                      timeout=30)
    assert r.status_code == 200, f"start failed {r.status_code}: {r.text[:200]}"
    job_id = r.json().get("job_id") or r.json().get("id")
    assert job_id, f"no job_id: {r.json()}"
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        s = requests.get(f"{BASE_URL}/api/backtest/status/{job_id}",
                         headers=headers, timeout=30)
        assert s.status_code == 200, s.text[:200]
        last = s.json()
        st = last.get("status")
        if st in ("done", "completed", "success"):
            return last
        if st in ("error", "failed"):
            pytest.fail(f"backtest failed: {last}")
        time.sleep(3)
    pytest.fail(f"backtest {strategy_id} timeout; last={last}")


def test_backtest_horst_e2e(auth_headers):
    res = _run_backtest(auth_headers, "horst_vwap_obv")
    result = res.get("result") or res.get("results") or {}
    per_pair = result.get("per_pair") or result.get("perPair") or {}
    assert per_pair, f"no per_pair in result: {result}"
    first = list(per_pair.values())[0] if isinstance(per_pair, dict) else per_pair[0]
    assert ("trades" in first) or ("num_trades" in first), f"no trades key: {first}"
    assert ("win_rate" in first) or ("winRate" in first), f"no win_rate key: {first}"


def test_backtest_vwap_reversion_regression(auth_headers):
    res = _run_backtest(auth_headers, "vwap_reversion")
    result = res.get("result") or res.get("results") or {}
    per_pair = result.get("per_pair") or result.get("perPair") or {}
    assert per_pair, f"no per_pair for vwap_reversion: {result}"
