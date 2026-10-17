"""Regression tests for Regime Lab clarity changes (read-only against production DB)."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://crypto-trader-stable.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

TIMEOUT_SHORT = 30
TIMEOUT_LONG = 120


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS},
                      timeout=TIMEOUT_SHORT)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="session")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# --- Health & Auth ---
def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT_SHORT)
    assert r.status_code == 200
    body = r.json()
    assert body.get("status") == "alive"


def test_login_wrong_password():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": "wrong-password"},
                      timeout=TIMEOUT_SHORT)
    assert r.status_code == 401


def test_auth_verify(auth_headers):
    r = requests.get(f"{BASE_URL}/api/auth/verify", headers=auth_headers, timeout=TIMEOUT_SHORT)
    assert r.status_code == 200
    assert r.json().get("valid") is True


# --- Regime Lab list (with projection) ---
def test_regime_lab_list_performance_and_projection(auth_headers):
    t0 = time.time()
    r = requests.get(f"{BASE_URL}/api/regime-lab/list", headers=auth_headers, timeout=TIMEOUT_SHORT)
    dt = time.time() - t0
    assert r.status_code == 200
    assert dt < 10, f"list took {dt:.1f}s (>10s)"
    body = r.json()
    assert "analyses" in body
    analyses = body["analyses"]
    assert isinstance(analyses, list)
    if analyses:
        row = analyses[0]
        for f in ("id", "name", "symbols", "timeframe", "created_at",
                  "walkforward_passed", "dataset_status", "release"):
            assert f in row, f"missing field {f}"
        # no heavy fields
        for banned in ("chart_emas", "chart", "per_coin"):
            assert banned not in row, f"heavy field {banned} present in list row"
    pytest.regime_analyses = analyses  # stash for later tests


# --- Regime Lab calibrations ---
def test_regime_lab_calibrations(auth_headers):
    r = requests.get(f"{BASE_URL}/api/regime-lab/calibrations?limit=5",
                     headers=auth_headers, timeout=TIMEOUT_SHORT)
    assert r.status_code == 200
    body = r.json()
    assert "calibrations" in body
    cals = body["calibrations"]
    assert isinstance(cals, list)
    assert len(cals) <= 5
    for c in cals:
        assert "_id" not in c
        report = c.get("report") or {}
        assert "per_symbol" not in report, "per_symbol should be stripped"
    pytest.regime_calibrations = cals


# --- Regime Lab status fallback ---
def test_regime_lab_status_not_found(auth_headers):
    r = requests.get(f"{BASE_URL}/api/regime-lab/status/does-not-exist-xyz-123",
                     headers=auth_headers, timeout=TIMEOUT_SHORT)
    assert r.status_code == 404
    assert r.json().get("detail") == "Job nicht gefunden"


def test_regime_lab_status_calibration_fallback(auth_headers):
    cals = getattr(pytest, "regime_calibrations", [])
    if not cals:
        pytest.skip("no calibrations available")
    cid = cals[0].get("id") or cals[0].get("calibration_id")
    if not cid:
        pytest.skip("calibration has no id")
    r = requests.get(f"{BASE_URL}/api/regime-lab/status/{cid}",
                     headers=auth_headers, timeout=TIMEOUT_SHORT)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("kind") == "calibration"
    assert body.get("status") == "done"
    result = body.get("result") or {}
    assert result.get("kind") == "calibration"


# --- Regime Lab analysis detail with quality ---
def test_regime_lab_analysis_detail_with_quality(auth_headers):
    analyses = getattr(pytest, "regime_analyses", [])
    if not analyses:
        pytest.skip("no analyses available")
    # pick smallest analysis (fewest symbols) for speed
    def sym_count(a):
        s = a.get("symbols") or []
        return len(s) if isinstance(s, list) else 99
    pick = sorted(analyses, key=sym_count)[0]
    aid = pick["id"]
    r = requests.get(f"{BASE_URL}/api/regime-lab/{aid}",
                     headers=auth_headers, timeout=TIMEOUT_LONG)
    assert r.status_code == 200, r.text[:500]
    body = r.json()
    assert "analysis" in body
    assert "quality" in body
    quality = body["quality"]
    assert isinstance(quality, dict)
    for _, v in quality.items():
        if isinstance(v, dict) and "grade" in v:
            assert v["grade"] in ("gut", "mittel", "schwach")
            assert "pct" in v


# --- Additional read-only endpoints ---
@pytest.mark.parametrize("path", [
    "/api/regime-lab/active",
    "/api/regime-lab/engine/defaults",
    "/api/regime-lab/releases",
    "/api/autotrade/config",
    "/api/ai/status",
    "/api/maintenance/backup",
])
def test_readonly_endpoints(path, auth_headers):
    r = requests.get(f"{BASE_URL}{path}", headers=auth_headers, timeout=TIMEOUT_SHORT)
    assert r.status_code == 200, f"{path} -> {r.status_code} {r.text[:200]}"
