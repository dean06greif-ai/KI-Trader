"""Backend API tests for /api/ai/playbook/backtest endpoints (iter 55)."""
import os
import time
import requests
import pytest

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://signal-quality-3.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, f"no token in response: {r.json()}"
    return tok


def test_overview_public_no_auth():
    r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    for k in ("classes", "last_result", "eligible", "variants", "param_help", "modes", "ai_defaults", "rules"):
        assert k in data, f"missing field {k}"
    ph = data["param_help"]
    assert isinstance(ph, dict) and len(ph) > 0
    # Setups with tp1_r
    for s in ("breakout", "trend_pullback"):
        if s in ph:
            assert "tp1_r" in ph[s], f"{s} should have tp1_r"
            assert "max_bars" in ph[s]
            assert "sides" in ph[s] or "htf_trend" in ph[s]
    # Setups WITHOUT tp1_r
    for s in ("mean_reversion", "range_fade", "htf_range"):
        if s in ph:
            assert "tp1_r" not in ph[s], f"{s} should NOT have tp1_r"


def test_run_requires_auth():
    r = requests.post(
        f"{BASE_URL}/api/ai/playbook/backtest/run",
        json={"asset_classes": ["crypto"], "setups": ["mean_reversion"], "days": 30, "mode": "single", "ai_revise": False},
        timeout=30,
    )
    assert r.status_code in (401, 403), f"expected 401/403, got {r.status_code}: {r.text}"


def test_run_single_mean_reversion_and_status(token):
    headers = {"Authorization": f"Bearer {token}"}
    body = {"asset_classes": ["crypto"], "setups": ["mean_reversion"], "days": 30, "mode": "single", "ai_revise": False}
    # Handle 409 already running
    job_id = None
    for _ in range(6):
        r = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/run", json=body, headers=headers, timeout=30)
        if r.status_code == 409:
            time.sleep(10)
            continue
        assert r.status_code == 200, f"run failed: {r.status_code} {r.text}"
        j = r.json()
        assert j.get("status") == "started"
        job_id = j.get("job_id")
        assert job_id
        break
    assert job_id, "could not start job (409 loop)"

    # Poll status
    final = None
    for _ in range(90):  # up to ~4.5 min
        try:
            s = requests.get(f"{BASE_URL}/api/ai/playbook/backtest/status/{job_id}", headers=headers, timeout=30)
        except requests.RequestException:
            time.sleep(3)
            continue
        if s.status_code >= 500:
            time.sleep(3)
            continue
        assert s.status_code == 200, s.text
        js = s.json()
        st = js.get("status")
        if st in ("done", "error", "failed"):
            final = js
            break
        time.sleep(3)
    assert final is not None, "job did not finish in time"
    assert final.get("status") == "done", f"job did not succeed: {final}"

    # Rows should include diag + lessons
    rows = final.get("rows") or final.get("result", {}).get("rows") or []
    assert rows, f"no rows in final result: {final}"
    row = rows[0]
    assert "diag" in row, f"row missing diag: {row.keys()}"
    diag = row["diag"]
    assert "is" in diag and "oos" in diag, diag
    assert "n" in diag["is"] and "n" in diag["oos"]
    assert "lessons" in row, f"row missing lessons: {row.keys()}"
    assert isinstance(row["lessons"], list)

    # After run, overview.history should include params/diag
    ov = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=30).json()
    hist = ((ov.get("classes") or {}).get("crypto") or {}).get("mean_reversion", {}).get("history") or []
    assert hist, "no history for mean_reversion after run"
    last = hist[-1]
    assert "params" in last and isinstance(last["params"], dict)
    assert "diag" in last
