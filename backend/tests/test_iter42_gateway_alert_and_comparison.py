"""Iter42: strategy-comparison ignores collection/stale flags; notify-config has ibkr_gateway."""
import os
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def test_strategy_comparison_forces_flags_false():
    r = requests.get(
        f"{BASE}/api/analytics/strategy-comparison",
        params={"mode": "all", "include_collection": "true", "include_stale": "true"},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("include_collection") is False
    assert data.get("include_stale") is False
    for row in data.get("comparison", []):
        assert row.get("is_stale") is not True
    # stale_strategies list still available (may be empty)
    assert "stale_strategies" in data


def test_notify_config_has_ibkr_gateway():
    r = requests.get(f"{BASE}/api/telegram/notify-config", timeout=15)
    assert r.status_code == 200, r.text
    cfg = r.json()
    # cfg may be nested under 'config' key or top-level
    node = cfg.get("config", cfg)
    assert "ibkr_gateway" in node, f"Missing ibkr_gateway in {list(node.keys())}"
    # default should be true (bool)
    assert isinstance(node["ibkr_gateway"], bool)


def _admin_session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    for payload in (
        {"username": "Admin", "password": "Dean06Greif!/Admin"},
        {"user": "Admin", "password": "Dean06Greif!/Admin"},
    ):
        r = s.post(f"{BASE}/api/auth/login", json=payload, timeout=20)
        if r.status_code == 200:
            data = r.json()
            token = data.get("token") or data.get("access_token")
            if token:
                s.headers.update({"Authorization": f"Bearer {token}"})
            return s
    raise RuntimeError("admin login failed")


def test_notify_config_toggle_ibkr_gateway():
    s = _admin_session()
    r = s.post(f"{BASE}/api/telegram/notify-config", json={"ibkr_gateway": False}, timeout=15)
    assert r.status_code == 200, r.text
    verify = s.get(f"{BASE}/api/telegram/notify-config", timeout=15).json()
    vnode = verify.get("config", verify)
    assert vnode.get("ibkr_gateway") is False
    # restore
    r2 = s.post(f"{BASE}/api/telegram/notify-config", json={"ibkr_gateway": True}, timeout=15)
    assert r2.status_code == 200
    verify2 = s.get(f"{BASE}/api/telegram/notify-config", timeout=15).json()
    vnode2 = verify2.get("config", verify2)
    assert vnode2.get("ibkr_gateway") is True


def test_ibkr_status_ok():
    r = requests.get(f"{BASE}/api/ibkr/status", timeout=15)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "hint" in d or "probe" in d


def test_autotrade_balance_has_ibkr():
    r = requests.get(f"{BASE}/api/autotrade/balance", timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "ibkr" in d
