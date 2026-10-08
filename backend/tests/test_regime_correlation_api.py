"""API tests for regime-correlation feature (iteration 61).

Covers auth, validation, job lifecycle (start -> 409 conflict -> finish ->
result shape + presence in regime-lab/active with kind 'correlation').
Uses only 2-3 crypto symbols with timeframe=1h, days=30 to keep the external
candle fetch small (<~60s).
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "Dean06Greif!/Admin")
GET_T = 90
POST_T = 60

SMALL_BODY = {
    "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    "timeframe": "1h",
    "days": 30,
    "train_pct": 75,
}


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=60)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# --- GET: public, returns {job, result} ---
def test_get_correlation_shape():
    # Retry once on transient gateway errors (502/504 occasionally seen while a
    # long-running regime-lab job is finishing). Core correctness is in the flow test.
    last = None
    for _ in range(3):
        last = requests.get(f"{BASE_URL}/api/regime-correlation", timeout=60)
        if last.status_code == 200:
            break
        time.sleep(2)
    assert last.status_code == 200
    data = last.json()
    assert "job" in data and "result" in data
    job = data["job"]
    assert "running" in job and "progress" in job and "error" in job


# --- POST auth ---
def test_post_requires_admin():
    r = requests.post(f"{BASE_URL}/api/regime-correlation", json=SMALL_BODY, timeout=60)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


# --- POST validation ---
def test_post_too_few_symbols(auth_headers):
    r = requests.post(f"{BASE_URL}/api/regime-correlation",
                      json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 30},
                      headers=auth_headers, timeout=60)
    assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"


# --- Job lifecycle: start, concurrent -> 409, finish ---
def _wait_until_idle(timeout=180):
    start = time.time()
    while time.time() - start < timeout:
        d = requests.get(f"{BASE_URL}/api/regime-correlation", timeout=60).json()
        if not d.get("job", {}).get("running"):
            return d
        time.sleep(3)
    raise AssertionError("correlation job did not finish within timeout")


def test_full_correlation_flow(auth_headers):
    # ensure idle first
    _wait_until_idle(timeout=180)

    # start
    r = requests.post(f"{BASE_URL}/api/regime-correlation",
                      json=SMALL_BODY, headers=auth_headers, timeout=60)
    assert r.status_code == 200, f"start failed: {r.status_code} {r.text}"
    started = r.json()
    assert started.get("status") == "started"
    job_id = started.get("job_id")
    assert job_id

    # job appears in active regime-lab list with kind 'correlation'
    time.sleep(1.0)
    active = requests.get(f"{BASE_URL}/api/regime-lab/active", timeout=90).json()
    # /regime-lab/active returns {"active": {...}} with a single running job (or null)
    act = active.get("active") if isinstance(active, dict) else None
    if act is None and isinstance(active, dict):
        # fallback shapes
        jobs = active.get("jobs") or active.get("list") or []
        kinds = [j.get("kind") for j in jobs if isinstance(j, dict)]
    else:
        kinds = [act.get("kind")] if isinstance(act, dict) else []
    assert "correlation" in kinds, f"active jobs missing kind=correlation: raw={active}"

    # second POST while running -> 409
    r2 = requests.post(f"{BASE_URL}/api/regime-correlation",
                       json=SMALL_BODY, headers=auth_headers, timeout=60)
    assert r2.status_code == 409, f"expected 409 when running, got {r2.status_code} {r2.text}"

    # wait until done
    final = _wait_until_idle(timeout=240)
    job = final["job"]
    assert job.get("error") in (None, ""), f"job finished with error: {job.get('error')}"
    result = final["result"]
    assert result, "no result after job finish"
    # top-level
    assert "pairs" in result and "groups" in result and "symbols" in result
    assert "detector" in result
    assert result.get("timeframe") == "1h"
    assert int(result.get("days")) == 30

    # pair fields
    assert len(result["pairs"]) >= 1
    for key in ("ret_corr", "agree_pct", "score", "ret_corr_holdout",
                "agree_pct_holdout", "n_holdout"):
        assert key in result["pairs"][0], f"missing pair field {key}"
