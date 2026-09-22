"""Review-Tests (Testing-Agent): Worker-Token-Selbstheilung E2E gegen laufenden Server.

Deckt ab:
- Admin-Login -> JWT
- GET /api/localworker/token (Admin / ohne Auth -> 401)
- POST /api/worker/poll (gültig / falsch / fehlend)
- SELBSTHEILUNG: Token direkt in Mongo geändert -> Poll mit NEUEM Token = 200
- POST /api/localworker/token/regenerate -> neues Token gültig, altes ungültig
- GET /api/localworker/status (öffentlich)
- GET /api/localworker/package + /manifest
"""
import io
import os
import secrets
import time
import zipfile

import pytest
import requests
from dotenv import dotenv_values
from pymongo import MongoClient

_fe = dotenv_values("/app/frontend/.env")
_be = dotenv_values("/app/backend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _fe.get("REACT_APP_BACKEND_URL")).rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL") or _be.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or _be.get("DB_NAME")
TIMEOUT = 60


@pytest.fixture(scope="module")
def mongo_settings():
    client = MongoClient(MONGO_URL, serverSelectionTimeoutMS=5000)
    yield client[DB_NAME].settings
    client.close()


@pytest.fixture(scope="module")
def admin_jwt():
    r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"})
    assert r.status_code == 200, f"Login fehlgeschlagen: {r.status_code} {r.text[:300]}"
    data = r.json()
    assert isinstance(data.get("token"), str) and data["token"]
    return data["token"]


def _poll(token, worker_id="pytest-review"):
    headers = {"X-Worker-Token": token} if token is not None else {}
    return requests.post(f"{BASE_URL}/api/worker/poll", timeout=TIMEOUT,
                         headers=headers,
                         json={"worker_id": worker_id, "name": "pytest-review",
                               "version": "1.9.0", "want_compute": False,
                               "want_data": False})


def _ui_token(jwt):
    r = requests.get(f"{BASE_URL}/api/localworker/token", timeout=TIMEOUT,
                     headers={"Authorization": f"Bearer {jwt}"})
    assert r.status_code == 200, r.text[:300]
    return r.json()["token"]


class TestTokenEndpoint:
    def test_token_requires_admin(self):
        assert requests.get(f"{BASE_URL}/api/localworker/token",
                            timeout=TIMEOUT).status_code == 401

    def test_token_with_admin(self, admin_jwt):
        tok = _ui_token(admin_jwt)
        assert len(tok) == 48, f"Token-Länge {len(tok)} statt 48 Hex"
        int(tok, 16)  # muss Hex sein


class TestWorkerPoll:
    def test_valid_token(self, admin_jwt):
        r = _poll(_ui_token(admin_jwt))
        assert r.status_code == 200, r.text[:300]
        body = r.json()
        assert set(("job", "cancel_ids", "settings")).issubset(body.keys())
        assert body["job"] is None or isinstance(body["job"], dict)
        assert isinstance(body["cancel_ids"], list)
        assert isinstance(body["settings"], dict)

    def test_wrong_token(self):
        assert _poll("f" * 48).status_code == 401

    def test_missing_header(self):
        assert _poll(None).status_code == 401

    def test_missing_worker_id(self, admin_jwt):
        r = requests.post(f"{BASE_URL}/api/worker/poll", timeout=TIMEOUT,
                          headers={"X-Worker-Token": _ui_token(admin_jwt)}, json={})
        assert r.status_code == 400


class TestSelfHealing:
    """Kern-Fix: Mongo-Token direkt geändert, ohne Server-Neustart."""

    def test_mongo_token_change_is_healed(self, admin_jwt, mongo_settings):
        original = _ui_token(admin_jwt)
        new_token = secrets.token_hex(24)
        try:
            mongo_settings.update_one({"_id": "local_worker_token"},
                                      {"$set": {"token": new_token}}, upsert=True)
            time.sleep(3.5)  # refresh_token ist auf 1x/3s gedrosselt
            r = _poll(new_token, worker_id="pytest-selfheal")
            assert r.status_code == 200, \
                f"Selbstheilung fehlgeschlagen: {r.status_code} {r.text[:300]}"
            # nach Heilung liefert die UI ebenfalls das neue Token
            assert _ui_token(admin_jwt) == new_token
        finally:
            mongo_settings.update_one({"_id": "local_worker_token"},
                                      {"$set": {"token": original}}, upsert=True)
            time.sleep(3.5)
            r2 = _poll(original, worker_id="pytest-selfheal")
            assert r2.status_code == 200, \
                f"Rückheilung fehlgeschlagen: {r2.status_code} {r2.text[:300]}"

    def test_stale_token_still_rejected(self, admin_jwt):
        """Selbstheilung darf kein beliebiges Token akzeptieren."""
        time.sleep(3.5)
        assert _poll(secrets.token_hex(24)).status_code == 401


class TestRegenerate:
    def test_regenerate_rotates_token(self, admin_jwt):
        old = _ui_token(admin_jwt)
        time.sleep(3.5)
        r = requests.post(f"{BASE_URL}/api/localworker/token/regenerate", timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {admin_jwt}"})
        assert r.status_code == 200, r.text[:300]
        new = r.json()["token"]
        assert new and new != old and len(new) == 48
        assert _poll(new, worker_id="pytest-regen").status_code == 200
        time.sleep(3.5)
        assert _poll(old, worker_id="pytest-regen").status_code == 401, \
            "ALTES Token wird weiterhin akzeptiert"
        # UI liefert danach das neue Token
        assert _ui_token(admin_jwt) == new

    def test_regenerate_requires_admin(self):
        assert requests.post(f"{BASE_URL}/api/localworker/token/regenerate",
                             timeout=TIMEOUT).status_code == 401


class TestStatusAndPackage:
    def test_status_public(self):
        r = requests.get(f"{BASE_URL}/api/localworker/status", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        for k in ("online", "workers", "queue", "settings"):
            assert k in d, f"Feld {k} fehlt"
        assert isinstance(d["online"], bool)
        assert isinstance(d["workers"], list)

    def test_manifest_complete(self):
        r = requests.get(f"{BASE_URL}/api/localworker/package/manifest", timeout=TIMEOUT)
        assert r.status_code == 200
        m = r.json()
        assert m["missing"] == [], f"Fehlende Dateien: {m['missing']}"
        assert m["complete"] is True

    def test_package_zip(self):
        r = requests.get(f"{BASE_URL}/api/localworker/package", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:200]
        z = zipfile.ZipFile(io.BytesIO(r.content))
        names = z.namelist()
        for f in ("worker.py", "requirements.txt", "README.md"):
            assert f in names, f"{f} fehlt im ZIP"
        for sub in ("core", "services", "strategies", "models"):
            assert any(n.startswith(f"{sub}/") for n in names), f"Modul {sub} fehlt"
        assert z.testzip() is None
