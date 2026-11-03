"""E2E integration tests for audit-fix iter8 (release/apply/confirm gates + evidence).

Runs against the live backend via REACT_APP_BACKEND_URL.
"""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback for offline harness
    BASE_URL = "http://localhost:8001"

ADMIN_USER = "Admin"
ADMIN_PW = "Dean06Greif!/Admin"


# ---------- fixtures ----------
@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PW},
                      timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok, f"no token in response: {data}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


@pytest.fixture(scope="module")
def draft_id(auth_headers):
    body = {
        "strategy_id": "scalping_4_rules",
        "model": {"regimes": [{"id": 0, "label": "Trend"}]},
        "configs": {"0": {}},
        "symbols": ["BTCUSDT"],
    }
    r = requests.post(f"{BASE_URL}/api/dynamic/save",
                      json=body, headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"save failed: {r.status_code} {r.text[:300]}"
    data = r.json()
    did = data.get("id") or (data.get("strategy") or {}).get("id")
    assert did, f"no id: {data}"
    yield did
    # cleanup
    try:
        requests.delete(f"{BASE_URL}/api/dynamic/{did}", headers=auth_headers, timeout=15)
    except Exception:
        pass


# ---------- tests ----------
def test_health():
    # allow warm-up retries
    last = None
    for _ in range(3):
        try:
            r = requests.get(f"{BASE_URL}/api/health", timeout=60)
            last = r
            if r.status_code == 200:
                return
        except requests.RequestException as e:
            last = e
    assert False, f"health failed: {last}"


def test_dynamic_list(auth_headers):
    r = requests.get(f"{BASE_URL}/api/dynamic/list", headers=auth_headers, timeout=15)
    assert r.status_code == 200


def test_approve_without_evidence_returns_409(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/approve",
                      json={}, headers=auth_headers, timeout=20)
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    detail = str(body.get("detail") or body).lower()
    assert "testnachweis" in detail or "evidence" in detail, f"unexpected detail: {body}"


def test_approve_override_without_note_returns_400(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/approve",
                      json={"override_missing_evidence": True},
                      headers=auth_headers, timeout=20)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"


def test_apply_before_approve_returns_409(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/apply",
                      json={}, headers=auth_headers, timeout=20)
    assert r.status_code == 409, f"expected 409, got {r.status_code}: {r.text[:300]}"


def test_approve_with_override_and_note_ok(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/approve",
                      json={"override_missing_evidence": True, "note": "Test"},
                      headers=auth_headers, timeout=30)
    assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    release = body.get("release") or body.get("strategy", {}).get("release") or {}
    assert release.get("approved_without_evidence") is True, f"release: {release}"


def test_apply_after_approve_not_409(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/apply",
                      json={}, headers=auth_headers, timeout=20)
    # After release: 200 or 400 (missing last_state / regime) allowed, but NOT 409
    assert r.status_code != 409, f"still 409 after approve: {r.text[:300]}"
    assert r.status_code in (200, 400), f"unexpected {r.status_code}: {r.text[:300]}"


def test_confirm_without_open_switch_returns_400(auth_headers, draft_id):
    r = requests.post(f"{BASE_URL}/api/dynamic/{draft_id}/confirm",
                      json={}, headers=auth_headers, timeout=20)
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"
    body = r.json()
    detail = str(body.get("detail") or body).lower()
    assert "kein offener" in detail or "regime-wechsel" in detail or "no pending" in detail, body


def test_evidence_bundle(auth_headers, draft_id):
    r = requests.get(f"{BASE_URL}/api/dynamic/{draft_id}/evidence",
                     headers=auth_headers, timeout=20)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    body = r.json()
    ev = body.get("evidence") or body
    blockers = ev.get("blockers")
    warnings = ev.get("warnings")
    runtime_health = ev.get("runtime_health") or {}
    assert isinstance(blockers, list), f"blockers not list: {blockers}"
    assert isinstance(warnings, list), f"warnings not list: {warnings}"
    joined = " ".join(str(b) for b in blockers).lower()
    assert "ohne testnachweis" in joined or "without evidence" in joined or \
           any("testnachweis" in str(b).lower() for b in blockers), f"blockers: {blockers}"
    lvl = runtime_health.get("level")
    assert lvl in ("ok", "warning", "critical", "unknown"), f"runtime_health.level: {runtime_health}"
    assert isinstance(ev.get("ready_for_live"), bool), f"ready_for_live: {ev.get('ready_for_live')}"


def test_delete_archives(auth_headers, draft_id):
    r = requests.delete(f"{BASE_URL}/api/dynamic/{draft_id}",
                        headers=auth_headers, timeout=20)
    assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
    body = r.json()
    assert body.get("archived") is True, f"archived flag missing: {body}"
