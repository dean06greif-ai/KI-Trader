"""Backend tests for iteration 8: Regime-Lab intuitive rework, new indicators, rule-preview."""
import os
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://trader-insights-81.preview.emergentagent.com").rstrip("/")
API = f"{BASE}/api"

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# --- Builder options: new indicators ---
def test_builder_options_new_indicators():
    r = requests.get(f"{API}/strategies/builder-options", timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    indicators = data.get("indicators") or []
    # indicators could be list of strings or list of dicts
    names = set()
    for it in indicators:
        if isinstance(it, str):
            names.add(it)
        elif isinstance(it, dict):
            names.add(it.get("name") or it.get("key") or it.get("id"))
    expected = {"supertrend", "supertrend_dir", "ema_slope_pct", "roc", "chop", "aroon_up", "aroon_down", "williams_r"}
    missing = expected - names
    assert not missing, f"missing indicators: {missing}, got: {names}"

    period_fields = data.get("period_fields") or []
    pf_names = set()
    for it in period_fields:
        if isinstance(it, str):
            pf_names.add(it)
        elif isinstance(it, dict):
            pf_names.add(it.get("name") or it.get("key") or it.get("id"))
    for needed in ("supertrend_period", "roc_period"):
        assert needed in pf_names, f"period_field {needed} missing, got {pf_names}"


# --- Regime lab endpoints ---
def test_regime_engine_defaults():
    r = requests.get(f"{API}/regime-lab/engine/defaults", timeout=15)
    assert r.status_code == 200, r.text


def test_regime_list():
    r = requests.get(f"{API}/regime-lab/list", timeout=15)
    assert r.status_code == 200, r.text


def test_regime_calibrations():
    r = requests.get(f"{API}/regime-lab/calibrations", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "calibrations" in data, f"missing 'calibrations': {data}"
    assert isinstance(data["calibrations"], list)


def test_analytics_daily():
    r = requests.get(f"{API}/analytics/daily", timeout=15)
    assert r.status_code == 200, r.text


# --- Rule preview with new indicators ---
def test_rule_preview_new_indicators(auth_headers):
    payload = {
        "symbol": "BTCUSDT",
        "definition": {
            "long_rules": [
                {"indicator": "supertrend_dir", "op": "==", "value": 1},
                {"indicator": "adx", "op": ">", "value": 20},
                {"indicator": "chop", "op": "<", "value": 55},
            ],
            "short_rules": [
                {"indicator": "supertrend_dir", "op": "==", "value": -1},
                {"indicator": "aroon_down", "op": ">", "value": 70},
            ],
        },
    }
    r = requests.post(f"{API}/strategies/rule-preview", json=payload, headers=auth_headers, timeout=60)
    # try alternate body shapes if 422
    if r.status_code == 422:
        alt = {"symbol": "BTCUSDT", **payload["definition"]}
        r = requests.post(f"{API}/strategies/rule-preview", json=alt, headers=auth_headers, timeout=60)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:500]}"
    body = r.text.lower()
    assert "unknown indicator" not in body, f"unknown indicator error: {r.text[:500]}"
