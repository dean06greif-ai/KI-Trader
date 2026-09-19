"""AP10/AP11 tests: regime-lab list schema, localworker package/manifest/status, auth."""
import io
import os
import re
import zipfile

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_USER = os.environ.get("TEST_ADMIN_USER", "Admin")
ADMIN_PASS = os.environ.get("TEST_ADMIN_PASSWORD", "Dean06Greif!/Admin")


def _backend_reachable() -> bool:
    if not BASE_URL:
        return False
    try:
        return requests.get(f"{BASE_URL}/api/health", timeout=5).status_code == 200
    except requests.RequestException:
        return False


pytestmark = pytest.mark.skipif(
    not _backend_reachable(),
    reason="Backend nicht erreichbar (REACT_APP_BACKEND_URL) – E2E übersprungen")


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(api):
    r = api.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth(api, token):
    api.headers.update({"Authorization": f"Bearer {token}"})
    return api


# BACKEND 1: regime-lab list schema
def test_regime_lab_list(auth):
    r = auth.get(f"{BASE_URL}/api/regime-lab/list")
    assert r.status_code == 200, r.text
    data = r.json()
    assert "analyses" in data
    for row in data["analyses"]:
        for k in ("walkforward_passed", "walkforward_stale", "dataset_status"):
            assert k in row, f"missing field {k} in row {row.get('id')}"


# BACKEND 2: localworker package manifest
def test_localworker_manifest(auth):
    r = auth.get(f"{BASE_URL}/api/localworker/package/manifest")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m.get("required_version") == "1.11.0"
    assert m.get("complete") is True
    assert m.get("commit")
    assert m.get("code_fingerprint")
    fh = m.get("file_hashes") or {}
    assert fh, "file_hashes empty"
    hex16 = re.compile(r"^[0-9a-fA-F]{16}$")
    for k, v in fh.items():
        assert hex16.match(v), f"hash not 16-hex for {k}: {v}"
        assert ".env" not in k, f"forbidden key: {k}"
        assert "worker_config" not in k, f"forbidden key: {k}"
    assert any(k.startswith("services/setup_backtest/") for k in fh.keys()), "no services/setup_backtest/ keys"


# BACKEND 3: localworker package zip
def test_localworker_package_zip(auth):
    r = auth.get(f"{BASE_URL}/api/localworker/package")
    assert r.status_code == 200, r.text
    ct = r.headers.get("Content-Type", "")
    assert "application/zip" in ct, f"content-type={ct}"
    assert len(r.content) > 100 * 1024, f"zip too small: {len(r.content)}"
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    assert any(n.endswith("worker.py") or n == "worker.py" for n in names)
    assert any(n.endswith("PACKAGE_INFO.json") for n in names)
    assert any("services/setup_backtest/" in n for n in names)
    assert any(n.endswith("services/__init__.py") for n in names)
    assert not any(".env" in n for n in names), f"env file present: {[n for n in names if '.env' in n]}"


# BACKEND 4: localworker status
def test_localworker_status(auth):
    r = auth.get(f"{BASE_URL}/api/localworker/status")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data.get("required_version") == "1.11.0"


# BACKEND 5: auth protection
def test_protected_without_token():
    r = requests.get(f"{BASE_URL}/api/localworker/token")
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}"
