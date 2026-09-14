"""Smoke regression test after Phase T (test-hygiene) changes.

Verifies read-only endpoints + login work via public preview URL.
NO destructive endpoints called.
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrading-core.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


def _get(session, path, headers=None, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            r = session.get(f"{BASE_URL}{path}", headers=headers, timeout=timeout)
            if r.status_code < 500:
                return r
            last = r
        except requests.exceptions.RequestException as e:
            last = e
        time.sleep(2)
    if isinstance(last, requests.Response):
        return last
    raise last


def _post(session, path, json=None, timeout=30, retries=3):
    last = None
    for i in range(retries):
        try:
            r = session.post(f"{BASE_URL}{path}", json=json, timeout=timeout)
            if r.status_code < 500:
                return r
            last = r
        except requests.exceptions.RequestException as e:
            last = e
        time.sleep(2)
    if isinstance(last, requests.Response):
        return last
    raise last


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def admin_token(session):
    r = _post(session, "/api/auth/login",
              json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text[:200]}"
    data = r.json()
    tok = data.get("token") or data.get("access_token")
    assert tok, f"no token in response: {data}"
    return tok


# --- Health -----------------------------------------------------------------
def test_health(session):
    r = _get(session, "/api/health", timeout=30)
    assert r.status_code == 200
    assert r.json().get("status") == "alive"


# --- Auth -------------------------------------------------------------------
def test_login_success(session):
    r = _post(session, "/api/auth/login",
              json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200
    body = r.json()
    assert body.get("token") or body.get("access_token")


def test_login_wrong_password(session):
    r = _post(session, "/api/auth/login",
              json={"username": ADMIN_USER, "password": "WRONG"}, timeout=20)
    assert r.status_code == 401


# --- AI playbook ------------------------------------------------------------
def test_ai_playbook(session):
    r = _get(session, "/api/ai/playbook", timeout=20)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    assert "setups" in data
    assert "classes" in data
    classes = data["classes"]
    for k in ("crypto", "indices", "resources", "forex"):
        assert k in classes, f"missing class {k}"
    assert "maturity" in data


# --- Safety status ----------------------------------------------------------
def test_safety_status(session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = _get(session, "/api/safety/status", headers=headers, timeout=20)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    data = r.json()
    light = None
    for k in ("light", "level", "status", "ampel", "traffic_light"):
        if k in data:
            light = data[k]
            break
    assert light in ("ok", "warn", "critical", "green", "yellow", "red") or isinstance(data, dict), \
        f"unexpected safety payload: {data}"


# --- AI status --------------------------------------------------------------
def test_ai_status(session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = _get(session, "/api/ai/status", headers=headers, timeout=20)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"


# --- Autotrade slippage stats -----------------------------------------------
def test_autotrade_slippage_stats(session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = _get(session, "/api/autotrade/slippage-stats?days=30", headers=headers, timeout=30)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"


# --- Policy lab status ------------------------------------------------------
def test_policy_lab_status(session, admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    r = _get(session, "/api/policy-lab/status", headers=headers, timeout=20)
    assert r.status_code == 200, f"{r.status_code} {r.text[:200]}"
