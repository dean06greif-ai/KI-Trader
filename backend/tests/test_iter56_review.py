"""Iteration 56 review: regime-lab list additive fields, engine defaults sub_min_days, dynamic workbench regime_presets passthrough."""
import os, requests, pytest, time

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'http://localhost:8001').rstrip('/')
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---- Regime-Lab list: regimes entries include additive trend/nnfx fields ----
def test_regime_lab_list_has_trend_nnfx(headers):
    r = requests.get(f"{BASE_URL}/api/regime-lab/list", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or data.get("analyses") or []
    assert items, "No analyses returned"
    target = next((a for a in items if a.get("id") == "ra_9f0077e5"), None)
    assert target, f"ra_9f0077e5 not found; ids={[a.get('id') for a in items][:10]}"
    regimes = target.get("regimes") or []
    assert regimes, "no regimes list"
    # Check additive fields on at least first regime
    r0 = regimes[0]
    assert "trend" in r0, f"'trend' missing: keys={list(r0.keys())}"
    assert "nnfx" in r0, f"'nnfx' missing: keys={list(r0.keys())}"


# ---- Engine defaults contain sub_min_days ----
def test_engine_defaults_sub_min_days(headers):
    # Try two likely routes
    r = requests.get(f"{BASE_URL}/api/regime-lab/engine/defaults", headers=headers, timeout=15)
    if r.status_code == 404:
        r = requests.get(f"{BASE_URL}/api/regime-lab/engine/fields", headers=headers, timeout=15)
    assert r.status_code == 200, f"Status {r.status_code}: {r.text[:300]}"
    body = r.json()
    txt = str(body)
    assert "sub_min_days" in txt, f"sub_min_days missing from engine defaults: {txt[:500]}"


# ---- Dynamic workbench start with regime_presets ----
def test_dynamic_workbench_regime_presets(headers):
    # Ensure no leftover job from previous run
    requests.post(f"{BASE_URL}/api/dynamic-workbench/reset", headers=headers, timeout=15)
    time.sleep(1)
    payload = {
        "kind": "discover",
        "analysis_id": "ra_9f0077e5",
        "regime_ids": [0, 2],
        "mode": "discovery",
        "iterations": 5,
        "rounds": 1,
        "indicators": ["adx", "rsi"],
        "regime_presets": {
            "0": {"indicators": ["adx", "ema_slow"], "objective": "combo"},
            "2": {"indicators": ["rsi", "bb_lower"], "objective": "win_rate"},
        },
    }
    r = requests.post(f"{BASE_URL}/api/dynamic-workbench/start", headers=headers, json=payload, timeout=30)
    assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:400]}"
    body = r.json()
    job_id = body.get("job_id") or body.get("id")
    assert job_id, f"No job_id: {body}"
    # Stop the job
    time.sleep(1)
    stop_endpoints = [
        f"/api/dynamic-workbench/cancel/{job_id}",
        f"/api/dynamic-workbench/stop/{job_id}",
    ]
    stopped = False
    for ep in stop_endpoints:
        s = requests.post(f"{BASE_URL}{ep}", headers=headers, timeout=15)
        if s.status_code in (200, 201, 204):
            stopped = True
            break
    # Safety: emergency reset to free the slot for the next test
    requests.post(f"{BASE_URL}/api/dynamic-workbench/reset", headers=headers, timeout=15)
    time.sleep(1)
    assert stopped, "Could not cancel workbench job"


def test_dynamic_workbench_legacy_body(headers):
    """Legacy call without regime_presets must still work."""
    requests.post(f"{BASE_URL}/api/dynamic-workbench/reset", headers=headers, timeout=15)
    time.sleep(1)
    payload = {
        "kind": "discover",
        "analysis_id": "ra_9f0077e5",
        "regime_ids": [0],
        "mode": "discovery",
        "iterations": 5,
        "rounds": 1,
        "indicators": ["adx", "rsi"],
    }
    r = requests.post(f"{BASE_URL}/api/dynamic-workbench/start", headers=headers, json=payload, timeout=30)
    assert r.status_code in (200, 201), f"{r.status_code}: {r.text[:400]}"
    body = r.json()
    job_id = body.get("job_id") or body.get("id")
    assert job_id
    time.sleep(1)
    for ep in [f"/api/dynamic-workbench/cancel/{job_id}", f"/api/dynamic-workbench/stop/{job_id}"]:
        s = requests.post(f"{BASE_URL}{ep}", headers=headers, timeout=15)
        if s.status_code in (200, 201, 204):
            break
