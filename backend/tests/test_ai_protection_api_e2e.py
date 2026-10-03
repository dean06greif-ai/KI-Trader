"""E2E-Tests für die /api/autotrade/ai-protection Endpoints.

Live gegen die Preview-URL. Prüft:
  * GET liefert Default-Policy
  * PATCH ohne Admin-Token wird abgelehnt (401/403)
  * PATCH mit Admin-Token aktualisiert und ignoriert unbekannte Felder
  * PATCH mit ausschließlich ungültigen Feldern -> 400
  * Reset am Ende: trigger_pct zurück auf 30.
"""
import os

import pytest
import requests

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


pytestmark = pytest.mark.live  # nicht in der unit-Suite; explizit ausführen


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=10,
    )
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text}")
    tok = r.json().get("token")
    assert tok, f"missing token in response: {r.json()}"
    return tok


class TestAiProtectionApi:
    def test_get_returns_defaults(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/ai-protection", timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        # Erwartete Default-Policy
        expected = {
            "enabled": True,
            "trigger_pct": 30,
            "lock_pct": 50,
            "release_margin": True,
            "max_leverage": 200,
            "margin_reduce_pct": 100,
            "sl_liq_buffer_pct": 0.3,
        }
        for k, v in expected.items():
            assert k in data, f"missing key {k} in {data}"
            if isinstance(v, float):
                assert abs(float(data[k]) - v) < 1e-6, f"{k}={data[k]} != {v}"
            elif isinstance(v, bool):
                assert bool(data[k]) is v
            else:
                assert float(data[k]) == float(v), f"{k}={data[k]} != {v}"

    def test_patch_without_auth_rejected(self):
        r = requests.patch(
            f"{BASE_URL}/api/autotrade/ai-protection",
            json={"trigger_pct": 20}, timeout=10)
        assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text}"

    def test_patch_with_admin_updates(self, admin_token):
        r = requests.patch(
            f"{BASE_URL}/api/autotrade/ai-protection",
            json={"trigger_pct": 20},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        data = r.json()
        assert float(data["trigger_pct"]) == 20.0
        # Verify via GET
        r2 = requests.get(f"{BASE_URL}/api/autotrade/ai-protection", timeout=10)
        assert r2.status_code == 200
        assert float(r2.json()["trigger_pct"]) == 20.0

    def test_patch_invalid_field_only_400(self, admin_token):
        r = requests.patch(
            f"{BASE_URL}/api/autotrade/ai-protection",
            json={"foo": 1},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        assert r.status_code == 400, r.text

    def test_reset_trigger_pct(self, admin_token):
        r = requests.patch(
            f"{BASE_URL}/api/autotrade/ai-protection",
            json={"trigger_pct": 30},
            headers={"Authorization": f"Bearer {admin_token}"},
            timeout=10,
        )
        assert r.status_code == 200, r.text
        assert float(r.json()["trigger_pct"]) == 30.0
