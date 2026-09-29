"""Iteration 35 – API-Tests: RAM-Profil, Risiko-Sizing-Config, Playbook-Setups."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

ADMIN = {"username": "Admin", "password": "Dean06Greif!/Admin"}


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_client(client):
    r = client.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Admin login failed {r.status_code}: {r.text[:300]}")
    token = r.json().get("token")
    assert isinstance(token, str) and token
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
    return s


# --- health / basics ---
def test_health(client):
    r = client.get(f"{BASE_URL}/api/health", timeout=30)
    assert r.status_code == 200, r.text[:300]


# --- ram_guard.profile via /api/system/ram ---
def test_system_ram_container_profile(client):
    r = client.get(f"{BASE_URL}/api/system/ram", timeout=60)
    assert r.status_code == 200, r.text[:300]
    c = r.json().get("container")
    assert isinstance(c, dict), r.json()
    for k in ("limit_mb", "profile", "candle_cache_max", "orderflow_ticks_per_symbol",
              "ml_train_limits", "free_mb"):
        assert k in c, f"missing {k} in {c}"
    assert c["profile"] in ("big", "small"), c["profile"]
    assert float(c["free_mb"]) > 0, c["free_mb"]
    assert float(c["limit_mb"]) > 0


# --- /api/ai/status config (Boot-Migration risk_sizing_v1) ---
def test_ai_status_risk_config(client):
    r = client.get(f"{BASE_URL}/api/ai/status", timeout=60)
    assert r.status_code == 200, r.text[:300]
    cfg = r.json().get("config")
    assert isinstance(cfg, dict)
    assert cfg.get("sizing_mode") == "risk"
    assert float(cfg.get("risk_per_trade_pct")) == 2.0
    assert float(cfg.get("risk_max_margin_pct")) == 15.0
    assert int(cfg.get("risk_max_leverage")) == 15
    assert float(cfg.get("risk_conviction_floor")) == 0.5
    # bestehende Keys weiterhin vorhanden (Regression)
    for k in ("lev_mode", "max_capital_per_trade", "crv_min"):
        assert k in cfg, f"legacy key {k} missing"
    # KI-Trader muss lokal ausgeschaltet bleiben
    assert cfg.get("enabled") is False


# --- /api/ai/config clamps ---
def test_config_clamp_risk_per_trade(admin_client):
    r = admin_client.post(f"{BASE_URL}/api/ai/config", json={"risk_per_trade_pct": 50}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert float(st["risk_per_trade_pct"]) == 10.0, st["risk_per_trade_pct"]
    # zurücksetzen
    admin_client.post(f"{BASE_URL}/api/ai/config", json={"risk_per_trade_pct": 2}, timeout=60)
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert float(st["risk_per_trade_pct"]) == 2.0


def test_config_clamp_leverage(admin_client):
    r = admin_client.post(f"{BASE_URL}/api/ai/config", json={"risk_max_leverage": 999}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert int(st["risk_max_leverage"]) == 200, st["risk_max_leverage"]
    admin_client.post(f"{BASE_URL}/api/ai/config", json={"risk_max_leverage": 15}, timeout=60)
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert int(st["risk_max_leverage"]) == 15


def test_config_sizing_mode_bogus_ignored_and_legacy_accepted(admin_client):
    r = admin_client.post(f"{BASE_URL}/api/ai/config", json={"sizing_mode": "bogus"}, timeout=60)
    assert r.status_code == 200, r.text[:300]
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert st["sizing_mode"] == "risk"

    r = admin_client.post(f"{BASE_URL}/api/ai/config", json={"sizing_mode": "legacy"}, timeout=60)
    assert r.status_code == 200
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert st["sizing_mode"] == "legacy"

    admin_client.post(f"{BASE_URL}/api/ai/config", json={"sizing_mode": "risk"}, timeout=60)
    st = admin_client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()["config"]
    assert st["sizing_mode"] == "risk"


def test_config_requires_admin(client):
    r = client.post(f"{BASE_URL}/api/ai/config", json={"risk_per_trade_pct": 3}, timeout=60)
    assert r.status_code in (401, 403), f"{r.status_code} {r.text[:200]}"


# --- /api/ai/playbook ---
def test_playbook_new_setups_and_maturity(client):
    r = client.get(f"{BASE_URL}/api/ai/playbook", timeout=90)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    for k in ("setups", "maturity", "live_blocked", "live_stats", "custom"):
        assert k in d, f"missing key {k}"
    setups = d["setups"]
    for sid in ("order_block", "fvg_fill", "htf_range"):
        assert sid in setups, f"{sid} missing in setups"
    base = ["trend_follow", "breakout", "squeeze_breakout", "mean_reversion", "range_fade",
            "liquidity_sweep", "momentum_news", "pullback", "swing_trend", "hedge"]
    for sid in base:
        assert sid in setups
    assert len(setups) >= 13
    fields = {"setup", "custom", "trades", "winrate", "pnl", "live_trades",
              "live_winrate", "live_pnl", "verdict", "live_ready", "reason"}
    assert isinstance(d["maturity"], list) and d["maturity"]
    for row in d["maturity"]:
        assert fields.issubset(row.keys()), f"missing {fields - set(row.keys())}"
    ids = {row["setup"] for row in d["maturity"]}
    for sid in ("order_block", "fvg_fill", "htf_range"):
        assert sid in ids
        row = next(x for x in d["maturity"] if x["setup"] == sid)
        assert row["live_ready"] is False
        assert row["trades"] == 0
    assert "_id" not in d
