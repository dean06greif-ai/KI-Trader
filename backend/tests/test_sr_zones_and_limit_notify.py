"""E2E tests for new features:
- GET /api/liquidity/sr-zones/{symbol} - 4h/1d S/R zones overlay
- GET/POST /api/telegram/notify-config - limit_orders toggle
- Regression: /api/liquidity/levels/{symbol}, /api/liquidity/heatmap/{symbol}
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_session(session):
    r = session.post(f"{BASE_URL}/api/auth/login",
                     json={"username": ADMIN_USER, "password": ADMIN_PASS},
                     timeout=20)
    if r.status_code != 200:
        # try alternate payload key
        r = session.post(f"{BASE_URL}/api/auth/login",
                         json={"user": ADMIN_USER, "password": ADMIN_PASS},
                         timeout=20)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    token = data.get("token") or data.get("access_token")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
    return session


# ------------------ S/R zones ------------------
@pytest.mark.parametrize("sym", ["BTCUSDT", "ETHUSDT", "SOLUSDT"])
def test_sr_zones_shape(session, sym):
    r = session.get(f"{BASE_URL}/api/liquidity/sr-zones/{sym}", timeout=30)
    assert r.status_code == 200, f"{sym}: {r.status_code} {r.text[:200]}"
    d = r.json()
    assert d["symbol"] == sym
    assert isinstance(d["price"], (int, float)) and d["price"] > 0
    assert d["timeframes"] == ["4h", "1d"]
    zones = d["zones"]
    assert isinstance(zones, list) and len(zones) > 0, "expected at least one zone"
    price = d["price"]
    per_tf_side = {}
    for z in zones:
        assert z["kind"] in ("support", "resistance")
        assert z["tf"] in ("4h", "1d")
        assert z["low"] < z["high"]
        assert z["low"] <= z["mid"] <= z["high"]
        assert z["touches"] >= 1
        assert 1 <= z["strength"] <= 100
        if z["kind"] == "support":
            assert z["mid"] < price, f"support {z['mid']} not below price {price}"
        else:
            assert z["mid"] > price, f"resistance {z['mid']} not above price {price}"
        per_tf_side[(z["tf"], z["kind"])] = per_tf_side.get((z["tf"], z["kind"]), 0) + 1
    for k, cnt in per_tf_side.items():
        assert cnt <= 3, f"more than 3 zones for {k}: {cnt}"
    assert len(zones) <= 12


def test_sr_zones_cached_fast(session):
    # first call (may already be cached from prior test)
    session.get(f"{BASE_URL}/api/liquidity/sr-zones/BTCUSDT", timeout=30)
    t0 = time.time()
    r = session.get(f"{BASE_URL}/api/liquidity/sr-zones/BTCUSDT", timeout=30)
    elapsed = time.time() - t0
    assert r.status_code == 200
    assert elapsed < 1.5, f"cache slow: {elapsed:.2f}s"


# ------------------ Telegram notify-config ------------------
def test_notify_config_has_limit_orders(session):
    r = session.get(f"{BASE_URL}/api/telegram/notify-config", timeout=15)
    assert r.status_code == 200
    cfg = r.json()
    assert "limit_orders" in cfg, f"missing limit_orders key: {list(cfg.keys())}"
    # default should be True
    assert cfg["limit_orders"] is True


def test_notify_config_toggle_limit_orders(admin_session):
    # set false
    r = admin_session.post(f"{BASE_URL}/api/telegram/notify-config",
                            json={"limit_orders": False}, timeout=15)
    assert r.status_code == 200, f"POST failed: {r.status_code} {r.text[:200]}"
    assert r.json()["limit_orders"] is False
    # verify GET
    r = admin_session.get(f"{BASE_URL}/api/telegram/notify-config", timeout=15)
    assert r.json()["limit_orders"] is False
    # reset to True (per user request)
    r = admin_session.post(f"{BASE_URL}/api/telegram/notify-config",
                            json={"limit_orders": True}, timeout=15)
    assert r.status_code == 200
    assert r.json()["limit_orders"] is True


# ------------------ Regression ------------------
def test_liquidity_levels_regression(session):
    r = session.get(f"{BASE_URL}/api/liquidity/levels/BTCUSDT", timeout=30)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    d = r.json()
    assert d["symbol"] == "BTCUSDT"


def test_liquidity_heatmap_regression(session):
    r = session.get(f"{BASE_URL}/api/liquidity/heatmap/BTCUSDT", timeout=45)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    d = r.json()
    assert d["symbol"] == "BTCUSDT"
