"""
Iteration 17 - Read-only tests against the LIVE preview backend.
Strictly GET only + one POST for login. No analyze/ema-compare/etc.
"""
import os
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ki-trader-refactor-7.preview.emergentagent.com").rstrip("/")
API = BASE_URL + "/api"

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    # Try login (single write op allowed)
    r = s.post(f"{API}/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    if r.status_code == 200:
        data = r.json()
        token = data.get("token") or data.get("access_token")
        if token:
            s.headers["Authorization"] = f"Bearer {token}"
    return s


def test_regime_lab_list(session):
    r = session.get(f"{API}/regime-lab/list", timeout=60)
    assert r.status_code == 200, r.text[:500]
    data = r.json()
    items = data if isinstance(data, list) else data.get("items") or data.get("analyses") or []
    assert isinstance(items, list)
    assert len(items) >= 1
    print(f"regime-lab/list returned {len(items)} items")


def test_regime_lab_detail_quality(session):
    r = session.get(f"{API}/regime-lab/ra_5c8e1115", timeout=60)
    assert r.status_code == 200, r.text[:500]
    j = r.json()
    q = j.get("quality") or {}
    combined = q.get("combined") or {}
    overall = combined.get("overall") or {}
    assert "grade" in overall
    assert "pct" in overall
    for k in ["reference_pct", "reference_grade", "reference_holdout_pct", "reference_lag_days", "reference_missed_pct"]:
        assert k in overall, f"missing field {k} in overall: {overall}"
    thr = combined.get("thresholds") or {}
    assert thr.get("reference_good") == 65.0
    assert thr.get("reference_ok") == 55.0
    assert thr.get("good") == 65.0
    assert thr.get("ok") == 50.0
    assert thr.get("min_holdout_bars") == 200
    # backward compat: no reference => grade still gut, pct ~97.7
    assert overall["grade"] == "gut", overall
    assert abs(float(overall["pct"]) - 97.7) < 1.0, overall


def test_regime_cockpit_health(session):
    r = session.get(f"{API}/regime-cockpit/health", timeout=60)
    assert r.status_code == 200, r.text[:500]
    j = r.json()
    checks = j.get("checks") or []
    names = [c.get("name") for c in checks]
    expected = {"orphaned_dynamic", "dynamic_idle", "no_release", "release_mismatch", "observer_low_hit", "structural_short_history"}
    assert expected.issubset(set(names)), f"missing checks: {expected - set(names)}, got: {names}"
    sh = next(c for c in checks if c.get("name") == "structural_short_history")
    assert sh.get("level") in ("ok", "warn")
    for f in ["level", "count", "items", "detail"]:
        assert f in sh, f"missing {f} in structural_short_history: {sh}"


def test_regime_lab_engine_defaults(session):
    r = session.get(f"{API}/regime-lab/engine/defaults", timeout=30)
    assert r.status_code == 200, r.text[:500]


def test_autotrade_regime_phase_optional(session):
    r = session.get(f"{API}/autotrade/regime-phase", params={"symbol": "BTCUSDT"}, timeout=30)
    # If endpoint does not exist -> 404 is acceptable; must not 500
    assert r.status_code != 500, r.text[:500]
    assert r.status_code in (200, 401, 403, 404), r.status_code
