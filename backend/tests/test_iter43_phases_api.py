"""Iteration 43 - API tests for Dynamic phases/versions, workbench pause/resume/reset,
regime-lab active filtering, and dynamic backtest local worker guards.

Runs against the public preview URL. Uses seed dyn_seed1 in local test_database.
"""
import os
import requests
import pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://trader-refactor-5.preview.emergentagent.com").rstrip("/")
ADMIN = {"username": "Admin", "password": "PreviewAdmin123!"}
DYN_ID = "dyn_seed1"


@pytest.fixture(scope="module")
def token():
    last = None
    for _ in range(4):
        try:
            r = requests.post(f"{BASE}/api/auth/login", json=ADMIN, timeout=60)
            if r.status_code == 200:
                return r.json()["token"]
            last = r.text
        except Exception as e:
            last = str(e)
    pytest.fail(f"login failed: {last}")


@pytest.fixture(scope="module")
def H(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------- phases endpoint ----------
def test_phases_shape(H):
    r = requests.get(f"{BASE}/api/dynamic/{DYN_ID}/phases", headers=H, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "phases" in body and isinstance(body["phases"], list) and len(body["phases"]) >= 3
    assert "release_status" in body
    assert "versions" in body and isinstance(body["versions"], int)
    for p in body["phases"]:
        for key in ("regime", "traded", "strategy_name", "optimized", "skip_reason"):
            assert key in p, f"missing {key} in {p}"


# ---------- POST /phase guard rails ----------
def test_phase_requires_confirm(H):
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/phase", headers=H,
                      json={"regime_id": 0, "action": "skip"}, timeout=15)
    assert r.status_code == 400
    assert "confirm" in r.text.lower()


def test_phase_invalid_regime(H):
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/phase", headers=H,
                      json={"regime_id": 99, "action": "skip", "confirm": True}, timeout=15)
    assert r.status_code == 400


def test_phase_strategy_must_be_non_dynamic(H):
    # pass a bogus id that isn't a non-dynamic registry entry
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/phase", headers=H,
                      json={"regime_id": 0, "action": "strategy", "strategy_id": "does_not_exist",
                            "confirm": True}, timeout=15)
    assert r.status_code == 400


def test_phase_skip_and_version_increment(H):
    v0 = requests.get(f"{BASE}/api/dynamic/{DYN_ID}/versions", headers=H, timeout=15).json()
    count_before = len(v0.get("versions", []))
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/phase", headers=H,
                      json={"regime_id": 1, "action": "skip", "confirm": True,
                            "keep_release": True}, timeout=15)
    assert r.status_code == 200, r.text
    body = r.json()
    assert "version" in body
    v1 = requests.get(f"{BASE}/api/dynamic/{DYN_ID}/versions", headers=H, timeout=15).json()
    assert len(v1.get("versions", [])) == count_before + 1


def test_phase_strategy_action_with_valid_registry(H):
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/phase", headers=H,
                      json={"regime_id": 0, "action": "strategy", "strategy_id": "nnfx_trend",
                            "confirm": True, "keep_release": True}, timeout=15)
    assert r.status_code == 200, r.text


# ---------- versions / restore ----------
def test_versions_list_no_snapshot(H):
    r = requests.get(f"{BASE}/api/dynamic/{DYN_ID}/versions", headers=H, timeout=15)
    assert r.status_code == 200
    versions = r.json().get("versions", [])
    assert len(versions) >= 1
    # snapshot must not be in list listing (keep small)
    for v in versions:
        assert "snapshot" not in v or v.get("snapshot") in (None, {}, [])


def test_restore_v1_requires_confirm(H):
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/versions/1/restore", headers=H,
                      json={}, timeout=15)
    assert r.status_code == 400


def test_restore_v1_success(H):
    r = requests.post(f"{BASE}/api/dynamic/{DYN_ID}/versions/1/restore", headers=H,
                      json={"confirm": True}, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("version")


# ---------- workbench pause/resume/reset ----------
def test_workbench_pause_unknown_404(H):
    r = requests.post(f"{BASE}/api/dynamic-workbench/pause/does-not-exist", headers=H,
                      json={}, timeout=10)
    assert r.status_code == 404


def test_workbench_resume_unknown_404(H):
    r = requests.post(f"{BASE}/api/dynamic-workbench/resume/does-not-exist", headers=H,
                      json={}, timeout=10)
    assert r.status_code == 404


def test_workbench_reset(H):
    r = requests.post(f"{BASE}/api/dynamic-workbench/reset", headers=H, json={}, timeout=10)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("status") == "reset"
    assert "cleared" in body


# ---------- regime-lab active filtering ----------
def test_regime_lab_active_no_workbench_sub(H):
    r = requests.get(f"{BASE}/api/regime-lab/active", headers=H, timeout=10)
    assert r.status_code == 200
    body = r.json()
    # whatever shape, ensure no workbench_job param surfaces
    import json
    text = json.dumps(body)
    assert "workbench_job" not in text


# ---------- dynamic backtest local worker guard ----------
def test_dynamic_backtest_local_not_cloud_error(H):
    payload = {
        "strategy_id": DYN_ID,
        "symbol": "EURUSD",
        "timeframe": "H1",
        "execution": "local",
    }
    r = requests.post(f"{BASE}/api/backtest/run", headers=H, json=payload, timeout=30)
    # Must NOT be the old 400 "nur in der Cloud"
    body_text = r.text.lower()
    assert "nur in der cloud" not in body_text
    # expected: 503 (no worker) or 409 (old worker) or 200/202 if a worker exists
    assert r.status_code in (200, 202, 400, 409, 503), f"unexpected {r.status_code}: {r.text[:200]}"
    if r.status_code == 503:
        assert "lokaler worker" in body_text or "worker" in body_text
