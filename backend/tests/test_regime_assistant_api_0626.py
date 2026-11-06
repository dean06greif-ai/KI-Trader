# Regime-Assistant + Research-Mode API regression tests (06/2026)
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
ADMIN = {"username": "Admin", "password": "PreviewAdmin2026"}
CREATED_RUNS = []  # job ids created by this module (stored as regime_assistant_runs.id)


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json=ADMIN, timeout=30)
    if r.status_code != 200:
        pytest.skip("admin login failed")
    return r.json()["token"]


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}"}


# Overview
def test_overview():
    d = requests.get(f"{BASE_URL}/api/regime-assistant/overview", timeout=30).json()
    classes = {c["asset_class"]: c for c in d["classes"]}
    assert set(classes) >= {"crypto", "indices", "resources", "forex"}
    assert classes["crypto"]["recipe"]["macro_tf"] == "1d"
    assert classes["crypto"]["recipe"]["primary_tf"] == "8h"
    assert classes["indices"]["recipe"]["macro_tf"] == "1w" and classes["indices"]["recipe"]["primary_tf"] == "1d"
    assert classes["forex"]["recipe"]["macro_tf"] == "1d" and classes["forex"]["recipe"]["primary_tf"] == "4h"
    assert d["config"]["fallback_enabled"] is False
    assert d["research_mode"] == {"evidence_mode": "robust", "regime_val_mode": "time"}


# Run auth/validation
def test_run_requires_admin():
    r = requests.post(f"{BASE_URL}/api/regime-assistant/run", json={"asset_class": "crypto"}, timeout=30)
    assert r.status_code in (401, 403)


def test_run_invalid_class(hdr):
    r = requests.post(f"{BASE_URL}/api/regime-assistant/run", json={"asset_class": "foo"}, headers=hdr, timeout=30)
    assert r.status_code == 400


def test_job_404():
    assert requests.get(f"{BASE_URL}/api/regime-assistant/job/doesnotexist", timeout=30).status_code == 404


# Latest run
def test_latest_run():
    d = requests.get(f"{BASE_URL}/api/regime-assistant/runs/latest", params={"asset_class": "crypto"}, timeout=30).json()
    run = d["run"]
    assert run and run["asset_class"] == "crypto"
    assert "light" in run["summary"]
    ok = [r for r in run["results"] if r.get("eval")]
    assert ok
    assert set(ok[0]["eval"]["variants"]) == {"combined", "primary", "macro"}


# Live
def test_live_btc():
    d = requests.get(f"{BASE_URL}/api/regime-assistant/live/BTCUSDT", timeout=120).json()
    assert "macro" in d and "primary" in d and "combined" in d, d
    c = d["combined"]
    for k in ("allow_long", "allow_short", "size_factor", "label"):
        assert k in c


# Research mode
def test_research_mode_roundtrip(hdr):
    assert requests.post(f"{BASE_URL}/api/research-mode", json={"evidence_mode": "classic"}, timeout=30).status_code in (401, 403)
    r = requests.post(f"{BASE_URL}/api/research-mode", json={"evidence_mode": "classic"}, headers=hdr, timeout=30)
    assert r.status_code == 200 and r.json()["evidence_mode"] == "classic"
    assert requests.get(f"{BASE_URL}/api/research-mode", timeout=30).json()["evidence_mode"] == "classic"
    r = requests.post(f"{BASE_URL}/api/research-mode", json={"evidence_mode": "bogus", "regime_val_mode": "xx"}, headers=hdr, timeout=30)
    assert r.json() == {"evidence_mode": "classic", "regime_val_mode": "time"}
    r = requests.post(f"{BASE_URL}/api/research-mode", json={"evidence_mode": "robust", "regime_val_mode": "time"}, headers=hdr, timeout=30)
    assert r.json() == {"evidence_mode": "robust", "regime_val_mode": "time"}


# Config
def test_config_roundtrip(hdr):
    assert requests.post(f"{BASE_URL}/api/regime-assistant/config", json={"fallback_enabled": True}, timeout=30).status_code in (401, 403)
    r = requests.post(f"{BASE_URL}/api/regime-assistant/config", json={"fallback_enabled": True}, headers=hdr, timeout=30)
    assert r.json()["config"]["fallback_enabled"] is True
    assert requests.get(f"{BASE_URL}/api/regime-assistant/overview", timeout=30).json()["config"]["fallback_enabled"] is True
    r = requests.post(f"{BASE_URL}/api/regime-assistant/config", json={"fallback_enabled": False}, headers=hdr, timeout=30)
    assert r.json()["config"]["fallback_enabled"] is False
    assert requests.get(f"{BASE_URL}/api/regime-assistant/overview", timeout=30).json()["config"]["fallback_enabled"] is False


# Full run (slow): job lifecycle + 409
def test_run_job_lifecycle(hdr):
    r = requests.post(f"{BASE_URL}/api/regime-assistant/run",
                      json={"asset_class": "crypto", "symbols": ["BTCUSDT"], "days": 150}, headers=hdr, timeout=30)
    assert r.status_code == 200, r.text
    jid = r.json()["job_id"]
    r2 = requests.post(f"{BASE_URL}/api/regime-assistant/run",
                       json={"asset_class": "crypto", "symbols": ["BTCUSDT"], "days": 150}, headers=hdr, timeout=30)
    j = requests.get(f"{BASE_URL}/api/regime-assistant/job/{jid}", timeout=30).json()
    if r2.status_code == 200:
        # candles cached -> first job finished in <1s before second POST; then a 2nd job is legit
        assert j["status"] == "done", "second run accepted while first still running"
        CREATED_RUNS.append(r2.json()["job_id"])
    else:
        assert r2.status_code == 409
    CREATED_RUNS.append(jid)
    assert j["status"] in ("running", "done")
    deadline = time.time() + 280
    while time.time() < deadline and j["status"] == "running":
        time.sleep(8)
        j = requests.get(f"{BASE_URL}/api/regime-assistant/job/{jid}", timeout=30).json()
    assert j["status"] == "done", j.get("error") or j.get("phase")
    res = j["result"]
    assert res["asset_class"] == "crypto" and res["days"] == 150
    assert res["results"][0]["symbol"] == "BTCUSDT"
    latest = requests.get(f"{BASE_URL}/api/regime-assistant/runs/latest", params={"asset_class": "crypto"}, timeout=30).json()["run"]
    assert latest["id"] in CREATED_RUNS


@pytest.fixture(scope="module", autouse=True)
def _cleanup_runs():
    """Delete only regime_assistant_runs created by this module (no delete API exists)."""
    yield
    if not CREATED_RUNS:
        return
    from dotenv import dotenv_values
    from pymongo import MongoClient
    env = dotenv_values(os.path.join(os.path.dirname(__file__), "..", ".env"))
    MongoClient(env["MONGO_URL"])[env["DB_NAME"]].regime_assistant_runs.delete_many({"id": {"$in": CREATED_RUNS}})
