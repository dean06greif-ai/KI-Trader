"""Iter55 live API checks for 'Ein Regime für alle' + 'Fairer Zeitraum-Vergleich'.

Read-only + a single fair-compare job (writes settings doc only). Shared production DB.
"""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://trader-upgrade-3.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=20)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


def test_health():
    r = requests.get(f"{BASE_URL}/api/health", timeout=20)
    assert r.status_code == 200


# ---- champions shape with fair field ----
def test_champions_has_fair_field():
    r = requests.get(f"{BASE_URL}/api/regime-lab/champions", params={"symbols": "BTCUSDT,ETHUSDT"}, timeout=60)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "results" in data and isinstance(data["results"], dict)
    # at least one key should exist (format SYMBOL|band)
    assert len(data["results"]) >= 1
    allowed_reasons = {"missing", "old", "uncovered", "blocked", "ok"}
    for key, pair in data["results"].items():
        assert "|" in key
        assert "fair" in pair, f"no fair field in {key}: {list(pair.keys())}"
        f = pair["fair"]
        assert isinstance(f.get("used"), bool)
        assert f.get("reason") in allowed_reasons, f"reason={f.get('reason')} not in {allowed_reasons} ({key})"
        # optional: window/note/excluded tolerated
        if "excluded" in f:
            assert isinstance(f["excluded"], (list, dict, int))


# ---- fair-compare admin guard ----
def test_fair_compare_requires_admin():
    r = requests.post(f"{BASE_URL}/api/regime-lab/champions/fair-compare", json={"keys": ["ETHUSDT|intraday"]}, timeout=20)
    assert r.status_code in (401, 403), r.status_code


def test_fair_compare_status_public():
    r = requests.get(f"{BASE_URL}/api/regime-lab/champions/fair-compare/status", timeout=20)
    assert r.status_code == 200
    js = r.json()
    for k in ("running", "done", "total", "results"):
        assert k in js, f"status missing {k}: {js.keys()}"


# ---- fair-compare run ----
def _status(admin_token):
    r = requests.get(f"{BASE_URL}/api/regime-lab/champions/fair-compare/status", timeout=20)
    return r.json()


def test_fair_compare_run_and_409(admin_token):
    headers = {"Authorization": f"Bearer {admin_token}"}
    # Wait until any already-running job is finished (defensive)
    for _ in range(60):
        if not _status(admin_token).get("running"):
            break
        time.sleep(2)

    r = requests.post(
        f"{BASE_URL}/api/regime-lab/champions/fair-compare",
        headers=headers,
        json={"keys": ["ETHUSDT|intraday"]},
        timeout=30,
    )
    assert r.status_code == 200, r.text
    assert r.json().get("started") is True

    # Immediate second call should 409 while running — tolerate quick finish
    r2 = requests.post(
        f"{BASE_URL}/api/regime-lab/champions/fair-compare",
        headers=headers,
        json={"keys": ["ETHUSDT|intraday"]},
        timeout=20,
    )
    if _status(admin_token).get("running"):
        assert r2.status_code == 409, (r2.status_code, r2.text)

    # Wait for completion (<=2min)
    done = False
    for _ in range(60):
        s = _status(admin_token)
        if not s.get("running") and s.get("done", 0) >= s.get("total", 0):
            done = True
            break
        time.sleep(2)
    assert done, f"fair-compare didn't finish in time: {_status(admin_token)}"


def test_after_run_eth_intraday_fair():
    r = requests.get(f"{BASE_URL}/api/regime-lab/champions", params={"symbols": "ETHUSDT"}, timeout=60)
    assert r.status_code == 200
    results = r.json()["results"]
    key = "ETHUSDT|intraday"
    if key in results:
        f = results[key]["fair"]
        # Accept used=True OR a blocked/old/uncovered reason with explanatory note
        assert f.get("used") is True or f.get("reason") in {"blocked", "old", "uncovered", "missing"}, f
        # candidates should carry 'fair' annotation in why when used
        if f.get("used"):
            cands = results[key].get("candidates") or []
            found = any("fair" in str(c.get("why", "")).lower() for c in cands)
            assert found, f"no 'fair' marker in candidates why: {cands}"


def test_btc_pairs_already_fair():
    r = requests.get(f"{BASE_URL}/api/regime-lab/champions", params={"symbols": "BTCUSDT"}, timeout=60)
    assert r.status_code == 200
    results = r.json()["results"]
    for key in ("BTCUSDT|intraday", "BTCUSDT|swing"):
        if key in results:
            f = results[key]["fair"]
            # Expected: already computed from a prior run → used True (or blocked with note)
            assert f.get("reason") in {"ok", "blocked", "old", "uncovered"} or f.get("used") is True, (key, f)


# ---- regime_phase still works ----
def test_regime_phase_btc():
    r = requests.get(f"{BASE_URL}/api/autotrade/regime_phase/BTCUSDT", timeout=30)
    assert r.status_code == 200, r.text
    js = r.json()
    assert "phase" in js
    assert "label" in js
    assert "lab" in js and isinstance(js["lab"], dict)
    assert "stage" in js["lab"]
