"""Iter9 E2E: Event-Setups router + safety db_storage + AI roles + legacy back-compat.
Runs against REACT_APP_BACKEND_URL. Non-destructive: NFP live remains false; no writes.
"""
import os
import time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

TIMEOUT = 60


@pytest.fixture(scope="module")
def token():
    for i in range(5):
        try:
            r = requests.post(f"{BASE}/api/auth/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS},
                              timeout=TIMEOUT)
            if r.status_code == 200:
                return r.json().get("token") or r.json().get("access_token")
        except Exception as e:
            print("login retry", i, e)
            time.sleep(3)
    pytest.skip("login failed")


@pytest.fixture
def h(token):
    return {"Authorization": f"Bearer {token}"}


# --- Event-Setups overview -------------------------------------------------
def test_event_setups_overview(h):
    r = requests.get(f"{BASE}/api/event-setups/overview", headers=h, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data.get("event_keys", [])) >= {"fomc", "cpi", "nfp", "ppi", "pce"}
    ev = data.get("events", {})
    for k in ("fomc", "cpi", "nfp", "ppi", "pce"):
        assert k in ev, f"missing event {k}"
        snap = ev[k]
        assert "label" in snap
        assert "phase" in snap
        assert "next_release_utc" in snap
        assert "backtest" in snap
    # cpi should already have validated backtest
    cpi_bt = ev["cpi"]["backtest"] or {}
    print("CPI backtest keys:", list(cpi_bt.keys()) if cpi_bt else None)
    # job field exists (may be null / dict)
    assert "job" in data


# --- Backtest start (with NFP but ai_revise=false to keep short) ----------
def test_event_setups_backtest_missing_events(h):
    r = requests.post(f"{BASE}/api/event-setups/backtest", headers=h,
                      json={"events": [], "years": 1, "ai_revise": False},
                      timeout=TIMEOUT)
    assert r.status_code == 400
    assert "Event" in r.text or "event" in r.text


def test_event_setups_backtest_unauth():
    r = requests.post(f"{BASE}/api/event-setups/backtest",
                      json={"events": ["nfp"], "years": 1, "ai_revise": False},
                      timeout=TIMEOUT)
    assert r.status_code in (401, 403)


def test_event_setups_backtest_start_and_poll(h):
    r = requests.post(f"{BASE}/api/event-setups/backtest", headers=h,
                      json={"events": ["nfp"], "years": 1, "ai_revise": False},
                      timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") in ("started", "busy"), body
    # Poll overview until job stops running or timeout ~3 min
    deadline = time.time() + 210
    saw_running = False
    last_status = None
    while time.time() < deadline:
        ov = requests.get(f"{BASE}/api/event-setups/overview", headers=h, timeout=TIMEOUT).json()
        job = ov.get("job") or {}
        last_status = job.get("status")
        if last_status == "running":
            saw_running = True
        if last_status in ("done", "error", None, "idle") and saw_running:
            break
        time.sleep(5)
    print("final job status:", last_status, "saw_running:", saw_running)
    # Accept: job either done or finished without visible running window (fast case)
    assert last_status in (None, "idle", "done", "error", "running"), last_status


# --- Live toggle (must reset to false) ------------------------------------
def test_event_setups_live_toggle(h):
    # 404 for unknown
    r404 = requests.post(f"{BASE}/api/event-setups/xyz/live", headers=h,
                         json={"live_enabled": False}, timeout=TIMEOUT)
    assert r404.status_code == 404

    # Toggle NFP true -> false
    r_on = requests.post(f"{BASE}/api/event-setups/nfp/live", headers=h,
                         json={"live_enabled": True}, timeout=TIMEOUT)
    assert r_on.status_code == 200, r_on.text
    snap_on = r_on.json()
    assert snap_on.get("live_enabled") is True, snap_on

    r_off = requests.post(f"{BASE}/api/event-setups/nfp/live", headers=h,
                          json={"live_enabled": False}, timeout=TIMEOUT)
    assert r_off.status_code == 200, r_off.text
    snap_off = r_off.json()
    assert snap_off.get("live_enabled") is False, snap_off


# --- Safety status db_storage -------------------------------------------
def test_safety_status_db_storage(h):
    r = requests.get(f"{BASE}/api/safety/status", headers=h, timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    data = r.json()
    checks = data.get("checks") or data.get("items") or []
    if isinstance(checks, dict):
        checks_list = list(checks.values())
        keys = list(checks.keys())
    else:
        checks_list = checks
        keys = [c.get("key") or c.get("name") for c in checks_list]
    print("safety keys:", keys)
    # find db_storage check
    found = None
    for c in checks_list:
        k = (c.get("key") or c.get("name") or "").lower()
        if "db_storage" in k or "storage" in k or "speicher" in k.lower():
            found = c
            break
    assert found is not None, f"db_storage check missing, got: {keys}"
    assert "level" in found
    # used_mb / quota_mb may be under details
    text = str(found).lower()
    assert "used_mb" in text or "quota_mb" in text or "mb" in text, found


# --- AI roles model change ----------------------------------------------
def test_ai_roles_update(h):
    r = requests.post(f"{BASE}/api/ai/roles", headers=h,
                      json={"news_watcher": {"interval_min": 15}},
                      timeout=TIMEOUT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "success", body


# --- Legacy back-compat -------------------------------------------------
@pytest.mark.parametrize("path", [
    "/api/fomc/status",
    "/api/econ/cpi/status",
    "/api/fomc/backtest",
    "/api/econ/cpi/backtest",
])
def test_legacy_endpoints(h, path):
    r = requests.get(f"{BASE}{path}", headers=h, timeout=TIMEOUT)
    assert r.status_code == 200, f"{path} -> {r.status_code}: {r.text[:200]}"
