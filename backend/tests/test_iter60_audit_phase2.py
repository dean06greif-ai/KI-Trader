"""Iteration 60 - Audit Phase 2 (steps 2.4-2.10) API regression tests."""
import os
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://trading-infra-build.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "LocalTest06!"


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(session):
    # retry once for cold start
    for _ in range(2):
        r = session.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
        if r.status_code == 200:
            data = r.json()
            tok = data.get("token") or data.get("access_token")
            assert tok, f"no token in login response: {data}"
            return tok
    pytest.fail(f"login failed: {r.status_code} {r.text[:400]}")


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


# ---------- Regression / Auth ----------

def test_health(session):
    for _ in range(3):
        r = session.get(f"{BASE_URL}/api/health", timeout=30)
        if r.status_code == 200:
            break
    assert r.status_code == 200
    j = r.json()
    assert j.get("status") in ("alive", "ok") or "alive" in str(j).lower()


def test_login_bad_password(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": "wrong"}, timeout=30)
    assert r.status_code in (400, 401, 403)


def test_balance_unauth_401(session):
    r = session.get(f"{BASE_URL}/api/autotrade/balance", timeout=30)
    assert r.status_code in (401, 403)


def test_balance_authed(session, auth_headers):
    r = session.get(f"{BASE_URL}/api/autotrade/balance", headers=auth_headers, timeout=30)
    assert r.status_code == 200


# ---------- 2.4 Safety status ----------

def test_safety_status_unauth(session):
    r = session.get(f"{BASE_URL}/api/safety/status", timeout=30)
    assert r.status_code in (401, 403)


def test_safety_status_authed(session, auth_headers):
    r = session.get(f"{BASE_URL}/api/safety/status", headers=auth_headers, timeout=30)
    assert r.status_code == 200, r.text[:400]
    j = r.json()
    assert "level" in j
    assert j["level"] in ("ok", "warn", "critical")
    assert "checks" in j
    checks = j["checks"]
    # checks may be dict or list; normalise
    if isinstance(checks, dict):
        keys = set(checks.keys())
    else:
        keys = set()
        for c in checks:
            if isinstance(c, dict):
                for k in ("name", "id", "key"):
                    if k in c:
                        keys.add(c[k])
    expected = {"sl_missing", "close_failed", "sync_stale", "watchdog_stale"}
    missing = expected - keys
    assert not missing, f"missing safety checks {missing}, got {keys}"


def test_safety_config_roundtrip(session, auth_headers):
    r = session.post(
        f"{BASE_URL}/api/safety/config",
        headers=auth_headers,
        json={"sync_stale_warn_min": 20},
        timeout=30,
    )
    assert r.status_code in (200, 204), r.text[:400]
    # verify persisted via GET status still works
    r2 = session.get(f"{BASE_URL}/api/safety/status", headers=auth_headers, timeout=30)
    assert r2.status_code == 200


# ---------- 2.7 Reward calibration ----------

def test_ai_rewards_has_calibration(session, auth_headers):
    r = session.get(f"{BASE_URL}/api/ai/rewards?days=30", headers=auth_headers, timeout=30)
    assert r.status_code == 200, r.text[:400]
    j = r.json()
    assert "calibration" in j, f"missing calibration key, got: {list(j.keys())}"
    calib = j["calibration"]
    assert isinstance(calib, dict)
    for k in ("bins", "rated", "calibration_error"):
        assert k in calib, f"calibration missing '{k}': {calib}"


# ---------- 2.10 ML-Gate status ----------

def test_ml_gate_status_has_risk_scaling(session, auth_headers):
    # try common paths
    paths = ["/api/ml/gate/status", "/api/ml-gate/status", "/api/ml_gate/status", "/api/ml-gate"]
    resp = None
    for p in paths:
        r = session.get(f"{BASE_URL}{p}", headers=auth_headers, timeout=30)
        if r.status_code == 200:
            resp = r
            break
    assert resp is not None, f"no ml-gate status endpoint responded 200 among {paths}"
    j = resp.json()
    assert "risk_scaling_active" in j, f"risk_scaling_active missing, keys={list(j.keys())}"
    assert j["risk_scaling_active"] is False


# ---------- Playbook maturity ----------

def test_ai_playbook_maturity_fields(session, auth_headers):
    # look for playbook endpoint
    candidates = ["/api/ai/playbook", "/api/ai/setup-status", "/api/ai/maturity", "/api/ai/playbook/status"]
    resp = None
    for p in candidates:
        r = session.get(f"{BASE_URL}{p}", headers=auth_headers, timeout=30)
        if r.status_code == 200:
            resp = r
            used = p
            break
    assert resp is not None, f"no playbook endpoint 200 in {candidates}"
    j = resp.json()
    # find maturity rows
    rows = None
    if isinstance(j, dict):
        for key in ("maturity", "rows", "setups", "items"):
            v = j.get(key)
            if isinstance(v, list):
                rows = v
                break
        if rows is None:
            # maybe dict of setup->row
            for key in ("maturity", "setups"):
                v = j.get(key)
                if isinstance(v, dict):
                    rows = list(v.values())
                    break
    # If DB fresh, rows may be empty — accept that
    if not rows:
        print(f"playbook rows empty (fresh DB) at {used}; keys={list(j.keys()) if isinstance(j, dict) else type(j)}")
        return
    row = rows[0]
    assert isinstance(row, dict)
    for field in ("wr_ci", "uncertain", "live_wr_ci"):
        assert field in row, f"maturity row missing '{field}': {list(row.keys())}"
    assert isinstance(row["wr_ci"], list) and len(row["wr_ci"]) == 2
    assert isinstance(row["uncertain"], bool)
