"""Iter49: test /api/ai/order-policy and /api/ai/config maker_policy/overrides persistence."""
import os
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://autopilot-staging-5.preview.emergentagent.com").rstrip("/")


def _admin_token():
    r = requests.post(f"{BASE}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=20)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers():
    return {"Authorization": f"Bearer {_admin_token()}"}


def test_order_policy_default_setup(auth_headers):
    r = requests.get(f"{BASE}/api/ai/order-policy", headers=auth_headers, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("policy") in ("setup", "all")
    rows = data.get("rows") or []
    assert isinstance(rows, list) and len(rows) > 0
    by_setup = {row["setup"]: row for row in rows}
    # Known presets: breakout -> market, pullback -> maker
    assert by_setup.get("breakout", {}).get("preset") == "market"
    assert by_setup.get("pullback", {}).get("preset") == "maker"
    for row in rows:
        for k in ("setup", "preset", "mode", "reason", "source"):
            assert k in row, row


def test_update_config_overrides_and_reset(auth_headers):
    # Set maker_policy=all + override breakout=maker
    r = requests.post(
        f"{BASE}/api/ai/config",
        headers=auth_headers,
        json={"maker_policy": "all", "maker_setup_overrides": {"breakout": "maker"}},
        timeout=20,
    )
    assert r.status_code == 200, r.text

    s = requests.get(f"{BASE}/api/ai/status", headers=auth_headers, timeout=20)
    assert s.status_code == 200
    cfg = (s.json() or {}).get("config") or {}
    assert cfg.get("maker_policy") == "all"
    assert (cfg.get("maker_setup_overrides") or {}).get("breakout") == "maker"

    # order-policy row for breakout should now be 'manual' source
    p = requests.get(f"{BASE}/api/ai/order-policy", headers=auth_headers, timeout=20)
    assert p.status_code == 200
    rows = p.json().get("rows") or []
    row_b = next((x for x in rows if x["setup"] == "breakout"), None)
    assert row_b is not None
    assert row_b["source"] == "manual", row_b

    # Reset
    r2 = requests.post(
        f"{BASE}/api/ai/config",
        headers=auth_headers,
        json={"maker_policy": "setup", "maker_setup_overrides": {}},
        timeout=20,
    )
    assert r2.status_code == 200
    s2 = requests.get(f"{BASE}/api/ai/status", headers=auth_headers, timeout=20)
    cfg2 = (s2.json() or {}).get("config") or {}
    assert cfg2.get("maker_policy") == "setup"
    assert (cfg2.get("maker_setup_overrides") or {}) == {}
