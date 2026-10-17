"""Iter46 live API tests: regime history import + dynamic backup/import/duplicate."""
import os
import json
import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://autopilot-staging-5.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_token():
    last = None
    for i in range(5):
        try:
            r = requests.post(f"{BASE}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=60)
            if r.status_code == 200:
                tok = r.json().get("token") or r.json().get("access_token")
                assert tok
                return tok
            last = f"{r.status_code}"
        except Exception as e:
            last = str(e)
        import time; time.sleep(2 + i)
    pytest.fail(f"login failed after retries: {last}")


@pytest.fixture(scope="module")
def hdr(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# --- Autopilot history import ---
def test_autopilot_runs_contains_imported_and_normal(hdr):
    r = requests.get(f"{BASE}/api/regime-lab/autopilot/runs", params={"limit": 60}, headers=hdr, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    runs = data if isinstance(data, list) else data.get("runs", [])
    ids = [x.get("id") or x.get("run_id") for x in runs]
    print("RUN IDS:", ids[:20])
    assert "imp_ra_e866c510" in ids
    assert "imp_ra_f9ee23d4" in ids
    assert "ca10c03bdd5e" in ids
    assert "d55aad985cdb" in ids
    imp = next(x for x in runs if (x.get("id") or x.get("run_id")) == "imp_ra_e866c510")
    result = imp.get("result") or {}
    assert result.get("source") == "import"
    ifrom = result.get("imported_from") or {}
    assert ifrom.get("name") == "9 Regime Krypto 1h Beste Referenzz"
    assert ifrom.get("grade") == "sehr gut"
    assert ifrom.get("regimes") == 9


def test_autopilot_import_idempotent(hdr):
    r = requests.post(f"{BASE}/api/regime-lab/autopilot/runs/import", headers=hdr, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("imported", -1) == 0, body


# --- Dynamic export ---
def test_dynamic_export(hdr):
    r = requests.get(f"{BASE}/api/dynamic/dyn_bf7d3517/export", headers=hdr, timeout=60)
    assert r.status_code == 200, r.text[:400]
    bundle = r.json()
    assert bundle.get("type") == "dynamic_strategy_backup"
    dyn = bundle.get("dynamic") or {}
    assert "last_state" not in dyn
    assert "pending_switch" not in dyn
    assert (bundle.get("analysis") or {}).get("id") == "ra_e866c510"
    assert isinstance(bundle.get("custom_strategies"), list)
    assert isinstance(bundle.get("strategy_coin_configs"), dict)
    # keep bundle for next tests
    with open("/tmp/dyn_export.json", "w") as f:
        json.dump(bundle, f)


def test_dynamic_import_invalid_body(hdr):
    r = requests.post(f"{BASE}/api/dynamic/import", json={"type": "not_a_backup"}, headers=hdr, timeout=30)
    assert r.status_code == 400, r.text


def test_dynamic_import_requires_admin():
    with open("/tmp/dyn_export.json") as f:
        bundle = json.load(f)
    r = requests.post(f"{BASE}/api/dynamic/import", json=bundle, timeout=30)
    assert r.status_code in (401, 403), r.status_code


_created_import_id = {"id": None}


def test_dynamic_import_creates_new(hdr):
    with open("/tmp/dyn_export.json") as f:
        bundle = json.load(f)
    r = requests.post(f"{BASE}/api/dynamic/import", json=bundle, headers=hdr, timeout=60)
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    new_id = data.get("id") or (data.get("dynamic") or {}).get("id")
    assert new_id and new_id != "dyn_bf7d3517"
    _created_import_id["id"] = new_id
    name = data.get("name") or (data.get("dynamic") or {}).get("name") or ""
    assert "(Import)" in name, name
    assert data.get("analysis") == "exists"

    # Verify via list endpoint
    lst = requests.get(f"{BASE}/api/dynamic/list", headers=hdr, timeout=30).json()
    items = lst.get("strategies") if isinstance(lst, dict) else lst
    item = next((x for x in items if x.get("id") == new_id), None)
    assert item is not None, new_id
    settings = item.get("settings") or {}
    assert settings.get("auto_check_enabled") is False, settings
    assert settings.get("auto_apply_enabled") is False, settings
    assert settings.get("analysis_id") == "ra_e866c510", settings


_dup_id = {"id": None}


def test_dynamic_duplicate(hdr):
    r = requests.post(
        f"{BASE}/api/dynamic/dyn_bf7d3517/duplicate",
        json={"name": "TEST_copy"},
        headers=hdr,
        timeout=60,
    )
    assert r.status_code == 200, r.text[:400]
    data = r.json()
    new_id = data.get("id") or (data.get("dynamic") or {}).get("id")
    assert new_id and new_id != "dyn_bf7d3517"
    _dup_id["id"] = new_id
    assert data.get("analysis") == "exists"
    # Verify analysis_id via list
    lst = requests.get(f"{BASE}/api/dynamic/list", headers=hdr, timeout=30).json()
    items = lst.get("strategies") if isinstance(lst, dict) else lst
    item = next((x for x in items if x.get("id") == new_id), None)
    assert item is not None
    assert (item.get("settings") or {}).get("analysis_id") == "ra_e866c510"


def test_cleanup(hdr):
    for key in ("id",):
        for store in (_created_import_id, _dup_id):
            did = store.get(key)
            if did:
                requests.delete(f"{BASE}/api/dynamic/{did}", headers=hdr, timeout=20)
