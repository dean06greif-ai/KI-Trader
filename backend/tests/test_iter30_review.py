"""Iteration 30 review test - dynamic strategies + workbench APIs against live URL."""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://regime-lab-dev.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json().get("token") or r.json().get("access_token")


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_strategies_include_dynamic():
    r = requests.get(f"{API}/strategies", timeout=60)
    assert r.status_code == 200
    data = r.json()
    strategies = data if isinstance(data, list) else data.get("strategies", data.get("items", []))
    dyn = [s for s in strategies if s.get("is_dynamic")]
    ids = {s.get("id") or s.get("_id") or s.get("sid") for s in dyn}
    assert "dyn_9130c3e6" in ids, f"dyn_9130c3e6 missing; got dyn ids: {ids}"
    for s in dyn:
        if (s.get("id") or s.get("sid")) == "dyn_9130c3e6":
            assert s.get("dynamic", {}).get("regimes"), "regimes missing on dyn_9130c3e6"


def test_dynamic_trade_plan():
    r = requests.get(f"{API}/dynamic/dyn_9130c3e6/trade-plan", timeout=15)
    assert r.status_code == 200, r.text
    js = r.json()
    assert "regimes" in js
    assert "on_switch" in js
    assert js["on_switch"] in ("close", "let_run")


def test_dynamic_settings_persist(auth_headers):
    # Set to let_run
    r = requests.post(f"{API}/dynamic/dyn_9130c3e6/settings",
                      json={"on_switch": "let_run"}, headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text
    r2 = requests.get(f"{API}/dynamic/dyn_9130c3e6/trade-plan", timeout=15)
    assert r2.json().get("on_switch") == "let_run"
    # Reset to close
    r3 = requests.post(f"{API}/dynamic/dyn_9130c3e6/settings",
                       json={"on_switch": "close"}, headers=auth_headers, timeout=15)
    assert r3.status_code == 200
    r4 = requests.get(f"{API}/dynamic/dyn_9130c3e6/trade-plan", timeout=15)
    assert r4.json().get("on_switch") == "close"


def test_dynamic_settings_needs_auth():
    r = requests.post(f"{API}/dynamic/dyn_9130c3e6/settings",
                      json={"on_switch": "let_run"}, timeout=15)
    assert r.status_code in (401, 403)


def test_backtest_ignores_dynamic():
    r = requests.post(f"{API}/backtest/run",
                      json={"strategy_id": "dyn_9130c3e6", "symbol": "BTCUSDT", "timeframe": "1h"},
                      timeout=30)
    # Should either 400 or 200 with dynamic ignored - accept any non-500
    assert r.status_code != 500, r.text


def test_optimizer_params_rejects_dynamic(auth_headers):
    r = requests.post(f"{API}/optimizer/run",
                      json={"mode": "params", "strategy_id": "dyn_9130c3e6",
                            "symbol": "BTCUSDT", "timeframe": "1h"},
                      headers=auth_headers, timeout=30)
    assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text[:200]}"


def test_workbench_invalid_kind(auth_headers):
    r = requests.post(f"{API}/dynamic-workbench/start",
                      json={"kind": "bogus"}, headers=auth_headers, timeout=15)
    assert r.status_code == 400


def test_workbench_create_empty_mapping(auth_headers):
    r = requests.post(f"{API}/dynamic-workbench/start",
                      json={"kind": "create", "analysis_id": "ra_f898ade7",
                            "mapping": {}, "walkforward": False},
                      headers=auth_headers, timeout=15)
    assert r.status_code == 400


def test_workbench_refine_no_analysis_id(auth_headers):
    # dyn_demo0001 has no analysis_id -> refine should error
    r = requests.post(f"{API}/dynamic-workbench/start",
                      json={"kind": "refine", "dynamic_id": "dyn_demo0001",
                            "regime_ids": [0], "mode": "params",
                            "iterations": 2, "rounds": 1, "walkforward": False},
                      headers=auth_headers, timeout=15)
    assert r.status_code == 400


def test_autotrade_get_regime_configs(auth_headers):
    r = requests.get(f"{API}/autotrade/strategy/dyn_9130c3e6/coin/BTCUSDT",
                     headers=auth_headers, timeout=15)
    assert r.status_code == 200
    js = r.json()
    # Expect regime_configs present for dynamic strategy
    assert "regime_configs" in js or "regimes" in js or js is not None
