"""Iteration 23 – Regime-Lab release/ablation/delete endpoints."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://regime-analysis-dev.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": "Admin", "password": "Dean06Greif!/Admin"},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in response: {r.json()}"
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def test_regime_lab_releases_shape(admin_headers):
    r = requests.get(f"{BASE_URL}/api/regime-lab/releases", headers=admin_headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("shadow_by_aid", "orphan_shadow", "activation", "releases", "rewards_by_structural"):
        assert k in data, f"missing key {k} in {list(data.keys())}"
    assert isinstance(data["shadow_by_aid"], dict)
    assert isinstance(data["orphan_shadow"], dict)


def test_ablation_local_returns_503(admin_headers):
    r = requests.post(
        f"{BASE_URL}/api/regime-lab/ablation",
        headers=admin_headers,
        json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60, "execution": "local"},
        timeout=30,
    )
    assert r.status_code == 503, f"expected 503, got {r.status_code}: {r.text}"


def test_delete_nonexistent_analysis_404(admin_headers):
    r = requests.delete(
        f"{BASE_URL}/api/regime-lab/nonexistent-id-xyz-12345",
        headers=admin_headers,
        timeout=30,
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
