"""Iteration 5 API tests: backup endpoints, ai config entry_mode, retention policy."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ki-enhance.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


# ---------------------------------------------------------------- Backup
class TestBackup:
    def test_backup_status(self):
        r = requests.get(f"{BASE_URL}/api/maintenance/backup", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("configured") is True
        assert data.get("bucket") == "mongo-backups"
        assert data.get("enabled") in (True, False)  # exposed
        assert data.get("retention_days") == 30
        colls = data.get("collections") or []
        assert "settings" in colls and "auto_trades" in colls
        assert isinstance(data.get("last_run"), dict)

    def test_backup_list_has_gz(self):
        r = requests.get(f"{BASE_URL}/api/maintenance/backup/list", timeout=60)
        assert r.status_code == 200, r.text
        files = r.json().get("files") or []
        names = [f.get("name") if isinstance(f, dict) else f for f in files]
        assert any(str(n).startswith("backup_") and str(n).endswith(".json.gz") for n in names), names

    def test_backup_run_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/maintenance/backup/run", timeout=30)
        assert r.status_code in (401, 403), r.status_code

    def test_backup_run_with_admin(self, admin_headers):
        r_before = requests.get(f"{BASE_URL}/api/maintenance/backup/list", timeout=60)
        files_before = {(f.get("name") if isinstance(f, dict) else f) for f in (r_before.json().get("files") or [])}

        r = requests.post(f"{BASE_URL}/api/maintenance/backup/run", headers=admin_headers, timeout=180)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "ok", data
        name = data.get("file") or data.get("name")
        assert name and name.startswith("backup_") and name.endswith(".json.gz"), data
        assert (data.get("docs") or 0) >= 0  # empty DB allowed
        assert not (data.get("errors") or [])

        time.sleep(1)
        r_after = requests.get(f"{BASE_URL}/api/maintenance/backup/list", timeout=60)
        files_after = {(f.get("name") if isinstance(f, dict) else f) for f in (r_after.json().get("files") or [])}
        assert name in files_after, (name, files_after - files_before)

    def test_backup_config_admin(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/maintenance/backup/config", headers=admin_headers, json={"retention_days": 14}, timeout=30)
        assert r.status_code == 200, r.text
        assert r.json().get("retention_days") == 14

        # reset
        r2 = requests.post(f"{BASE_URL}/api/maintenance/backup/config", headers=admin_headers, json={"retention_days": 30}, timeout=30)
        assert r2.status_code == 200
        assert r2.json().get("retention_days") == 30

    def test_backup_config_invalid(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/maintenance/backup/config", headers=admin_headers, json={}, timeout=30)
        assert r.status_code == 400, r.text
        r2 = requests.post(f"{BASE_URL}/api/maintenance/backup/config", headers=admin_headers, json={"collections": []}, timeout=30)
        assert r2.status_code == 400, r2.text

    def test_backup_restore_dry_run_and_apply(self, admin_headers):
        r_list = requests.get(f"{BASE_URL}/api/maintenance/backup/list", timeout=60)
        files = r_list.json().get("files") or []
        names = [f.get("name") if isinstance(f, dict) else f for f in files]
        gz = [n for n in names if str(n).startswith("backup_") and str(n).endswith(".json.gz")]
        assert gz, names
        fname = gz[0]

        r = requests.post(f"{BASE_URL}/api/maintenance/backup/restore", headers=admin_headers, json={"file": fname}, timeout=120)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("status") == "dry_run", d
        assert isinstance(d.get("collections") or d.get("counts") or {}, dict)

        r2 = requests.post(f"{BASE_URL}/api/maintenance/backup/restore", headers=admin_headers,
                           json={"file": fname, "collections": ["custom_strategies"], "apply": True}, timeout=180)
        assert r2.status_code == 200, r2.text
        d2 = r2.json()
        assert d2.get("status") == "restored", d2

    def test_backup_restore_traversal_rejected(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/maintenance/backup/restore", headers=admin_headers, json={"file": "../evil"}, timeout=30)
        assert r.status_code == 400, r.text


# ---------------------------------------------------------------- Retention
def test_retention_policy_tightened():
    r = requests.get(f"{BASE_URL}/api/maintenance/retention", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    policy = data.get("policy") or data.get("rules") or data
    if isinstance(policy, list):
        by_coll = {row.get("coll") or row.get("collection"): row for row in policy}
    else:
        by_coll = policy
    assert by_coll["ai_decisions"]["days"] == 14
    assert by_coll["ai_chat_archive"]["days"] == 30
    assert by_coll["job_series"]["days"] == 60


# ---------------------------------------------------------------- Entry-Modus
class TestEntryMode:
    def _get_config(self, admin_headers):
        # try status endpoint, fallback to ai/config GET
        for path in ("/api/ai/status", "/api/ai/config"):
            r = requests.get(f"{BASE_URL}{path}", headers=admin_headers, timeout=30)
            if r.status_code == 200:
                j = r.json()
                cfg = j.get("config") or j
                if "entry_mode" in cfg:
                    return cfg
        pytest.fail("no config with entry_mode found")

    def test_set_conservative(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/ai/config", headers=admin_headers, json={"entry_mode": "conservative"}, timeout=30)
        assert r.status_code == 200, r.text
        cfg = r.json().get("config") or r.json()
        assert cfg.get("entry_mode") == "conservative", cfg

    def test_set_aggressive(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/ai/config", headers=admin_headers, json={"entry_mode": "aggressive"}, timeout=30)
        assert r.status_code == 200, r.text
        cfg = r.json().get("config") or r.json()
        assert cfg.get("entry_mode") == "aggressive", cfg

    def test_invalid_falls_back_to_ai(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/ai/config", headers=admin_headers, json={"entry_mode": "invalid"}, timeout=30)
        assert r.status_code == 200, r.text
        cfg = r.json().get("config") or r.json()
        assert cfg.get("entry_mode") == "ai", cfg

    def test_reset_to_ai(self, admin_headers):
        r = requests.post(f"{BASE_URL}/api/ai/config", headers=admin_headers, json={"entry_mode": "ai"}, timeout=30)
        assert r.status_code == 200, r.text
        cfg = r.json().get("config") or r.json()
        assert cfg.get("entry_mode") == "ai"
        # persisted?
        cfg2 = self._get_config(admin_headers)
        assert cfg2.get("entry_mode") == "ai"
