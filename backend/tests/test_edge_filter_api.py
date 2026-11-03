# Edge-Filter config API + diagnosis regression (public URL)
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text}")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    yield s
    s.post(f"{BASE_URL}/api/ai/config", json={"edge_filter_max_fee_r": 0.1, "edge_filter_require_bias": True}, timeout=20)


def _cfg(s):
    r = s.get(f"{BASE_URL}/api/ai/status", timeout=20)
    assert r.status_code == 200
    d = r.json()
    return d.get("config", d)


def test_config_requires_admin():
    r = requests.post(f"{BASE_URL}/api/ai/config", json={"edge_filter_max_fee_r": 0.2}, timeout=20)
    assert r.status_code in (401, 403)


def test_defaults_present(admin):
    c = _cfg(admin)
    for k in ("edge_filter_enabled", "edge_filter_max_fee_r", "edge_filter_require_bias",
              "edge_filter_crypto_only", "edge_filter_apply_paper"):
        assert k in c, k


def test_update_and_persist(admin):
    r = admin.post(f"{BASE_URL}/api/ai/config", json={"edge_filter_max_fee_r": 0.15, "edge_filter_require_bias": False}, timeout=20)
    assert r.status_code == 200
    cfg = r.json()["config"]
    assert cfg["edge_filter_max_fee_r"] == 0.15
    assert cfg["edge_filter_require_bias"] is False
    c = _cfg(admin)
    assert c["edge_filter_max_fee_r"] == 0.15
    assert c["edge_filter_require_bias"] is False


def test_clamp(admin):
    r = admin.post(f"{BASE_URL}/api/ai/config", json={"edge_filter_max_fee_r": 5}, timeout=20)
    assert r.status_code == 200
    assert r.json()["config"]["edge_filter_max_fee_r"] == 0.5


def test_reset(admin):
    r = admin.post(f"{BASE_URL}/api/ai/config", json={"edge_filter_max_fee_r": 0.1, "edge_filter_require_bias": True}, timeout=20)
    assert r.status_code == 200
    c = _cfg(admin)
    assert c["edge_filter_max_fee_r"] == 0.1
    assert c["edge_filter_require_bias"] is True


def test_diagnosis():
    r = requests.get(f"{BASE_URL}/api/ai/diagnosis", timeout=60)
    assert r.status_code == 200
    assert isinstance(r.json(), dict)
