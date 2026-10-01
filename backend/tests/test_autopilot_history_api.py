"""API tests for Autopilot history / reference-start / pin (iteration 40).

Covers:
  - GET /api/regime-lab/autopilot/runs → rating + best_in_group fields
  - POST /api/regime-lab/autopilot/runs/{id}/pin (admin, 401/404)
  - POST /api/regime-lab/autopilot with reference_run_id (admin): started job
    carries params.reference & params.engine_config, referenced run pinned=true.
  - Unknown reference_run_id → 404.
"""
import os
import time

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS},
                      timeout=30)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _get_runs():
    r = requests.get(f"{BASE_URL}/api/regime-lab/autopilot/runs", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data["runs"] if isinstance(data, dict) and "runs" in data else data


def _cancel_active(headers):
    """Cancel any running regime-lab job to avoid 409s."""
    try:
        r = requests.get(f"{BASE_URL}/api/regime-lab/active", headers=headers, timeout=30)
        if r.status_code != 200:
            return
        data = r.json() or {}
        active = data.get("active") if isinstance(data, dict) else None
        jobs = []
        if isinstance(active, list):
            jobs = active
        elif isinstance(active, dict):
            jobs = [active]
        for j in jobs:
            jid = j.get("job_id") or j.get("id")
            if jid:
                requests.post(f"{BASE_URL}/api/regime-lab/cancel/{jid}", headers=headers, timeout=30)
        time.sleep(2)
    except Exception as e:
        print("cancel_active err:", e)


# ---------- GET autopilot runs ----------
def test_autopilot_runs_returns_rating_and_best_in_group():
    r = requests.get(f"{BASE_URL}/api/regime-lab/autopilot/runs", timeout=30)
    assert r.status_code == 200, r.text
    runs = r.json()
    runs = runs.get("runs", runs) if isinstance(runs, dict) else runs
    assert isinstance(runs, list) and len(runs) > 0
    by_id = {x["id"]: x for x in runs if "id" in x}
    for rid in ("ap_demo_top", "ap_demo_mid", "ap_demo_weak"):
        assert rid in by_id, f"missing seeded run {rid}"
    top = by_id["ap_demo_top"]
    assert top.get("rating", {}).get("grade") in ("top", "good")
    assert top.get("rating", {}).get("label")
    assert "best_in_group" in top
    # weak should not be best_in_group
    weak = by_id["ap_demo_weak"]
    assert weak.get("rating", {}).get("grade") in ("mid", "weak")
    assert weak.get("best_in_group") is False


# ---------- Pin ----------
def test_pin_requires_admin():
    r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/ap_demo_mid/pin",
                      json={"pinned": True}, timeout=30)
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"


def test_pin_unknown_id_404(admin_headers):
    r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/__nope__/pin",
                      json={"pinned": True}, headers=admin_headers, timeout=30)
    assert r.status_code == 404


def test_pin_toggle(admin_headers):
    rid = "ap_demo_mid"
    r1 = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/{rid}/pin",
                       json={"pinned": True}, headers=admin_headers, timeout=30)
    assert r1.status_code == 200, r1.text
    # verify via list
    runs = _get_runs()
    row = next(x for x in runs if x["id"] == rid)
    assert row.get("pinned") is True
    # toggle off
    r2 = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/{rid}/pin",
                       json={"pinned": False}, headers=admin_headers, timeout=30)
    assert r2.status_code == 200
    runs = _get_runs()
    row = next(x for x in runs if x["id"] == rid)
    assert not row.get("pinned")


# ---------- Reference-Start ----------
def test_autopilot_start_with_reference_run(admin_headers):
    _cancel_active(admin_headers)
    time.sleep(1)
    body = {
        "reference_run_id": "ap_demo_top",
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "days": 360,
        "execution": "cloud",
        "max_rounds": 3,
        "warm_start": True,
    }
    r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                      json=body, headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "started"
    job_id = data.get("job_id")
    assert job_id

    # inspect job params
    found = None
    for _ in range(10):
        s = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}",
                         headers=admin_headers, timeout=30)
        if s.status_code == 200:
            found = s.json()
            if (found.get("params") or {}).get("reference") or (found.get("params") or {}).get("engine_config"):
                break
        time.sleep(0.5)
    assert found is not None, "job not found"
    params = found.get("params") or {}
    ref = params.get("reference") or {}
    assert ref.get("run_id") == "ap_demo_top"
    assert ref.get("timeframe") == "1h"
    ec = params.get("engine_config") or {}
    assert ec.get("detector") == "kombi", f"engine_config did not take reference best: {ec}"

    # referenced run should be pinned
    runs = _get_runs()
    top = next(x for x in runs if x["id"] == "ap_demo_top")
    assert top.get("pinned") is True

    # cancel the job
    c = requests.post(f"{BASE_URL}/api/regime-lab/cancel/{job_id}",
                      headers=admin_headers, timeout=30)
    assert c.status_code in (200, 202, 204)
    # Unpin again for cleanliness
    requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/ap_demo_top/pin",
                  json={"pinned": False}, headers=admin_headers, timeout=30)


def test_autopilot_start_with_unknown_reference_404(admin_headers):
    _cancel_active(admin_headers)
    time.sleep(1)
    body = {"reference_run_id": "__nope__", "symbols": ["BTCUSDT"],
            "timeframe": "4h", "days": 360, "execution": "cloud", "max_rounds": 3}
    r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                      json=body, headers=admin_headers, timeout=30)
    assert r.status_code == 404
    msg = (r.json().get("detail") or "").lower()
    assert "referenz" in msg or "nicht" in msg
