"""Iteration 16: park/reactivate lesson endpoints + lessons CRUD regression (live API)."""
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
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def client(token):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    return s


def _find(lessons, lid):
    for l in lessons:
        if l.get("id") == lid:
            return l
    return None


class TestParkReactivate:
    def test_full_lifecycle(self, client):
        # CREATE
        r = client.post(f"{BASE_URL}/api/ai/lessons",
                        json={"title": "TEST_park_lesson", "detail": "TEST detail", "weight": 3})
        assert r.status_code == 200, r.text[:300]
        lid = r.json()["lesson"]["id"]
        try:
            # GET list -> present, not dormant
            lst = client.get(f"{BASE_URL}/api/ai/lessons").json()
            lessons = lst.get("lessons", lst if isinstance(lst, list) else [])
            found = _find(lessons, lid)
            assert found is not None
            assert found.get("status") != "dormant"

            # PARK
            r = client.post(f"{BASE_URL}/api/ai/lessons/{lid}/park")
            assert r.status_code == 200, r.text[:300]
            assert r.json()["lesson"].get("status") == "dormant"
            lessons = client.get(f"{BASE_URL}/api/ai/lessons").json().get("lessons", [])
            assert _find(lessons, lid).get("status") == "dormant"

            # REACTIVATE
            r = client.post(f"{BASE_URL}/api/ai/lessons/{lid}/reactivate")
            assert r.status_code == 200, r.text[:300]
            assert r.json()["lesson"].get("status") != "dormant"
            lessons = client.get(f"{BASE_URL}/api/ai/lessons").json().get("lessons", [])
            assert _find(lessons, lid).get("status") != "dormant"

            # PATCH (regression)
            r = client.patch(f"{BASE_URL}/api/ai/lessons/{lid}", json={"detail": "TEST detail v2"})
            assert r.status_code == 200
            assert r.json()["lesson"]["detail"] == "TEST detail v2"
        finally:
            d = client.delete(f"{BASE_URL}/api/ai/lessons/{lid}")
            assert d.status_code in (200, 204)
        lessons = client.get(f"{BASE_URL}/api/ai/lessons").json().get("lessons", [])
        assert _find(lessons, lid) is None

    def test_park_unknown_id_404(self, client):
        r = client.post(f"{BASE_URL}/api/ai/lessons/les_DOESNOTEXIST/park")
        assert r.status_code == 404

    def test_reactivate_unknown_id_404(self, client):
        r = client.post(f"{BASE_URL}/api/ai/lessons/les_DOESNOTEXIST/reactivate")
        assert r.status_code == 404

    def test_park_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/ai/lessons/les_x/park", timeout=30)
        assert r.status_code in (401, 403), r.status_code

    def test_reactivate_requires_auth(self):
        r = requests.post(f"{BASE_URL}/api/ai/lessons/les_x/reactivate", timeout=30)
        assert r.status_code in (401, 403), r.status_code
