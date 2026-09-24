"""Backend E2E tests for iteration 26 review request.

Covers:
 - GET /api/regime-lab/engine/defaults (jump keys + CONFIG_GROUPS meta)
 - GET /api/telegram/notify-catalog (signals_only_traded, signals_collection)
 - POST /api/regime-lab/analyze + GET /api/regime-lab/{id}
 - POST /api/regime-lab/autopilot + polling status
"""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "PreviewAdmin123"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_regime_engine_defaults(headers):
    r = requests.get(f"{BASE_URL}/api/regime-lab/engine/defaults", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    cfg = data.get("config") or data.get("defaults") or data
    # jump keys should be somewhere under config
    flat = str(data)
    for key, expected in [("jump_fast_days", 1.0),
                          ("jump_slow_days", 14.0),
                          ("jump_center", 0.6),
                          ("jump_penalty_days", 1.5)]:
        assert key in flat, f"Missing {key} in defaults: {flat[:800]}"
    # meta / groups
    meta = data.get("meta") or data.get("config_meta") or {}
    groups = data.get("groups") or data.get("config_groups") or {}
    combined = str(meta) + str(groups)
    assert "Jump-Modell" in combined, f"Expected 'Jump-Modell' group not found. meta+groups sample: {combined[:1000]}"


def test_notify_catalog_new_keys(headers):
    r = requests.get(f"{BASE_URL}/api/telegram/notify-catalog", headers=headers, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    txt = str(data)
    assert "signals_only_traded" in txt, "signals_only_traded missing"
    assert "signals_collection" in txt, "signals_collection missing"


def test_regime_lab_analyze_jump(headers):
    body = {
        "symbols": ["BTCUSDT"],
        "timeframe": "4h",
        "days": 400,
        "train_pct": 75,
        "scope": "combined",
        "engine": "v2",
        "engine_config": {"detector": "jump", "regime_mode": 3},
        "name": "Jump QA",
    }
    r = requests.post(f"{BASE_URL}/api/regime-lab/analyze",
                      headers=headers, json=body, timeout=60)
    assert r.status_code == 200, r.text
    resp = r.json()
    # analyze may be async (job) or sync (analysis_id)
    analysis_id = resp.get("analysis_id") or resp.get("id")
    job_id = resp.get("job_id")
    if not analysis_id and job_id:
        # poll status
        for _ in range(120):
            s = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}",
                             headers=headers, timeout=30)
            if s.status_code == 200:
                sd = s.json()
                status = sd.get("status")
                if status in ("done", "completed", "finished", "success"):
                    analysis_id = sd.get("analysis_id") or (sd.get("result") or {}).get("analysis_id")
                    break
                if status in ("error", "failed"):
                    pytest.fail(f"analyze job failed: {sd}")
            time.sleep(2)
    assert analysis_id, f"No analysis_id returned. resp={resp}"
    g = requests.get(f"{BASE_URL}/api/regime-lab/{analysis_id}", headers=headers, timeout=60)
    assert g.status_code == 200, g.text
    ga = g.json()
    assert "analysis" in ga or "quality" in ga or "regimes" in ga, f"unexpected shape: {list(ga.keys())}"


def test_regime_lab_autopilot(headers):
    # Wait for any running job to finish first
    for _ in range(60):
        j = requests.get(f"{BASE_URL}/api/regime-lab/jobs", headers=headers, timeout=15)
        if j.status_code != 200:
            break
        active = [x for x in (j.json() if isinstance(j.json(), list) else j.json().get("jobs", []))
                  if str(x.get("status", "")).lower() in ("running", "pending", "queued")]
        if not active:
            break
        time.sleep(3)

    body = {
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "timeframe": "4h",
        "days": 400,
        "train_pct": 75,
        "engine_config": {"detector": "kombi", "regime_mode": 3},
        "max_rounds": 30,
        "seed": 5,
        "min_phase_days_target": 4,
        "max_phase_days_target": 14,
        "auto_chain": False,
    }
    r = requests.post(f"{BASE_URL}/api/regime-lab/autopilot",
                      headers=headers, json=body, timeout=60)
    assert r.status_code == 200, r.text
    resp = r.json()
    job_id = resp.get("job_id") or resp.get("id")
    assert job_id, f"no job_id: {resp}"

    result = None
    for _ in range(180):  # up to ~9 min
        s = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}",
                         headers=headers, timeout=30)
        assert s.status_code == 200
        sd = s.json()
        status = str(sd.get("status", "")).lower()
        if status in ("done", "completed", "finished", "success"):
            result = sd.get("result") or sd
            break
        if status in ("error", "failed"):
            pytest.fail(f"autopilot failed: {sd}")
        time.sleep(3)
    assert result, "autopilot didn't finish in time"

    txt = str(result)
    assert "holdout_reference_f1_pct" in txt, "holdout_metric holdout_reference_f1_pct missing"
    best = result.get("best") or (result.get("result") or {}).get("best") or {}
    print("best.detector =", best.get("detector"))
    assert best.get("detector") == "jump", f"expected jump, got {best.get('detector')}"
    # adopt_recommended
    adopt = result.get("adopt_recommended")
    if adopt is None:
        adopt = (result.get("result") or {}).get("adopt_recommended")
    assert adopt is True, f"adopt_recommended not true: {adopt}"
    assert not result.get("error"), f"error present: {result.get('error')}"
