"""AP04 API flow tests: dynamic strategy release lifecycle (draft/validated/approved),
apply-gate, confirm CAS, approve endpoint, delete=archive.
"""
import os
import requests
import pytest

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_USER = os.environ.get("ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("ADMIN_PASSWORD", "LocalDev06!")


def _backend_reachable() -> bool:
    try:
        return requests.get(f"{API}/health", timeout=5).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _backend_reachable(),
                                reason="Backend nicht erreichbar – E2E übersprungen")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}", "Content-Type": "application/json"}


def test_login_wrong_password():
    r = requests.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": "wrong"}, timeout=30)
    assert r.status_code == 401


def test_health():
    r = requests.get(f"{API}/health", timeout=30)
    assert r.status_code == 200
    data = r.json()
    assert data.get("status") == "alive" or "alive" in str(data).lower()


def test_safety_status(admin_headers):
    # NOTE: endpoint requires auth (401 without) — flag deviation from spec
    r = requests.get(f"{API}/safety/status", headers=admin_headers, timeout=15)
    assert r.status_code == 200


def test_dynamic_list_no_auth():
    r = requests.get(f"{API}/dynamic/list", timeout=10)
    assert r.status_code == 200


# ---- Draft lifecycle ----
DRAFT_ID = {}


def test_save_draft(admin_headers):
    body = {
        "name": "AP04-Test-Draft",
        "strategy_id": "bollinger_reversion",
        "symbols": ["BTCUSDT"],
        "timeframe": "5m",
        "model": {"regimes": [{"id": 0, "label": "Trend"}, {"id": 1, "label": "Range"}]},
        "configs": {"0": {"tp1_crv": 1.5}, "1": {}},
    }
    r = requests.post(f"{API}/dynamic/save", json=body, headers=admin_headers, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "id" in data
    DRAFT_ID["id"] = data["id"]


def test_list_shows_draft(admin_headers):
    r = requests.get(f"{API}/dynamic/list", timeout=10)
    assert r.status_code == 200
    _j = r.json(); items = _j if isinstance(_j, list) else (_j.get("strategies") or _j.get("items") or [])
    match = [i for i in items if i.get("id") == DRAFT_ID["id"]]
    assert match, f"draft {DRAFT_ID['id']} not in list"
    rel = match[0].get("release_status") or (match[0].get("release") or {}).get("status")
    assert rel == "draft", f"expected draft, got {rel}"


def test_apply_draft_409(admin_headers):
    r = requests.post(f"{API}/dynamic/{DRAFT_ID['id']}/apply", json={}, headers=admin_headers, timeout=15)
    assert r.status_code == 409, f"expected 409, got {r.status_code} body={r.text}"
    # German reason
    body = r.text.lower()
    assert "entwurf" in body or "validier" in body


def test_confirm_no_pending(admin_headers):
    r = requests.post(f"{API}/dynamic/{DRAFT_ID['id']}/confirm", json={}, headers=admin_headers, timeout=15)
    assert r.status_code == 400, f"expected 400, got {r.status_code} body={r.text}"
    assert "regime" in r.text.lower() or "wechsel" in r.text.lower()


def test_approve(admin_headers):
    r = requests.post(f"{API}/dynamic/{DRAFT_ID['id']}/approve", json={"note": "test"}, headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    rel = data.get("release_status") or (data.get("release") or {}).get("status")
    assert rel == "approved", f"expected approved, got {rel} data={data}"


def test_list_shows_approved():
    r = requests.get(f"{API}/dynamic/list", timeout=10)
    _j = r.json(); items = _j if isinstance(_j, list) else (_j.get("strategies") or _j.get("items") or [])
    match = [i for i in items if i.get("id") == DRAFT_ID["id"]]
    assert match
    rel = match[0].get("release_status") or (match[0].get("release") or {}).get("status")
    assert rel == "approved"


def test_apply_approved_no_last_state(admin_headers):
    r = requests.post(f"{API}/dynamic/{DRAFT_ID['id']}/apply", json={}, headers=admin_headers, timeout=15)
    # Should NOT be 409 anymore; expect 400 with "Regime aktualisieren"
    assert r.status_code == 400, f"expected 400, got {r.status_code} body={r.text}"
    assert "regime" in r.text.lower()


def test_delete_archives(admin_headers):
    r = requests.delete(f"{API}/dynamic/{DRAFT_ID['id']}", headers=admin_headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("status") == "deleted"
    assert data.get("archived") is True
    assert "unapplied" in data


def test_list_no_deleted():
    r = requests.get(f"{API}/dynamic/list", timeout=10)
    _j = r.json(); items = _j if isinstance(_j, list) else (_j.get("strategies") or _j.get("items") or [])
    assert not [i for i in items if i.get("id") == DRAFT_ID["id"]]


# ---- Validated lifecycle ----
VAL_ID = {}


def test_save_validated(admin_headers):
    body = {
        "name": "AP04-Test-Validated",
        "strategy_id": "bollinger_reversion",
        "symbols": ["BTCUSDT"],
        "timeframe": "5m",
        "model": {"regimes": [{"id": 0, "label": "Trend"}, {"id": 1, "label": "Range"}]},
        "configs": {"0": {"tp1_crv": 1.5}, "1": {}},
        "verdict": {"dynamic_better": True},
    }
    r = requests.post(f"{API}/dynamic/save", json=body, headers=admin_headers, timeout=20)
    assert r.status_code == 200, r.text
    VAL_ID["id"] = r.json()["id"]


def test_validated_status_in_list():
    r = requests.get(f"{API}/dynamic/list", timeout=10)
    _j = r.json(); items = _j if isinstance(_j, list) else (_j.get("strategies") or _j.get("items") or [])
    match = [i for i in items if i.get("id") == VAL_ID["id"]]
    assert match
    rel = match[0].get("release_status") or (match[0].get("release") or {}).get("status")
    assert rel == "validated", f"expected validated, got {rel}"


def test_apply_validated_not_409(admin_headers):
    r = requests.post(f"{API}/dynamic/{VAL_ID['id']}/apply", json={}, headers=admin_headers, timeout=15)
    assert r.status_code != 409, f"validated apply should not be 409, got {r.status_code} body={r.text}"
    # 400 (missing last_state) acceptable
    assert r.status_code in (200, 400), f"unexpected {r.status_code} {r.text}"


def test_cleanup_validated(admin_headers):
    r = requests.delete(f"{API}/dynamic/{VAL_ID['id']}", headers=admin_headers, timeout=15)
    assert r.status_code == 200
