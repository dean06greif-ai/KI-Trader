"""Iteration 11 – Backend tests for panel-scoped copilot history & event-setups overview."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://strategy-engine-88.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- Copilot status regression ---
def test_copilot_status_ok():
    r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert "provider" in data and "ready" in data


# --- Panel-scoped history GETs ---
def test_history_no_panel_returns_all():
    r = requests.get(f"{BASE_URL}/api/copilot/history?limit=60", timeout=20)
    assert r.status_code == 200
    data = r.json()
    assert "messages" in data
    assert isinstance(data["messages"], list)


def test_history_backtester_panel_ok():
    r = requests.get(f"{BASE_URL}/api/copilot/history?panel=backtester&limit=60", timeout=20)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert isinstance(msgs, list)
    # If entries exist they should carry the panel field == 'backtester' (or be legacy)
    for m in msgs:
        if "panel" in m and m["panel"]:
            assert m["panel"] == "backtester"


def test_history_optimizer_panel_ok():
    r = requests.get(f"{BASE_URL}/api/copilot/history?panel=optimizer&limit=60", timeout=20)
    assert r.status_code == 200
    msgs = r.json()["messages"]
    assert isinstance(msgs, list)
    for m in msgs:
        if "panel" in m and m["panel"]:
            assert m["panel"] == "optimizer"


# --- Event-Setups overview regression ---
def test_event_setups_overview():
    r = requests.get(f"{BASE_URL}/api/event-setups/overview", timeout=30)
    assert r.status_code == 200
    data = r.json()
    events = data.get("events") or data
    # Data may be dict keyed by event name or list – normalise
    if isinstance(events, dict):
        keys = set(events.keys())
    else:
        keys = {e.get("event") or e.get("name") for e in events}
    for want in ("fomc", "cpi", "nfp", "ppi", "pce"):
        assert want in keys, f"missing event {want} in overview keys {keys}"


# --- AI playbook backtest overview regression ---
def test_ai_playbook_backtest_overview():
    r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30)
    assert r.status_code == 200
    assert isinstance(r.json(), (dict, list))


# --- Backtester chat start -> poll -> verify panel isolation, then cleanup ---
@pytest.mark.timeout(200)
def test_backtester_chat_isolation_and_cleanup(auth_headers):
    # Snapshot counts before
    r_bt_before = requests.get(f"{BASE_URL}/api/copilot/history?panel=backtester&limit=200", timeout=20)
    r_opt_before = requests.get(f"{BASE_URL}/api/copilot/history?panel=optimizer&limit=200", timeout=20)
    assert r_bt_before.status_code == 200 and r_opt_before.status_code == 200
    n_bt_before = len(r_bt_before.json()["messages"])
    n_opt_before = len(r_opt_before.json()["messages"])

    # Start chat with backtester panel context
    start = requests.post(f"{BASE_URL}/api/copilot/chat/start",
                          headers=auth_headers,
                          json={"message": "Kurzer Test: Nenne eine wichtige Backtester-Metrik in einem Satz.",
                                "context": {"panel": "backtester"}}, timeout=30)
    if start.status_code == 409:
        pytest.skip("Another copilot chat job is running – skipping isolation test")
    assert start.status_code == 200, start.text
    job_id = start.json()["job_id"]

    # Poll
    status = None
    deadline = time.time() + 170
    while time.time() < deadline:
        jr = requests.get(f"{BASE_URL}/api/copilot/chat/job/{job_id}", timeout=20)
        assert jr.status_code == 200
        js = jr.json()
        status = js["status"]
        if status in ("done", "error"):
            break
        time.sleep(4)
    assert status == "done", f"chat did not complete: status={status}"

    # After completion, backtester history must contain 2 new messages (user + assistant)
    r_bt_after = requests.get(f"{BASE_URL}/api/copilot/history?panel=backtester&limit=200", timeout=20)
    r_opt_after = requests.get(f"{BASE_URL}/api/copilot/history?panel=optimizer&limit=200", timeout=20)
    assert r_bt_after.status_code == 200 and r_opt_after.status_code == 200
    n_bt_after = len(r_bt_after.json()["messages"])
    n_opt_after = len(r_opt_after.json()["messages"])

    assert n_bt_after >= n_bt_before + 2, f"backtester history did not grow ({n_bt_before}->{n_bt_after})"
    assert n_opt_after == n_opt_before, f"optimizer history changed unexpectedly ({n_opt_before}->{n_opt_after})"

    # Last messages must have panel='backtester'
    latest = r_bt_after.json()["messages"][-2:]
    for m in latest:
        if "panel" in m:
            assert m["panel"] == "backtester", f"expected panel=backtester on new msg, got {m.get('panel')}"

    # Cleanup: DELETE only backtester panel history
    dr = requests.delete(f"{BASE_URL}/api/copilot/history?panel=backtester",
                         headers=auth_headers, timeout=20)
    assert dr.status_code == 200, dr.text

    r_bt_final = requests.get(f"{BASE_URL}/api/copilot/history?panel=backtester&limit=200", timeout=20)
    r_opt_final = requests.get(f"{BASE_URL}/api/copilot/history?panel=optimizer&limit=200", timeout=20)
    assert r_bt_final.status_code == 200 and r_opt_final.status_code == 200
    # Backtester should now be empty
    assert len(r_bt_final.json()["messages"]) == 0, "backtester history not fully cleared"
    # Optimizer still intact
    assert len(r_opt_final.json()["messages"]) == n_opt_before, \
        f"optimizer history modified by backtester delete ({n_opt_before}->{len(r_opt_final.json()['messages'])})"
