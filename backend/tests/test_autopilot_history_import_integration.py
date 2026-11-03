"""Integration-Test gegen den laufenden Backend-Service für den neuen
POST /api/regime-lab/autopilot/runs/import Endpoint und zugehörige Checks."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrader-autopilot-1.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "LocalTest123!"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"Login failed: {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("bearer")
    assert tok, f"No token in login response: {data}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


class TestImportEndpoint:
    def test_import_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/import", timeout=20)
        assert r.status_code in (401, 403), f"Expected 401/403 without token, got {r.status_code}"

    def test_import_first_call_returns_counts(self, auth_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/import",
                          headers=auth_headers, timeout=30)
        assert r.status_code == 200, f"{r.status_code} {r.text}"
        data = r.json()
        for key in ("imported", "already", "skipped", "names"):
            assert key in data, f"Missing key {key} in {data}"
        assert isinstance(data["imported"], int)
        assert isinstance(data["already"], int)
        assert isinstance(data["skipped"], int)
        assert isinstance(data["names"], list)
        print(f"First import: imported={data['imported']} already={data['already']} skipped={data['skipped']}")
        # store for next test via class attribute
        TestImportEndpoint._first = data

    def test_import_second_call_is_idempotent(self, auth_headers):
        r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot/runs/import",
                          headers=auth_headers, timeout=30)
        assert r.status_code == 200
        data = r.json()
        assert data["imported"] == 0, f"Second call should import 0, got {data}"
        # already count should be >= first imported count
        first = getattr(TestImportEndpoint, "_first", None)
        if first:
            assert data["already"] >= first["imported"], f"already {data['already']} < first imported {first['imported']}"

    def test_runs_contains_imported_rows(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/regime-lab/autopilot/runs?limit=60",
                         headers=auth_headers, timeout=20)
        assert r.status_code == 200, r.text
        payload = r.json()
        runs = payload.get("runs") or []
        imported = [x for x in runs if str(x.get("id", "")).startswith("imp_")]
        print(f"Total runs: {len(runs)}, imported rows: {len(imported)}")
        # at least one imported row expected unless DB has no analyses
        if imported:
            row = imported[0]
            res = row.get("result") or {}
            assert res.get("source") == "import"
            assert row.get("pinned") is True
            assert (res.get("imported_from") or {}).get("name") is not None or \
                   (res.get("imported_from") or {}).get("id") is not None
            best = res.get("best") or {}
            assert isinstance(best.get("score"), (int, float)), f"score not number: {best.get('score')}"
            assert isinstance(best.get("metrics"), dict)
            # rating/grade annotation
            assert ("rating" in row) or ("rating" in res), f"No rating field on imported row: {list(row.keys())}"

    def test_calibrations_history_excludes_imports(self, auth_headers):
        r = requests.get(f"{BASE_URL}/api/regime-lab/calibrations?limit=60",
                         headers=auth_headers, timeout=20)
        assert r.status_code == 200, r.text
        cals = (r.json() or {}).get("calibrations") or []
        bad = [c for c in cals if str(c.get("id", "")).startswith("imp_") or (c.get("source") == "import")]
        assert not bad, f"Kalibrierungs-Verlauf must not list imported rows, found: {bad[:3]}"

    def test_followup_analysis_not_double_imported(self, auth_headers):
        """Imported rows should uniquely cover one detection key per autopilot run."""
        r = requests.get(f"{BASE_URL}/api/regime-lab/autopilot/runs?limit=60",
                         headers=auth_headers, timeout=20)
        runs = (r.json() or {}).get("runs") or []
        # Each imp_<id> should be unique (no duplicate detection -> no duplicate pin for same analysis)
        imp_ids = [x["id"] for x in runs if str(x.get("id", "")).startswith("imp_")]
        assert len(imp_ids) == len(set(imp_ids)), f"Duplicate imp_ ids: {imp_ids}"
