"""Iteration 10 review – Guard-Shadow API + Worker 1.13.0 Restart-Detection + Regime-Autopilot auto_chain.

Läuft gegen die öffentliche Preview-URL (REACT_APP_BACKEND_URL). Am Ende werden
Guard-Config-Werte auf die Defaults (True/True/12) zurückgesetzt und alle
gestarteten Jobs (Optimizer + Autopilot) sauber abgebrochen.
"""
import os
import time
import uuid

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://signal-guardian-6.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

session = requests.Session()
session.headers.update({"Content-Type": "application/json"})


@pytest.fixture(scope="module")
def admin_token():
    r = session.post(f"{BASE}/api/auth/login",
                     json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=120)
    assert r.status_code == 200, f"login failed {r.status_code}: {r.text[:200]}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def admin_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def worker_token(admin_headers):
    r = session.get(f"{BASE}/api/localworker/token", headers=admin_headers, timeout=120)
    assert r.status_code == 200, r.text
    return r.json()["token"]


# ---------------- General ----------------
def test_api_root_status():
    for url in (f"{BASE}/api", f"{BASE}/api/"):
        r = session.get(url, timeout=120)
        assert r.status_code == 200, url
        j = r.json()
        assert "app" in j and "status" in j


# ---------------- Guard-Shadow ----------------
def test_guard_shadow_stats_shape():
    r = session.get(f"{BASE}/api/ai/guard-shadow/stats", timeout=120)
    assert r.status_code == 200, r.text[:300]
    d = r.json()
    for k in ("days", "open_shadows", "closed", "guards", "fee_guard_detail",
              "recent", "adjustments", "baseline", "last_adjust_ts"):
        assert k in d, f"missing key {k}"
    assert isinstance(d["guards"], list)
    for row in d.get("recent", []):
        assert "_id" not in row


def test_ai_config_guard_toggle(admin_headers):
    # Umstellen auf false/false/20
    payload = {"guard_shadow_enabled": False, "guard_autotune_enabled": False,
               "guard_autotune_min_samples": 20}
    r = session.post(f"{BASE}/api/ai/config", json=payload, headers=admin_headers, timeout=120)
    assert r.status_code == 200, r.text[:300]
    cfg = r.json().get("config", r.json())
    assert cfg.get("guard_shadow_enabled") is False
    assert cfg.get("guard_autotune_enabled") is False
    assert cfg.get("guard_autotune_min_samples") == 20
    # Reset auf Defaults
    reset = {"guard_shadow_enabled": True, "guard_autotune_enabled": True,
             "guard_autotune_min_samples": 12}
    r = session.post(f"{BASE}/api/ai/config", json=reset, headers=admin_headers, timeout=120)
    assert r.status_code == 200
    cfg = r.json().get("config", r.json())
    assert cfg.get("guard_shadow_enabled") is True
    assert cfg.get("guard_autotune_enabled") is True
    assert cfg.get("guard_autotune_min_samples") == 12


# ---------------- Worker Package Manifest ----------------
def test_worker_package_manifest():
    r = session.get(f"{BASE}/api/localworker/package/manifest", timeout=120)
    assert r.status_code == 200, r.text[:200]
    assert r.json().get("required_version") == "1.13.0"


def test_localworker_status_required_version():
    r = session.get(f"{BASE}/api/localworker/status", timeout=120)
    assert r.status_code == 200
    assert r.json().get("required_version") == "1.13.0"


# ---------------- Fake-Worker Restart-Detection ----------------
def _poll(worker_token, worker_id, running=None):
    body = {"worker_id": worker_id, "name": "qa", "version": "1.13.0",
            "running_jobs": list(running or []), "want_compute": True,
            "want_data": False, "resources": {}, "data": {}}
    r = session.post(f"{BASE}/api/worker/poll", json=body,
                     headers={"X-Worker-Token": worker_token,
                              "Content-Type": "application/json"}, timeout=120)
    return r


def test_worker_restart_requeues_job(admin_headers, worker_token):
    worker_id = f"qa-w-{uuid.uuid4().hex[:6]}"
    # (1) Worker anmelden
    r = _poll(worker_token, worker_id)
    assert r.status_code == 200, r.text[:300]

    # kurz warten damit worker als online geführt wird
    time.sleep(1)
    r = session.get(f"{BASE}/api/localworker/status", timeout=120)
    workers = r.json().get("workers", [])
    assert any(w.get("worker_id") == worker_id or w.get("id") == worker_id
               for w in workers), f"worker not visible: {workers}"

    # (2) Local Optimizer-Job starten (params, kleiner Umfang)
    body = {"mode": "params", "strategy_id": "scalping_4_rules", "symbols": ["BTCUSDT"],
            "days": 3, "timeframe": "1m", "iterations": 4,
            "execution": "local"}
    r = session.post(f"{BASE}/api/optimizer/run", json=body,
                     headers=admin_headers, timeout=120)
    if r.status_code == 409:
        pytest.skip(f"Another optimizer already running: {r.text[:200]}")
    assert r.status_code == 200, r.text[:400]
    job_id = r.json()["job_id"]

    try:
        # (3) Poll erneut -> Job sollte geclaimt werden
        claimed = None
        for _ in range(6):
            r = _poll(worker_token, worker_id)
            assert r.status_code == 200
            j = (r.json() or {}).get("job") or {}
            if j.get("job_id") == job_id:
                claimed = j
                break
            time.sleep(1)
        assert claimed, f"job {job_id} was not claimed by fake worker"

        # (4) Warten damit LOST_JOB_GRACE (20 s) abläuft
        time.sleep(22)

        # (5) >=3 Polls ohne running_jobs -> requeue oder Re-Claim
        reclaimed = False
        for _ in range(6):
            rp = _poll(worker_token, worker_id, running=[])
            jj = (rp.json() or {}).get("job") or {}
            if jj.get("job_id") == job_id:
                reclaimed = True  # Job wurde nach Requeue erneut vergeben
            time.sleep(1)

        # (6) Status prüfen: entweder Phase enthält 'neu gestartet' / 'übernommen'
        #     oder Job wurde erneut geclaimt
        r = session.get(f"{BASE}/api/optimizer/status/{job_id}", timeout=120)
        assert r.status_code == 200, r.text
        js = r.json()
        phase = (js.get("phase") or "").lower()
        ok = reclaimed or ("neu gestartet" in phase) or ("übernommen" in phase)
        assert ok, \
            f"job weder requeued noch reclaimed. phase='{phase}' status={js.get('status')}"
    finally:
        # (7) Aufräumen
        session.post(f"{BASE}/api/optimizer/cancel/{job_id}",
                     headers=admin_headers, timeout=120)


# ---------------- Regime-Autopilot auto_chain ----------------
def test_regime_autopilot_accepts_auto_chain(admin_headers):
    body = {"symbols": ["BTCUSDT"], "timeframe": "15m", "days": 30,
            "train_pct": 75, "engine_config": {}, "max_minutes": 1,
            "max_rounds": 2, "auto_chain": False, "execution": "cloud"}
    r = session.post(f"{BASE}/api/regime-lab/autopilot", json=body,
                     headers=admin_headers, timeout=120)
    assert r.status_code == 200, r.text[:400]
    job_id = r.json().get("job_id") or r.json().get("id")
    assert job_id

    try:
        # Status kurz prüfen
        r = session.get(f"{BASE}/api/regime-lab/status/{job_id}", timeout=120)
        assert r.status_code == 200
        st = r.json()
        assert "status" in st or "phase" in st
    finally:
        # Cleanup: erst /stop, alternativ /cancel
        stopped = session.post(f"{BASE}/api/regime-lab/autopilot/stop/{job_id}",
                               headers=admin_headers, timeout=120)
        if stopped.status_code >= 400:
            session.post(f"{BASE}/api/regime-lab/cancel/{job_id}",
                         headers=admin_headers, timeout=120)
