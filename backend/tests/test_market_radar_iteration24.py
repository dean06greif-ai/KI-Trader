"""Backend tests for iteration 24: Market Radar + limit-order lines regression."""
import os
import time
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ai-trader-preview.preview.emergentagent.com").rstrip("/")
TIMEOUT = 60


def _get(path, retries=4):
    last = None
    for i in range(retries):
        try:
            r = requests.get(f"{BASE_URL}{path}", timeout=TIMEOUT)
            if r.status_code < 500:
                return r
            last = r
        except Exception as e:
            last = e
        time.sleep(2 * (i + 1))
    if isinstance(last, Exception):
        raise last
    return last


def _post(path, retries=4, **kw):
    last = None
    for i in range(retries):
        try:
            r = requests.post(f"{BASE_URL}{path}", timeout=TIMEOUT, **kw)
            if r.status_code < 500:
                return r
            last = r
        except Exception as e:
            last = e
        time.sleep(2 * (i + 1))
    if isinstance(last, Exception):
        raise last
    return last


def test_radar_status_ok():
    r = _get("/api/ai/radar/status")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert "last_run" in data and data["last_run"], "last_run missing/empty"
    assert data.get("running") is False
    assert "auto_schedule" in data and "Sonntag" in data["auto_schedule"]
    report = data.get("report") or {}
    for k in ["summary", "short_term", "mid_term", "long_term", "coins", "watchlist"]:
        assert k in report, f"missing key in report: {k}; got keys={list(report.keys())}"


def test_radar_run_requires_admin():
    r = _post("/api/ai/radar/run")
    assert r.status_code in (401, 403), f"expected 401/403 got {r.status_code}: {r.text[:200]}"


def test_limit_orders_ok():
    r = _get("/api/ai/limit-orders")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert "orders" in data and isinstance(data["orders"], list)
    assert "history" in data and isinstance(data["history"], list)


def test_sr_zones_regression():
    r = _get("/api/liquidity/sr-zones/BTCUSDT")
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    assert "zones" in data and isinstance(data["zones"], list)
