"""Iteration 29: Verify Copilot advisor endpoints (status/model/apply)
+ regime_gate autotrade endpoint + strategy_coin_config regime persistence.

Skip Copilot chat LLM calls unless COPILOT_LLM=1 (they take 10-60s and
were already validated in iter 28)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    BASE_URL = "http://localhost:8001"

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    for i in range(3):
        try:
            r = requests.post(f"{BASE_URL}/api/auth/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=30)
            if r.status_code == 200:
                tok = r.json().get("token") or r.json().get("access_token")
                assert tok, f"no token in {r.json()}"
                return tok
        except requests.exceptions.RequestException:
            pass
        time.sleep(2)
    pytest.fail("login failed after retries")


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- Copilot status ---------------------------------------------------------

def test_copilot_status_ready_shared_and_deepseek():
    # Retry on transient ingress timeouts / 502s
    r = None
    for i in range(4):
        try:
            r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=40)
            if r.status_code == 200:
                break
        except requests.exceptions.RequestException:
            pass
        time.sleep(2 + i)
    assert r is not None and r.status_code == 200, (r.status_code if r else "no response", (r.text[:200] if r else ""))
    data = r.json()
    assert data.get("ready") is True, f"copilot not ready: {data}"
    assert data.get("key_source") in ("shared", "dedicated"), data
    # Priority claim from review: fallback to OPENROUTER_API_KEY => 'shared'
    assert data["key_source"] == "shared", f"expected shared fallback, got {data['key_source']}"
    models = data.get("models") or []
    assert any("deepseek" in (m or "").lower() for m in models), f"no deepseek in models: {models[:10]}"
    assert isinstance(data.get("paid_models"), list)
    assert data.get("provider")
    assert data.get("keys_available", 0) >= 1


# --- Copilot model selection ----------------------------------------------

def test_copilot_model_set_and_reset(auth_headers):
    # try a deepseek variant available in the list
    r0 = requests.get(f"{BASE_URL}/api/copilot/status", timeout=10)
    models = r0.json().get("models") or []
    target = next((m for m in models if "deepseek" in m.lower()), None)
    assert target, "no deepseek model available"

    r = requests.post(f"{BASE_URL}/api/copilot/model", json={"model": target},
                      headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text[:300]

    r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=10)
    assert r.json().get("model") == target, r.json()

    # reset to auto
    r = requests.post(f"{BASE_URL}/api/copilot/model", json={"model": None},
                      headers=auth_headers, timeout=15)
    assert r.status_code == 200, r.text[:300]
    r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=10)
    assert r.json().get("model") in (None, ""), r.json()


# --- Copilot apply rejects settings ---------------------------------------

def test_copilot_apply_rejects_settings_proposal(auth_headers):
    body = {"proposal": {"type": "settings", "settings": {"mode": "parameter"}}}
    r = requests.post(f"{BASE_URL}/api/copilot/apply", json=body,
                      headers=auth_headers, timeout=15)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
    detail = r.json().get("detail", "")
    assert "Panel" in detail or "übernommen" in detail or "Übernehmen" in detail


# --- Copilot history (regression) -----------------------------------------

def test_copilot_history_ok():
    r = requests.get(f"{BASE_URL}/api/copilot/history?limit=5", timeout=15)
    assert r.status_code == 200
    assert isinstance(r.json().get("messages"), list)


# --- Optional live chat (skipped by default) ------------------------------

@pytest.mark.skipif(os.environ.get("COPILOT_LLM") != "1",
                    reason="live LLM call skipped (set COPILOT_LLM=1 to enable)")
def test_copilot_chat_settings_proposal(auth_headers):
    body = {"message": "Bitte stelle für mich die Optimizer-Einstellungen ein (Modus egal).",
            "context": {"panel": "optimizer",
                        "settings": {"mode": "parameter", "days": 30}}}
    r = requests.post(f"{BASE_URL}/api/copilot/chat", json=body,
                      headers=auth_headers, timeout=90)
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    assert data.get("reply"), data


# --- Regime phase endpoint ------------------------------------------------

def test_regime_phase_btcusdt():
    r = requests.get(f"{BASE_URL}/api/autotrade/regime_phase/BTCUSDT", timeout=60)
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    assert data.get("phase") in ("bulle", "bär", "seitwärts"), data
    assert "label" in data
    conf = data.get("confidence")
    assert isinstance(conf, (int, float))


# --- Strategy coin config: regime filter persistence ----------------------

def test_strategy_coin_config_regime_filter_persist(auth_headers):
    strategy_id = "trend_following"  # existing default strategy
    symbol = "BTCUSDT"
    # First fetch current -> use as base
    r_get = requests.get(
        f"{BASE_URL}/api/autotrade/strategy/{strategy_id}/coin/{symbol}",
        timeout=15)
    assert r_get.status_code == 200, r_get.text[:200]
    current = r_get.json().get("config", {})

    payload = {
        **current,
        "regime_filter_enabled": True,
        "regime_block_phases": ["seitwärts"],
    }
    r_set = requests.post(
        f"{BASE_URL}/api/autotrade/strategy/{strategy_id}/coin/{symbol}",
        json=payload, headers=auth_headers, timeout=15)
    assert r_set.status_code == 200, r_set.text[:300]

    r2 = requests.get(
        f"{BASE_URL}/api/autotrade/strategy/{strategy_id}/coin/{symbol}",
        timeout=15)
    assert r2.status_code == 200
    cfg = r2.json().get("config", {})
    assert cfg.get("regime_filter_enabled") is True, cfg
    assert cfg.get("regime_block_phases") == ["seitwärts"], cfg


# --- Regression: core endpoints -------------------------------------------

@pytest.mark.parametrize("path", [
    "/api/settings",
    "/api/optimizer/active",
    "/api/copilot/history",
    "/api/strategies",
])
def test_core_endpoints_alive(path):
    r = requests.get(f"{BASE_URL}{path}", timeout=20)
    assert r.status_code in (200, 401), f"{path} -> {r.status_code} {r.text[:200]}"


def test_auth_login_regression():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS},
                      timeout=15)
    assert r.status_code == 200
    assert (r.json().get("token") or r.json().get("access_token"))
