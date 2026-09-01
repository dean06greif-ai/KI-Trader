"""Regression: Worker-Token-Fix (Cache-Desync + UI-Fehlerpfad + OpenRouter-Tageslimit).

Deckt ab:
- get_token cached KEIN Zufalls-Token, wenn db None ist (sperrte sonst alle Worker aus)
- get_token liest atomar das bestehende Token aus Mongo (Multi-Prozess-sicher)
- require_worker heilt einen veralteten Cache über refresh_token (Deploy/Regenerate
  auf anderer Instanz) statt dauerhaft 401 zu liefern
- E2E: UI-Token == vom Poll-Endpoint akzeptiertes Token
- ai_providers erkennt OpenRouter "free-models-per-day" als TAGES-Limit
"""
import asyncio
import os

import pytest
import requests
from dotenv import dotenv_values

from services import local_exec
from services.ai_providers import _quota_cooldown_s, KEY_LIMIT_COOLDOWN_S

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
TIMEOUT = 60


class _FakeSettings:
    def __init__(self, token=None):
        self.doc = {"_id": "local_worker_token", "token": token} if token else None
        self.calls = 0

    async def find_one_and_update(self, flt, update, upsert=False, return_document=None):
        self.calls += 1
        if self.doc is None:
            self.doc = {"_id": "local_worker_token",
                        **update.get("$setOnInsert", {})}
        return self.doc

    async def update_one(self, flt, update, upsert=False):
        self.doc = {"_id": "local_worker_token", **update.get("$set", {})}


class _FakeDB:
    def __init__(self, token=None):
        self.settings = _FakeSettings(token)


@pytest.fixture(autouse=True)
def _reset_token_cache():
    local_exec._token_cache = None
    local_exec._token_refreshed_at = 0.0
    yield
    local_exec._token_cache = None
    local_exec._token_refreshed_at = 0.0


class TestGetToken:
    def test_no_cache_without_db(self):
        t1 = asyncio.run(local_exec.get_token(None))
        assert local_exec._token_cache is None, \
            "Zufalls-Token darf ohne DB nicht gecached werden"
        db = _FakeDB(token="real-token-from-mongo")
        t2 = asyncio.run(local_exec.get_token(db))
        assert t2 == "real-token-from-mongo"
        assert t1 != t2

    def test_reads_existing_token_from_db(self):
        db = _FakeDB(token="persisted-abc")
        assert asyncio.run(local_exec.get_token(db)) == "persisted-abc"
        assert local_exec._token_cache == "persisted-abc"

    def test_generates_and_persists_when_missing(self):
        db = _FakeDB()
        t = asyncio.run(local_exec.get_token(db))
        assert isinstance(t, str) and len(t) == 48
        assert db.settings.doc["token"] == t
        # zweiter Aufruf: Cache, kein weiterer DB-Roundtrip
        calls = db.settings.calls
        assert asyncio.run(local_exec.get_token(db)) == t
        assert db.settings.calls == calls

    def test_refresh_heals_stale_cache(self):
        """Simuliert: andere Instanz hat regeneriert -> Cache veraltet."""
        local_exec._token_cache = "stale-old-token"
        db = _FakeDB(token="new-token-in-mongo")
        assert asyncio.run(local_exec.get_token(db)) == "stale-old-token"
        assert asyncio.run(local_exec.refresh_token(db)) == "new-token-in-mongo"
        assert local_exec._token_cache == "new-token-in-mongo"

    def test_refresh_throttled(self):
        db = _FakeDB(token="tok-a")
        asyncio.run(local_exec.refresh_token(db))
        # innerhalb 3s: kein erneuter Cache-Reset (liefert Cache)
        local_exec._token_cache = "cached-now"
        assert asyncio.run(local_exec.refresh_token(db)) == "cached-now"


class TestWorkerAuthE2E:
    """Gegen den laufenden Server: exakt der Flow UI -> Worker."""

    def _admin(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                          json={"username": os.environ.get("ADMIN_USER", "Admin"),
                                "password": os.environ.get("ADMIN_PASSWORD",
                                                           "Dean06Greif!/Admin")})
        assert r.status_code == 200, r.text[:300]
        return r.json()["token"]

    def test_ui_token_accepted_by_poll(self):
        jwt = self._admin()
        r = requests.get(f"{BASE_URL}/api/localworker/token", timeout=TIMEOUT,
                         headers={"Authorization": f"Bearer {jwt}"})
        assert r.status_code == 200, r.text[:300]
        token = r.json()["token"]
        assert token
        r2 = requests.post(f"{BASE_URL}/api/worker/poll", timeout=TIMEOUT,
                           headers={"X-Worker-Token": token},
                           json={"worker_id": "pytest-token-check", "name": "pytest",
                                 "version": "1.9.0", "want_compute": False,
                                 "want_data": False})
        assert r2.status_code == 200, r2.text[:300]
        assert "settings" in r2.json()

    def test_wrong_token_rejected(self):
        r = requests.post(f"{BASE_URL}/api/worker/poll", timeout=TIMEOUT,
                          headers={"X-Worker-Token": "definitiv-falsch"},
                          json={"worker_id": "pytest-bad"})
        assert r.status_code == 401

    def test_missing_token_rejected(self):
        r = requests.post(f"{BASE_URL}/api/worker/poll", timeout=TIMEOUT,
                          json={"worker_id": "pytest-bad"})
        assert r.status_code == 401

    def test_token_endpoint_requires_admin(self):
        r = requests.get(f"{BASE_URL}/api/localworker/token", timeout=TIMEOUT)
        assert r.status_code == 401
        r = requests.post(f"{BASE_URL}/api/localworker/token/regenerate", timeout=TIMEOUT)
        assert r.status_code == 401


class TestOpenRouterDailyLimit:
    def test_free_models_per_day_is_daily(self):
        msg = ('Rate limit exceeded: free-models-per-day. '
               'Add 10 credits to unlock 1000 free model requests per day')
        assert _quota_cooldown_s(msg) > KEY_LIMIT_COOLDOWN_S

    def test_minute_limit_stays_short(self):
        assert _quota_cooldown_s("Rate limit exceeded: 20 requests per minute") \
            == KEY_LIMIT_COOLDOWN_S
