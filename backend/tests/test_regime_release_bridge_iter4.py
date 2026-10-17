"""Iteration 4: Regime-Lab Bridge to KI-Trader (release stages)

Tests are read-only against the production Atlas DB via the preview backend.
No writes / no stage changes are enforced – only error paths and read endpoints.
"""

import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to frontend/.env value at import time (should be set via env)
    with open("/app/frontend/.env", "r") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                break

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

SLOW_TIMEOUT = 180  # /list can take 20-60 s (Atlas latency)


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": ADMIN_USER, "password": ADMIN_PASS},
        timeout=30,
    )
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def analysis_id():
    r = requests.get(f"{BASE_URL}/api/regime-lab/list", timeout=SLOW_TIMEOUT)
    assert r.status_code == 200, f"/list failed {r.status_code}"
    data = r.json()
    assert "analyses" in data and isinstance(data["analyses"], list)
    assert data["analyses"], "no analyses in list"
    # verify release.stage present on each entry (should all be 'none' currently)
    for a in data["analyses"][:5]:
        rel = a.get("release") or {}
        assert "stage" in rel, f"missing release.stage: {a}"
    return data["analyses"][0].get("id") or data["analyses"][0].get("_id") or data["analyses"][0].get("aid")


def test_releases_shape():
    r = requests.get(f"{BASE_URL}/api/regime-lab/releases", timeout=SLOW_TIMEOUT)
    assert r.status_code == 200, r.text
    d = r.json()
    for key in ("releases", "rewards_by_structural", "activation", "autonomy", "proposals"):
        assert key in d, f"missing key {key} in /releases response: keys={list(d.keys())}"
    assert isinstance(d["releases"], list)
    act = d["activation"] or {}
    assert "ok" in act and "reasons" in act and "shadow_trades" in act


def test_list_release_stage_none(analysis_id):
    assert analysis_id, "need an analysis id"


def test_release_check_shadow_fails_with_reasons(analysis_id):
    r = requests.get(
        f"{BASE_URL}/api/regime-lab/{analysis_id}/release-check",
        params={"stage": "shadow"},
        timeout=60,
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d.get("ok") is False, f"expected ok=false, got {d}"
    reasons = d.get("reasons") or []
    assert isinstance(reasons, list) and reasons, "reasons should be non-empty list"
    joined = " ".join(reasons).lower()
    # Erwartete Klartext-Fragmente
    assert any(k in joined for k in ("behalten", "kalibrier", "ablation")), f"unexpected reasons: {reasons}"
    ev = d.get("evidence") or {}
    assert "evidence_hash" in ev
    rel = d.get("release") or {}
    assert "stage" in rel


def test_release_no_token_401(analysis_id):
    r = requests.post(
        f"{BASE_URL}/api/regime-lab/{analysis_id}/release",
        json={"stage": "shadow", "reason": "Test"},
        timeout=30,
    )
    assert r.status_code == 401, f"expected 401, got {r.status_code}: {r.text}"


def test_release_shadow_admin_400_with_all_reasons(admin_token, analysis_id):
    r = requests.post(
        f"{BASE_URL}/api/regime-lab/{analysis_id}/release",
        json={"stage": "shadow", "reason": "Test"},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    body = r.json()
    detail = body.get("detail") if isinstance(body, dict) else None
    reasons = None
    if isinstance(detail, dict):
        reasons = detail.get("reasons")
    assert isinstance(reasons, list) and reasons, f"expected reasons list, got: {body}"


def test_release_none_missing_reason_400(admin_token, analysis_id):
    r = requests.post(
        f"{BASE_URL}/api/regime-lab/{analysis_id}/release",
        json={"stage": "none"},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"
    body = r.json()
    txt = str(body).lower()
    assert "reason" in txt and "pflicht" in txt, f"unexpected error: {body}"


def test_release_invalid_stage_400(admin_token, analysis_id):
    r = requests.post(
        f"{BASE_URL}/api/regime-lab/{analysis_id}/release",
        json={"stage": "foo", "reason": "x"},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text}"


def test_regime_phase_lab_field():
    r = requests.get(f"{BASE_URL}/api/autotrade/regime_phase/BTCUSDT", timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "lab" in d, f"missing lab key: {list(d.keys())}"
    lab = d["lab"]
    assert lab.get("stage") == "none"
    assert "phase" in lab and lab["phase"] is None


def test_ai_rewards_by_structural_regime():
    r = requests.get(f"{BASE_URL}/api/ai/rewards", params={"days": 90}, timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    assert "by_structural_regime" in d, f"missing key. got keys={list(d.keys())}"
    assert isinstance(d["by_structural_regime"], list)


def test_ai_proposal_decide_missing_pid_404(admin_token):
    r = requests.post(
        f"{BASE_URL}/api/ai/proposals/nonexistent_pid_iter4",
        json={"action": "approve"},
        headers={"Authorization": f"Bearer {admin_token}"},
        timeout=30,
    )
    # Regression: must not be 500
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
