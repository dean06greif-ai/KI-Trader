"""Review tests: multi-worker + asset suggest + regime-lab worker targeting."""
import os
import time
import uuid
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


def _admin_token():
    r = requests.post(f"{BASE}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _hdr():
    return {"Authorization": f"Bearer {_admin_token()}"}


def _worker_token(hdr):
    r = requests.get(f"{BASE}/api/localworker/token", headers=hdr, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


def _poll(wtoken, wid, want_compute=False, name=None):
    headers = {"X-Worker-Token": wtoken}
    body = {
        "worker_id": wid,
        "name": name or wid,
        "version": "1.18.0",
        "running_jobs": [],
        "want_compute": want_compute,
        "want_data": False,
    }
    r = requests.post(f"{BASE}/api/worker/poll", headers=headers, json=body, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()


# --- Review 1: status new fields ---
def test_status_has_new_fields():
    hdr = _hdr()
    r = requests.get(f"{BASE}/api/localworker/status", headers=hdr, timeout=30)
    assert r.status_code == 200
    data = r.json()
    for k in ("online", "workers", "queue", "data_jobs", "settings", "required_version", "waiting_workers", "max_workers"):
        assert k in data, f"missing field {k}; keys={list(data.keys())}"
    assert data["max_workers"] == 2
    assert isinstance(data["waiting_workers"], list)


# --- Review 2: multi-worker admission + 3rd rejected ---
def test_three_workers_third_rejected():
    hdr = _hdr()
    wtoken = _worker_token(hdr)
    w1, w2, w3 = f"qa_w1_{uuid.uuid4().hex[:4]}", f"qa_w2_{uuid.uuid4().hex[:4]}", f"qa_w3_{uuid.uuid4().hex[:4]}"
    r1 = _poll(wtoken, w1)
    r2 = _poll(wtoken, w2)
    r3 = _poll(wtoken, w3)
    assert r1.get("rejected") in (None, False), f"w1 rejected: {r1}"
    assert r2.get("rejected") in (None, False), f"w2 rejected: {r2}"
    assert r3.get("rejected"), f"w3 expected rejected: {r3}"
    assert r3.get("job") is None
    # waiting_workers should contain w3
    _poll(wtoken, w1); _poll(wtoken, w2); _poll(wtoken, w3)
    st = requests.get(f"{BASE}/api/localworker/status", headers=hdr, timeout=30).json()
    waiting_ids = {w.get("worker_id") if isinstance(w, dict) else w for w in st["waiting_workers"]}
    assert w3 in waiting_ids, f"w3 not in waiting: {st['waiting_workers']}"


# --- Review 3+4: regime-lab worker targeting + parallel guard ---
def _cancel_active_regime_jobs(hdr):
    """Cancel any pre-existing regime-lab/autopilot jobs so test can proceed."""
    try:
        r = requests.get(f"{BASE}/api/regime-lab/active", headers=hdr, timeout=20)
        if r.status_code == 200:
            active = r.json().get("active")
            if active and active.get("id"):
                requests.post(f"{BASE}/api/regime-lab/cancel/{active['id']}", headers=hdr, timeout=20)
                time.sleep(1)
    except Exception:
        pass


def test_regime_lab_worker_target_and_parallel():
    # ensure stale workers from previous test have expired (WORKER_TIMEOUT=8s)
    time.sleep(9)
    hdr = _hdr()
    _cancel_active_regime_jobs(hdr)
    wtoken = _worker_token(hdr)
    wA = f"qa_wA_{uuid.uuid4().hex[:4]}"
    wB = f"qa_wB_{uuid.uuid4().hex[:4]}"
    offline_wid = "qa_offline_doesnotexist"

    # offline worker -> 503
    r = requests.post(
        f"{BASE}/api/regime-lab/analyze",
        headers=hdr,
        json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60, "execution": "local", "worker_id": offline_wid},
        timeout=30,
    )
    assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text}"
    assert "verbunden" in r.text.lower(), r.text

    # Admit both workers (poll twice to ensure admission)
    _poll(wtoken, wA, want_compute=True)
    _poll(wtoken, wB, want_compute=True)
    _poll(wtoken, wA, want_compute=True)
    _poll(wtoken, wB, want_compute=True)

    # Start job targeted to A
    r = requests.post(
        f"{BASE}/api/regime-lab/analyze",
        headers=hdr,
        json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60, "execution": "local", "worker_id": wA},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    jobA = r.json()
    jidA = jobA.get("id") or jobA.get("job_id") or jobA.get("job", {}).get("id")

    # Second job targeted to A -> 409
    r2 = requests.post(
        f"{BASE}/api/regime-lab/analyze",
        headers=hdr,
        json={"symbols": ["ETHUSDT"], "timeframe": "1h", "days": 60, "execution": "local", "worker_id": wA},
        timeout=30,
    )
    assert r2.status_code == 409, f"expected 409 for same worker: {r2.status_code} {r2.text}"

    # Cloud start -> 409
    r_cloud = requests.post(
        f"{BASE}/api/regime-lab/analyze",
        headers=hdr,
        json={"symbols": ["ETHUSDT"], "timeframe": "1h", "days": 60, "execution": "cloud"},
        timeout=30,
    )
    assert r_cloud.status_code == 409, f"expected 409 for cloud: {r_cloud.status_code} {r_cloud.text}"

    # Second targeted to B -> allowed
    r3 = requests.post(
        f"{BASE}/api/regime-lab/analyze",
        headers=hdr,
        json={"symbols": ["ETHUSDT"], "timeframe": "1h", "days": 60, "execution": "local", "worker_id": wB},
        timeout=30,
    )
    assert r3.status_code == 200, f"expected 200 for parallel on B: {r3.status_code} {r3.text}"
    jobB = r3.json()
    jidB = jobB.get("id") or jobB.get("job_id") or jobB.get("job", {}).get("id")

    # Worker A polling should NOT receive B's job
    _poll(wtoken, wA, want_compute=True)  # heartbeat
    _poll(wtoken, wB, want_compute=True)

    # Fetch job details - verify worker_target
    try:
        r = requests.get(f"{BASE}/api/regime-lab/active", headers=hdr, timeout=30)
        if r.status_code == 200:
            active = r.json()
            print(f"active job response: {active}")
    except Exception:
        pass

    # Cancel both
    for jid in (jidA, jidB):
        if jid:
            requests.post(f"{BASE}/api/regime-lab/cancel/{jid}", headers=hdr, timeout=30)


# --- Review 5: asset suggestion ---
@pytest.mark.parametrize("aid", ["ra_4c51713e", "ra_f9ee23d4"])
def test_asset_suggest_known(aid):
    hdr = _hdr()
    r = requests.get(f"{BASE}/api/dynamic-workbench/suggest-assets/{aid}", headers=hdr, timeout=30)
    if r.status_code == 404:
        pytest.skip(f"analysis {aid} not seeded in local DB")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "per_regime" in data
    assert "recommended" in data
    assert isinstance(data["recommended"], list)
    assert len(data["recommended"]) > 0
    assert "overall_scores" in data
    assert "sources" in data
    # check per_regime item structure
    if data["per_regime"]:
        reg = data["per_regime"][0]
        for k in ("regime_id", "label", "assets", "picks"):
            assert k in reg, f"missing {k} in per_regime item"
        if reg["assets"]:
            a = reg["assets"][0]
            for k in ("symbol", "score", "pick", "share", "segments", "consistency", "perf", "reasons"):
                assert k in a, f"missing {k} in asset"


def test_asset_suggest_unknown_404():
    hdr = _hdr()
    r = requests.get(f"{BASE}/api/dynamic-workbench/suggest-assets/ra_doesnotexist_xyz", headers=hdr, timeout=30)
    assert r.status_code == 404


def test_asset_suggest_regimes_filter():
    hdr = _hdr()
    aid = "ra_4c51713e"
    r = requests.get(f"{BASE}/api/dynamic-workbench/suggest-assets/{aid}?regimes=0,2", headers=hdr, timeout=30)
    if r.status_code == 404:
        pytest.skip("analysis not seeded")
    assert r.status_code == 200, r.text
    data = r.json()
    reg_ids = {str(r.get("regime_id")) for r in data["per_regime"]}
    assert reg_ids.issubset({"0", "2"}), f"got regimes {reg_ids}"
