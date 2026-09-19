"""Iteration 9 – Integration tests via public REACT_APP_BACKEND_URL.

Covers:
- Admin login
- Worker package/manifest (v1.12.0) + regime_autopilot present
- Worker reconnect / offline-hint behaviour (heartbeat clears hint,
  progress w/o phase restores previous phase)
- Regime-Autopilot cloud run (start, active, done, stop 409 vs 200)
- Job series accepts regime kinds incl. regime_autopilot + regime_ablation
- Optimizer indicator pool (new indicators run to done)
"""
from __future__ import annotations

import io
import os
import time
import zipfile
from typing import Optional

import pytest
import requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


# ----------------- fixtures -----------------
@pytest.fixture(scope="session")
def sess():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="session")
def admin_token(sess):
    r = sess.post(f"{BASE}/api/auth/login",
                  json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, r.json()
    return tok


@pytest.fixture(scope="session")
def admin(sess, admin_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {admin_token}"})
    return s


@pytest.fixture(scope="session")
def worker_token(admin):
    r = admin.get(f"{BASE}/api/localworker/token", timeout=15)
    assert r.status_code == 200, r.text
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="session")
def worker(worker_token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "X-Worker-Token": worker_token})
    return s


# ----------------- 1) auth -----------------
def test_admin_login_ok(sess):
    r = sess.post(f"{BASE}/api/auth/login",
                  json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=45)
    assert r.status_code == 200
    j = r.json()
    assert (j.get("token") or j.get("access_token"))


# ----------------- 2) worker package v1.12.0 -----------------
def test_localworker_required_version(admin):
    r = admin.get(f"{BASE}/api/localworker/status", timeout=15)
    assert r.status_code == 200
    assert r.json().get("required_version") == "1.12.0"


def test_localworker_manifest_has_autopilot(admin):
    r = admin.get(f"{BASE}/api/localworker/package/manifest", timeout=15)
    assert r.status_code == 200
    j = r.json()
    assert j.get("required_version") == "1.12.0"
    fh = j.get("file_hashes") or {}
    assert "services/regime_autopilot.py" in fh, list(fh.keys())[:20]


def test_localworker_package_zip_worker_version(admin):
    r = admin.get(f"{BASE}/api/localworker/package", timeout=30)
    assert r.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(r.content))
    names = zf.namelist()
    wp = next((n for n in names if n.endswith("worker.py")), None)
    assert wp, names[:20]
    txt = zf.read(wp).decode("utf-8", "ignore")
    assert 'VERSION = "1.12.0"' in txt


# ----------------- 3) worker reconnect bugfix -----------------
def _poll(worker, running=None, jid_hint=None):
    body = {
        "worker_id": "qa-w1", "name": "QA", "version": "1.12.0",
        "resources": {"cores": 4}, "gpu": {"available": False},
        "data": {"symbols": []}, "running_jobs": running or [],
        "want_compute": True, "want_data": True,
    }
    r = worker.post(f"{BASE}/api/worker/poll", json=body, timeout=15)
    assert r.status_code == 200, r.text
    return r.json()


def test_worker_reconnect_offline_hint_flow(admin, worker):
    # 1) heartbeat
    _poll(worker)
    # 2) start local optimizer explore job
    body = {"mode": "explore", "symbols": ["BTCUSDT"], "days": 3,
            "timeframe": "15m", "objective": "combo", "iterations": 5,
            "min_trades": 1, "max_rules": 3,
            "indicators": ["rsi", "macd_hist", "ema_fast"],
            "optimize": {"tpsl": False}, "execution": "local",
            "explore": {"target_champions": 1, "max_minutes": 1},
            "walk_forward": {"enabled": True, "train_pct": 75, "mode": "single"}}
    r = admin.post(f"{BASE}/api/optimizer/run", json=body, timeout=20)
    assert r.status_code == 200, r.text
    jid = r.json().get("job_id") or r.json().get("id")
    assert jid

    # 3) claim via poll
    jid_claimed = None
    for _ in range(15):
        p = _poll(worker)
        job = p.get("job")
        if job and job.get("job_id") == jid:
            jid_claimed = jid
            break
        time.sleep(1)
    assert jid_claimed, f"worker did not claim job {jid}"

    try:
        # 4) send progress with phase
        phase = "Endlos-Suche · 50 Kombis"
        r = worker.post(f"{BASE}/api/worker/job/{jid}/progress",
                        json={"progress": 40, "phase": phase}, timeout=15)
        assert r.status_code == 200
        time.sleep(0.5)
        s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
        assert s.get("phase") == phase, s.get("phase")

        # 5) go offline, wait for stale-hint (>30s silence)
        time.sleep(45)
        s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
        hint_phase = s.get("phase") or ""
        assert "Verbindung" in hint_phase and "unterbroch" in hint_phase, hint_phase

        # 6) reconnect with running_jobs -> phase restored (no progress post)
        restored = False
        for _ in range(12):
            _poll(worker, running=[jid])
            time.sleep(1)
            s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
            if s.get("phase") == phase:
                restored = True
                break
        assert restored, f"phase not restored: {s.get('phase')}"

        # 7) go offline again, then progress w/o phase -> phase restored
        time.sleep(45)
        s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
        assert "Verbindung" in (s.get("phase") or "")
        r = worker.post(f"{BASE}/api/worker/job/{jid}/progress",
                        json={"progress": 41}, timeout=15)
        assert r.status_code == 200
        time.sleep(1)
        s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
        assert s.get("phase") == phase, s.get("phase")
    finally:
        # 8) cleanup – send cancelled result
        try:
            worker.post(f"{BASE}/api/worker/job/{jid}/result",
                        json={"kind": "optimizer", "status": "cancelled"}, timeout=15)
        except Exception:
            pass
        try:
            admin.post(f"{BASE}/api/optimizer/cancel/{jid}", timeout=15)
        except Exception:
            pass


# ----------------- 4) Regime-Autopilot cloud -----------------
def _wait_regime_done(admin, jid, timeout=180):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        r = admin.get(f"{BASE}/api/regime-lab/status/{jid}", timeout=15)
        if r.status_code == 200:
            last = r.json()
            if last.get("status") in ("done", "error", "cancelled"):
                return last
        time.sleep(3)
    return last


def test_regime_autopilot_cloud_run(admin):
    body = {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60,
            "train_pct": 75, "engine_config": {"detector": "kombi"},
            "max_rounds": 4, "search_detectors": True}
    r = admin.post(f"{BASE}/api/regime-lab/autopilot", json=body, timeout=20)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("status") == "started"
    jid = j.get("job_id")
    assert jid

    # active shows kind autopilot
    time.sleep(1)
    act = admin.get(f"{BASE}/api/regime-lab/active", timeout=15).json()
    assert (act.get("kind") == "autopilot") or (
        isinstance(act, dict) and "autopilot" in str(act).lower()), act

    final = _wait_regime_done(admin, jid, timeout=240)
    assert final and final.get("status") == "done", final
    res = final.get("result") or {}
    assert res.get("kind") == "autopilot", res
    best = res.get("best") or {}
    assert best.get("engine_config"), best
    metrics = best.get("metrics") or {}
    for k in ("inner_direction_pct", "holdout_direction_pct", "avg_live_phase_days"):
        assert k in metrics, (k, metrics)
    assert "baseline" in res
    assert res.get("tested") == 4, res.get("tested")
    assert res.get("stop_reason") == "rounds_limit", res.get("stop_reason")
    assert "selection_basis" in res

    # runs list
    runs = admin.get(f"{BASE}/api/regime-lab/autopilot/runs", timeout=15).json()
    items = runs if isinstance(runs, list) else runs.get("items") or runs.get("runs") or []
    assert any((it.get("job_id") == jid) or (it.get("id") == jid) for it in items), items

    # stop on finished -> 409
    r = admin.post(f"{BASE}/api/regime-lab/autopilot/stop/{jid}", timeout=15)
    assert r.status_code == 409, (r.status_code, r.text)


def test_regime_autopilot_stop_running(admin):
    body = {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60,
            "train_pct": 75, "engine_config": {"detector": "kombi"},
            "max_rounds": 0, "max_minutes": 5, "search_detectors": False}
    r = admin.post(f"{BASE}/api/regime-lab/autopilot", json=body, timeout=20)
    assert r.status_code == 200, r.text
    jid = r.json().get("job_id")
    assert jid
    time.sleep(4)
    r = admin.post(f"{BASE}/api/regime-lab/autopilot/stop/{jid}", timeout=15)
    assert r.status_code == 200, (r.status_code, r.text)
    final = _wait_regime_done(admin, jid, timeout=90)
    assert final and final.get("status") == "done", final
    res = final.get("result") or {}
    assert res.get("stop_reason") == "stopped_by_user", res.get("stop_reason")


# ----------------- 5) Job series regime kinds -----------------
def test_series_add_regime_kinds(admin):
    created = []
    try:
        pairs = [
            ("regime_ablation", "Indikator-Ablation"),
            ("regime_autopilot", "Regime-Autopilot"),
            ("regime_calibration", None),
            ("regime_kombi", None),
            ("regime_ema_compare", None),
        ]
        for kind, needle in pairs:
            inner = {"symbols": ["BTCUSDT"], "timeframe": "1h",
                     "days": 60, "train_pct": 75, "engine_config": {}}
            r = admin.post(f"{BASE}/api/series/add",
                           json={"kind": kind, "body": inner}, timeout=15)
            assert r.status_code == 200, (kind, r.status_code, r.text)
            j = r.json()
            item = j.get("item") or j
            iid = item.get("id") or j.get("id")
            assert iid, (kind, j)
            created.append(iid)
            if needle:
                lbl = item.get("label") or ""
                assert needle in lbl, (kind, lbl)

        # unknown kind -> 400
        r = admin.post(f"{BASE}/api/series/add",
                       json={"kind": "foo", "body": {"symbols": ["BTCUSDT"]}}, timeout=15)
        assert r.status_code == 400, (r.status_code, r.text)

        # list
        r = admin.get(f"{BASE}/api/series", timeout=15)
        assert r.status_code == 200
        listed = r.json()
        items = listed if isinstance(listed, list) else (
            listed.get("items") or listed.get("series") or [])
        ids = {it.get("id") for it in items}
        for iid in created:
            assert iid in ids, (iid, ids)
    finally:
        for iid in created:
            try:
                admin.delete(f"{BASE}/api/series/{iid}", timeout=15)
            except Exception:
                pass


# ----------------- 6) Optimizer indicator pool -----------------
def test_optimizer_new_indicator_pool_runs(admin):
    body = {"mode": "discovery", "symbols": ["BTCUSDT"], "days": 3,
            "timeframe": "15m", "objective": "combo", "iterations": 5,
            "min_trades": 1, "max_rules": 2,
            "indicators": ["adx", "plus_di", "cci", "supertrend_dir", "chop"],
            "optimize": {"tpsl": False}, "execution": "cloud"}
    r = admin.post(f"{BASE}/api/optimizer/run", json=body, timeout=20)
    assert r.status_code == 200, r.text
    jid = r.json().get("job_id") or r.json().get("id")
    assert jid
    end = time.time() + 180
    final: Optional[dict] = None
    while time.time() < end:
        s = admin.get(f"{BASE}/api/optimizer/status/{jid}", timeout=15).json()
        if s.get("status") in ("done", "error", "cancelled"):
            final = s
            break
        time.sleep(3)
    assert final and final.get("status") == "done", final
    assert not final.get("error"), final.get("error")
