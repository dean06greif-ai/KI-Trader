"""API-Regressionstests (Iteration 15) fuer die 3 Bugfixes:
  1) correlation_guard: Trader-Hoheit (manuelles Umschalten wirkt sofort + persistiert)
  2) /api/ai/status Key-/Provider-Status vorhanden (402-Fix ist intern/Unit-getestet)
  3) Lektionen-Lebenszyklus: abgelaufene Lektion -> status=dormant, NICHT geloescht
     + CRUD-Regression fuer Trader-Lektionen.
Laeuft gegen die oeffentliche Preview-URL (REACT_APP_BACKEND_URL).
"""
import os
import re
from pathlib import Path

import pytest
import requests
from dotenv import dotenv_values

pytestmark = pytest.mark.live

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL fehlt")
BASE_URL = base_url.rstrip("/")


@pytest.fixture(scope="module")
def creds():
    p = Path("/app/memory/test_credentials.md")
    if not p.exists():
        pytest.skip("test_credentials.md fehlt")
    txt = p.read_text(encoding="utf-8")
    u = re.search(r"(?im)^\s*[-*]?\s*Benutzer\s*:\s*(\S+)", txt)
    pw = re.search(r"(?im)^\s*[-*]?\s*Passwort\s*:\s*(\S+)", txt)
    if not u or not pw:
        pytest.skip("keine Credentials gefunden")
    return {"username": u.group(1), "password": pw.group(1)}


@pytest.fixture(scope="module")
def client(creds):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json=creds, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Login fehlgeschlagen: {r.status_code} {r.text[:300]}")
    token = r.json().get("token")
    assert isinstance(token, str) and token
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _cfg(client):
    r = client.get(f"{BASE_URL}/api/ai/status", timeout=30)
    assert r.status_code == 200, r.text[:300]
    return r.json()


# ---------------------------------------------------------------- Status/Auth
class TestStatusAndAuth:
    def test_status_200_with_key_and_provider_fields(self, client):
        d = _cfg(client)
        assert isinstance(d.get("config"), dict)
        for field in ("has_key", "provider_keys", "providers_health", "backup_keys"):
            assert field in d, f"{field} fehlt in /api/ai/status"
        assert "correlation_guard" in d["config"]

    def test_login_wrong_password_401(self, creds):
        r = requests.post(f"{BASE_URL}/api/auth/login",
                          json={"username": creds["username"], "password": "wrong-x"},
                          timeout=30)
        assert r.status_code == 401

    def test_config_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/ai/config",
                          json={"correlation_guard": False}, timeout=30)
        assert r.status_code == 401

    def test_lesson_create_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/ai/lessons",
                          json={"title": "TEST_unauth", "detail": "x"}, timeout=30)
        assert r.status_code == 401


# --------------------------------------------------- correlation_guard (Fix 1)
class TestCorrelationGuardTraderAuthority:
    def test_toggle_off_then_on_persists(self, client):
        original = bool(_cfg(client)["config"].get("correlation_guard", True))
        try:
            # OFF
            r = client.post(f"{BASE_URL}/api/ai/config",
                            json={"correlation_guard": False}, timeout=30)
            assert r.status_code == 200, r.text[:300]
            body = r.json()
            assert body.get("status") == "success"
            assert body["config"]["correlation_guard"] is False
            assert _cfg(client)["config"]["correlation_guard"] is False

            # ON
            r = client.post(f"{BASE_URL}/api/ai/config",
                            json={"correlation_guard": True}, timeout=30)
            assert r.status_code == 200
            assert r.json()["config"]["correlation_guard"] is True
            assert _cfg(client)["config"]["correlation_guard"] is True

            # OFF nochmal -> muss bestehen bleiben (kein KI-Rueckdrehen)
            client.post(f"{BASE_URL}/api/ai/config",
                        json={"correlation_guard": False}, timeout=30)
            assert _cfg(client)["config"]["correlation_guard"] is False
        finally:
            client.post(f"{BASE_URL}/api/ai/config",
                        json={"correlation_guard": original}, timeout=30)

    def test_toggle_off_survives_unrelated_config_update(self, client):
        original = bool(_cfg(client)["config"].get("correlation_guard", True))
        try:
            client.post(f"{BASE_URL}/api/ai/config",
                        json={"correlation_guard": False}, timeout=30)
            r = client.post(f"{BASE_URL}/api/ai/config",
                            json={"max_same_direction": 3}, timeout=30)
            assert r.status_code == 200
            assert _cfg(client)["config"]["correlation_guard"] is False
        finally:
            client.post(f"{BASE_URL}/api/ai/config",
                        json={"correlation_guard": original}, timeout=30)

    def test_ai_change_of_guard_needs_trader_confirmation(self):
        """Unit-Ebene: _tuning_guard blockiert JEDE KI-Aenderung am Guard."""
        from services.ai_engine import ai_engine
        for val in (True, False):
            msg = ai_engine._tuning_guard({"correlation_guard": val})
            assert msg, f"KI-Aenderung auf {val} wurde nicht blockiert"
            assert "Trader" in msg


# ------------------------------------------------------ Lektionen CRUD (Fix 3)
class TestLessonsCrud:
    def test_create_update_delete_lesson(self, client):
        created_id = None
        try:
            r = client.post(f"{BASE_URL}/api/ai/lessons",
                            json={"title": "TEST_Lektion Regression",
                                  "detail": "Nur Testzwecke", "weight": 3}, timeout=60)
            assert r.status_code == 200, r.text[:300]
            lesson = r.json()["lesson"]
            created_id = lesson["id"]
            assert lesson["title"] == "TEST_Lektion Regression"
            assert lesson["locked"] is True
            assert lesson["origin"] in ("trader", "user", "human")

            # GET-Persistenz
            all_l = client.get(f"{BASE_URL}/api/ai/lessons", timeout=30).json()["lessons"]
            found = [l for l in all_l if l["id"] == created_id]
            assert found, "Lektion nach POST nicht in GET /api/ai/lessons"
            assert found[0]["detail"] == "Nur Testzwecke"

            # PATCH
            r = client.patch(f"{BASE_URL}/api/ai/lessons/{created_id}",
                             json={"detail": "Geaendert im Test"}, timeout=60)
            assert r.status_code == 200, r.text[:300]
            assert r.json()["lesson"]["detail"] == "Geaendert im Test"
            all_l = client.get(f"{BASE_URL}/api/ai/lessons", timeout=30).json()["lessons"]
            assert [l for l in all_l if l["id"] == created_id][0]["detail"] == "Geaendert im Test"

            # DELETE
            r = client.delete(f"{BASE_URL}/api/ai/lessons/{created_id}", timeout=60)
            assert r.status_code == 200
            all_l = client.get(f"{BASE_URL}/api/ai/lessons", timeout=30).json()["lessons"]
            assert not [l for l in all_l if l["id"] == created_id]
            created_id = None
        finally:
            if created_id:
                client.delete(f"{BASE_URL}/api/ai/lessons/{created_id}", timeout=30)

    def test_create_lesson_validation_400(self, client):
        r = client.post(f"{BASE_URL}/api/ai/lessons",
                        json={"title": "", "detail": ""}, timeout=30)
        assert r.status_code == 400

    def test_patch_unknown_lesson_404(self, client):
        r = client.patch(f"{BASE_URL}/api/ai/lessons/les_doesnotexist",
                         json={"detail": "x"}, timeout=30)
        assert r.status_code == 404

    def test_delete_unknown_lesson_404(self, client):
        r = client.delete(f"{BASE_URL}/api/ai/lessons/les_doesnotexist", timeout=30)
        assert r.status_code == 404

    def test_lessons_response_has_no_mongo_id(self, client):
        lessons = client.get(f"{BASE_URL}/api/ai/lessons", timeout=30).json()["lessons"]
        assert all("_id" not in l for l in lessons)


# ------------------------------------------- Lebenszyklus dormant (Fix 3 core)
class TestLessonLifecycleDormant:
    def test_expired_lesson_becomes_dormant_and_is_not_deleted(self, client):
        """Abgelaufene KI-Lektion direkt in Mongo -> GET liefert status=dormant."""
        from pymongo import MongoClient
        mongo_url = os.environ.get("MONGO_URL") or dotenv_values("/app/backend/.env").get("MONGO_URL")
        db_name = os.environ.get("DB_NAME") or dotenv_values("/app/backend/.env").get("DB_NAME")
        assert mongo_url and db_name
        mc = MongoClient(mongo_url)
        coll = mc[db_name]["settings"]
        lesson = {"id": "les_TESTdormant1", "title": "TEST_Regime-Bias",
                  "detail": "x", "valid_until": "2026-01-01T00:00:00+00:00",
                  "origin": "ai", "locked": False, "weight": 2}
        try:
            coll.update_one({"_id": "ai_lessons"},
                            {"$push": {"lessons": lesson}}, upsert=True)
            lessons = client.get(f"{BASE_URL}/api/ai/lessons", timeout=30).json()["lessons"]
            hit = [l for l in lessons if l["id"] == "les_TESTdormant1"]
            assert hit, "abgelaufene Lektion wurde GELOESCHT statt zurueckgestellt"
            assert hit[0].get("status") == "dormant", hit[0]
            assert hit[0].get("dormant_since")

            # Persistenz in Mongo
            doc = coll.find_one({"_id": "ai_lessons"}) or {}
            stored = [l for l in doc.get("lessons", []) if l.get("id") == "les_TESTdormant1"]
            assert stored and stored[0].get("status") == "dormant"
        finally:
            coll.update_one({"_id": "ai_lessons"},
                            {"$pull": {"lessons": {"id": "les_TESTdormant1"}}})
            mc.close()

    def test_locked_expired_lesson_stays_active(self):
        from services.ai_lessons import apply_lifecycle
        out, _ = apply_lifecycle([{"id": "a", "title": "t", "detail": "d",
                                   "locked": True,
                                   "valid_until": "2020-01-01T00:00:00+00:00"}])
        assert out[0].get("status") != "dormant"
