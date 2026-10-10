"""Iteration 17 – Regression after ai_engine split + regime_core facade.

Live API smoke tests (marked 'live') covering:
- core AI endpoints (status/lessons/proposals/settings/insights)
- governance path (POST /api/ai/config correlation_guard toggle via update_config)
- lesson path (create -> park -> reactivate -> delete via lesson_store)
"""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(client):
    for path in ("/api/auth/login", "/api/admin/login"):
        r = client.post(f"{BASE_URL}{path}", json={"username": ADMIN_USER, "password": ADMIN_PASS})
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("access_token")
            if tok:
                return tok
    pytest.fail("admin login failed on /api/auth/login and /api/admin/login")


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# --- Kern-API-Smoke -------------------------------------------------------
def test_ai_status(client):
    r = client.get(f"{BASE_URL}/api/ai/status", timeout=60)
    assert r.status_code == 200, r.text[:400]
    d = r.json()
    assert "config" in d and "enabled" in d["config"], list(d.keys())
    assert "providers_health" in d, list(d.keys())


@pytest.mark.parametrize("path", [
    "/api/ai/lessons",
    "/api/ai/proposals",
    "/api/settings",
    "/api/ai/insights",
])
def test_core_get_endpoints(client, path):
    r = client.get(f"{BASE_URL}{path}", timeout=60)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:300]}"
    assert isinstance(r.json(), (dict, list))


# --- Governance-Pfad (update_config im Kern) ------------------------------
def test_correlation_guard_toggle_persists(client, auth):
    try:
        r = client.post(f"{BASE_URL}/api/ai/config", json={"correlation_guard": False}, headers=auth, timeout=60)
        assert r.status_code == 200, r.text[:400]
        g = client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()
        assert g["config"]["correlation_guard"] is False, g["config"].get("correlation_guard")
    finally:
        r2 = client.post(f"{BASE_URL}/api/ai/config", json={"correlation_guard": True}, headers=auth, timeout=60)
        assert r2.status_code == 200, r2.text[:300]
        g2 = client.get(f"{BASE_URL}/api/ai/status", timeout=60).json()
        assert g2["config"]["correlation_guard"] is True


# --- Lessons-Pfad ---------------------------------------------------------
def test_lesson_lifecycle(client, auth):
    payload = {
        "title": "TEST_iter17 Regression",
        "detail": "Bei ADX < 15 keine Trendtrades eingehen.",
        "weight": 3,
    }
    r = client.post(f"{BASE_URL}/api/ai/lessons", json=payload, headers=auth, timeout=60)
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    lid = data.get("id") or (data.get("lesson") or {}).get("id")
    assert lid, data
    try:
        listing = client.get(f"{BASE_URL}/api/ai/lessons", timeout=60).json()
        items = listing if isinstance(listing, list) else listing.get("lessons", [])
        assert any((it.get("id") == lid) for it in items), "created lesson not in listing"

        rp = client.post(f"{BASE_URL}/api/ai/lessons/{lid}/park", headers=auth, timeout=60)
        assert rp.status_code == 200, rp.text[:300]
        rr = client.post(f"{BASE_URL}/api/ai/lessons/{lid}/reactivate", headers=auth, timeout=60)
        assert rr.status_code == 200, rr.text[:300]
    finally:
        rd = client.delete(f"{BASE_URL}/api/ai/lessons/{lid}", headers=auth, timeout=60)
        assert rd.status_code == 200, rd.text[:300]
        listing = client.get(f"{BASE_URL}/api/ai/lessons", timeout=60).json()
        items = listing if isinstance(listing, list) else listing.get("lessons", [])
        assert not any((it.get("id") == lid) for it in items), "lesson still present after delete"


# --- Proposals decide with unknown id -> clean 404 -----------------------
def test_proposal_decide_unknown_id(client, auth):
    r = client.post(f"{BASE_URL}/api/ai/proposals/does-not-exist-iter17",
                    json={"action": "reject"}, headers=auth, timeout=60)
    assert r.status_code == 404, f"{r.status_code} {r.text[:300]}"


def test_proposals_actionable(client):
    r = client.get(f"{BASE_URL}/api/ai/proposals/actionable", timeout=60)
    assert r.status_code == 200, r.text[:300]
    assert "proposals" in r.json()
