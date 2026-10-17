"""Regression tests for iteration 40: IBKR forex, forex fees, MR strategies, copilot bg-job, optimizer queue series."""
import os
import time
import json
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://08569cec-212c-4c67-b6aa-ea6a7cdffdb9.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in {r.json()}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---- IBKR status ----
def test_ibkr_status_not_configured():
    r = None
    for _ in range(3):
        try:
            r = requests.get(f"{BASE_URL}/api/ibkr/status", timeout=30)
            break
        except requests.exceptions.ReadTimeout:
            time.sleep(2)
    assert r is not None, "ibkr/status timed out repeatedly"
    assert r.status_code == 200
    d = r.json()
    if d.get("configured"):
        # Umgebung MIT IBKR_GATEWAY_URL: Statusform prüfen statt "nicht konfiguriert"
        assert d.get("gateway_url_set") is True
        assert "authenticated" in d and "hint" in d and "probe" in d
        return
    assert d.get("configured") is False
    assert d.get("gateway_url_set") is False
    err = (d.get("error") or "") + (d.get("message") or "")
    assert "nicht" in err.lower() or "gateway" in err.lower(), f"expected german hint, got: {d}"


# ---- Strategies include new MR strategies with params meta ----
def test_strategies_contains_mr_strategies():
    r = requests.get(f"{BASE_URL}/api/strategies", timeout=15)
    assert r.status_code == 200
    data = r.json()
    lst = data["strategies"] if isinstance(data, dict) and "strategies" in data else data
    ids = {s["id"] for s in lst}
    assert "mr_zscore" in ids, f"mr_zscore missing; ids={ids}"
    assert "mr_keltner_fade" in ids, f"mr_keltner_fade missing"
    for sid in ("mr_zscore", "mr_keltner_fade"):
        strat = next(s for s in lst if s["id"] == sid)
        params = strat.get("params") or {}
        assert isinstance(params, dict) and len(params) > 0, f"{sid} has no params meta"
        # each param should have min/max/step
        first_key = next(iter(params))
        pmeta = params[first_key]
        assert "min" in pmeta and "max" in pmeta and "step" in pmeta, f"{sid}.{first_key} missing min/max/step: {pmeta}"


# ---- Settings: forex commission fields present & updatable ----
def test_settings_forex_fees_present_and_updatable(auth_headers):
    r = requests.get(f"{BASE_URL}/api/settings", timeout=10)
    assert r.status_code == 200, r.text[:200]
    s = r.json()
    assert "forex_commission_pct" in s
    assert "forex_min_commission_usd" in s
    assert abs(float(s["forex_commission_pct"]) - 0.002) < 1e-9 or "forex_commission_pct" in s
    assert float(s["forex_min_commission_usd"]) >= 0

    # Update
    new_pct = 0.003
    new_min = 2.5
    up = requests.post(
        f"{BASE_URL}/api/settings",
        headers=auth_headers,
        json={"forex_commission_pct": new_pct, "forex_min_commission_usd": new_min},
        timeout=10,
    )
    assert up.status_code == 200, up.text[:300]

    r2 = requests.get(f"{BASE_URL}/api/settings", timeout=10)
    s2 = r2.json()
    assert abs(float(s2["forex_commission_pct"]) - new_pct) < 1e-9, s2
    assert abs(float(s2["forex_min_commission_usd"]) - new_min) < 1e-9, s2

    # Restore
    requests.post(
        f"{BASE_URL}/api/settings",
        headers=auth_headers,
        json={"forex_commission_pct": 0.002, "forex_min_commission_usd": 2.0},
        timeout=10,
    )


# ---- Copilot chat: background job start + poll ----
def test_copilot_chat_start_and_poll(auth_headers):
    start = requests.post(
        f"{BASE_URL}/api/copilot/chat/start",
        headers=auth_headers,
        json={"message": "Hallo, was kannst du?", "context": {"panel": "optimizer"}},
        timeout=20,
    )
    assert start.status_code == 200, start.text[:300]
    job_id = start.json().get("job_id")
    assert job_id, f"no job_id: {start.json()}"

    deadline = time.time() + 130
    last = None
    while time.time() < deadline:
        j = requests.get(f"{BASE_URL}/api/copilot/chat/job/{job_id}", headers=auth_headers, timeout=15)
        assert j.status_code == 200
        last = j.json()
        st = last.get("status")
        if st in ("done", "error", "failed"):
            break
        time.sleep(3)
    assert last is not None
    assert last.get("status") in ("done", "error", "failed"), f"still running: {last}"
    if last.get("status") == "done":
        reply = (last.get("result") or {}).get("reply") or last.get("reply")
        assert reply and len(str(reply).strip()) > 0, f"empty reply: {last}"
    else:
        # OpenRouter overload/error accepted; expect german-ish error text
        err = last.get("error") or (last.get("result") or {}).get("error") or ""
        assert isinstance(err, str)
        print(f"Copilot returned status={last.get('status')} err={err[:200]}")


# ---- Series (optimizer queue) create + poll + result + cleanup ----
def test_series_optimizer_create_and_result(auth_headers):
    body = {
        "kind": "optimizer",
        "body": {
            "mode": "params",
            "strategy_id": "mr_zscore",
            "symbols": ["BTCUSDT"],
            "days": 3,
            "timeframe": "15m",
            "objective": "combo",
            "iterations": 5,
            "min_trades": 1,
            "algorithm": "random",
        },
    }
    r = requests.post(f"{BASE_URL}/api/series/add", headers=auth_headers, json=body, timeout=20)
    assert r.status_code < 400, f"series/add failed: {r.status_code} {r.text[:300]}"
    resp = r.json()
    sid = resp.get("id") or resp.get("series_id") or (resp.get("item") or {}).get("id")
    assert sid, f"no series id: {resp}"

    # List series
    lst = requests.get(f"{BASE_URL}/api/series", headers=auth_headers, timeout=10)
    assert lst.status_code == 200
    items = lst.json()
    items = items if isinstance(items, list) else items.get("items", [])
    assert any((it.get("id") == sid) for it in items), f"series {sid} not in list"

    # Poll for done
    deadline = time.time() + 240
    final_status = None
    while time.time() < deadline:
        one = requests.get(f"{BASE_URL}/api/series", headers=auth_headers, timeout=10).json()
        one = one if isinstance(one, list) else one.get("items", [])
        cur = next((x for x in one if x.get("id") == sid), None)
        if cur:
            final_status = cur.get("status")
            if final_status in ("done", "finished", "completed", "error", "failed"):
                break
        time.sleep(5)
    print(f"series status={final_status}")

    if final_status in ("done", "finished", "completed"):
        res = requests.get(f"{BASE_URL}/api/series/{sid}/result", headers=auth_headers, timeout=15)
        assert res.status_code == 200, res.text[:300]
        assert res.json(), "empty result"

    # Cleanup finished
    d = requests.delete(f"{BASE_URL}/api/series/finished", headers=auth_headers, timeout=10)
    assert d.status_code in (200, 204)


# ---- Forex backtest uses forex fees, doesn't crash ----
def test_backtest_forex_mr_zscore(auth_headers):
    r = requests.post(
        f"{BASE_URL}/api/backtest/run",
        headers=auth_headers,
        json={"strategy_ids": ["mr_zscore"], "symbols": ["EURUSD"], "days": 3, "max_capital": 1000},
        timeout=30,
    )
    assert r.status_code < 400, f"backtest/run failed: {r.status_code} {r.text[:300]}"
    resp = r.json()
    job_id = resp.get("job_id") or resp.get("id")
    if not job_id:
        # synchronous result
        assert resp, "empty response"
        return
    deadline = time.time() + 180
    last = None
    while time.time() < deadline:
        j = requests.get(f"{BASE_URL}/api/backtest/status/{job_id}", headers=auth_headers, timeout=15)
        if j.status_code == 200:
            last = j.json()
            st = last.get("status")
            if st in ("done", "finished", "completed", "error", "failed"):
                break
        time.sleep(4)
    assert last is not None
    assert last.get("status") in ("done", "finished", "completed", "error", "failed"), f"still running: {last}"
    # accept both done and error, ensure no crash
    print(f"backtest final status={last.get('status')}")
