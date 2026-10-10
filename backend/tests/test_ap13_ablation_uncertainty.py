"""AP13 tests: Ablation job + uncertainty calibration in analyze."""
import os
import time
import pytest
import requests

BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or "http://localhost:8001").rstrip("/")
USER = os.environ.get("ADMIN_USER", "Admin")
PW = os.environ.get("ADMIN_PASSWORD", "Dean06Greif!Dev")


def _backend_reachable() -> bool:
    try:
        return requests.get(f"{BASE_URL}/api/health", timeout=5).status_code == 200
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _backend_reachable(),
                                reason="Backend nicht erreichbar – E2E übersprungen")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": USER, "password": PW}, timeout=60)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


def _wait_job(job_id, timeout=180):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}", timeout=15)
        assert r.status_code == 200, r.text
        j = r.json()
        last = j
        st = j.get("status")
        if st in ("done", "error", "cancelled", "failed"):
            return j
        time.sleep(3)
    pytest.fail(f"Job {job_id} timed out. Last: {last}")


def test_auth_ok(token):
    assert isinstance(token, str) and len(token) > 10


def test_ablation_requires_auth():
    r = requests.post(f"{BASE_URL}/api/regime-lab/ablation",
                      json={"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 30},
                      timeout=15)
    assert r.status_code in (401, 403)


def test_regression_endpoints(auth_headers):
    r = requests.get(f"{BASE_URL}/api/health", timeout=10)
    assert r.status_code == 200
    r = requests.get(f"{BASE_URL}/api/safety/status", headers=auth_headers, timeout=15)
    assert r.status_code == 200
    r = requests.get(f"{BASE_URL}/api/regime-lab/list", headers=auth_headers, timeout=15)
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, (list, dict))


def test_ema_compare_start_cancel(auth_headers):
    """Regression: EMA-compare still starts, cancel immediately to keep lane free."""
    r = requests.post(f"{BASE_URL}/api/regime-lab/ema-compare",
                      headers=auth_headers,
                      json={"symbols": ["BTCUSDT"], "timeframe": "1h",
                            "days": 30, "train_pct": 75,
                            "engine_config": {"detector": "reactive"}},
                      timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    time.sleep(1)
    c = requests.post(f"{BASE_URL}/api/regime-lab/cancel/{job_id}",
                      headers=auth_headers, timeout=15)
    assert c.status_code in (200, 409)
    # Wait until it's not running anymore
    for _ in range(30):
        s = requests.get(f"{BASE_URL}/api/regime-lab/status/{job_id}", timeout=10).json()
        if s.get("status") in ("cancelled", "done", "error", "failed"):
            break
        time.sleep(2)


def test_ablation_full_run(auth_headers):
    body = {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 90,
            "train_pct": 75, "engine_config": {"detector": "reactive"}}
    r = requests.post(f"{BASE_URL}/api/regime-lab/ablation",
                      headers=auth_headers, json=body, timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    res = _wait_job(job_id, timeout=240)
    assert res.get("status") == "done", res
    analysis_id = res.get("analysis_id") or res.get("result", {}).get("analysis_id")
    # Fetch full result
    result = res.get("result") or {}
    if not result and analysis_id:
        d = requests.get(f"{BASE_URL}/api/regime-lab/{analysis_id}",
                         headers=auth_headers, timeout=15).json()
        result = d.get("result") or d.get("analysis") or d
    assert result.get("kind") == "ablation", result
    rows = result.get("rows") or []
    assert rows and rows[0].get("variant_key") == "full", rows[:2]
    keys = {r.get("variant_key") for r in rows}
    for k in ("full", "no_mtf", "no_volume", "no_ema_confirm", "alt_ema"):
        assert k in keys, f"missing variant {k}: {keys}"
    for row in rows:
        if row.get("error"):
            continue
        pool = row.get("pooling") or {}
        classes = pool.get("classes") or {}
        crypto = classes.get("crypto") or {}
        assert "direction_pct" in crypto, f"no direction_pct in {row.get('variant_key')}: {pool}"
    assert result.get("best_variant") in keys
    assert result.get("selection_basis") in ("inner_validation", "train_only")
    assert result.get("holdout_role") == "final_test"
    verdicts = result.get("verdicts") or {}
    # verdicts is a dict keyed by variant_key (non-full variants only)
    assert isinstance(verdicts, dict) and verdicts, f"expected verdicts dict: {verdicts}"
    for k in ("no_mtf", "no_volume", "no_ema_confirm"):
        assert k in verdicts, f"missing verdict for {k}: {list(verdicts.keys())}"
        assert "delta_pp" in verdicts[k] and "verdict" in verdicts[k]
    # attempt_no increments on second run
    r2 = requests.post(f"{BASE_URL}/api/regime-lab/ablation",
                       headers=auth_headers, json=body, timeout=30)
    assert r2.status_code == 200
    job2 = r2.json()["job_id"]
    res2 = _wait_job(job2, timeout=240)
    assert res2.get("status") == "done"
    result2 = res2.get("result") or {}
    if not result2:
        aid2 = res2.get("analysis_id")
        d = requests.get(f"{BASE_URL}/api/regime-lab/{aid2}", headers=auth_headers, timeout=15).json()
        result2 = d.get("result") or d.get("analysis") or d
    a1 = result.get("attempt_no") or (result.get("rows", [{}])[0].get("attempt_no"))
    a2 = result2.get("attempt_no") or (result2.get("rows", [{}])[0].get("attempt_no"))
    if a1 is not None and a2 is not None:
        assert a2 > a1, f"attempt_no did not increment: {a1} -> {a2}"


def test_analyze_uncertainty(auth_headers):
    body = {"symbols": ["BTCUSDT"], "timeframe": "15m", "days": 30,
            "scope": "combined", "train_pct": 80}
    r = requests.post(f"{BASE_URL}/api/regime-lab/analyze",
                      headers=auth_headers, json=body, timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    res = _wait_job(job_id, timeout=180)
    assert res.get("status") == "done", res
    analysis_id = res.get("analysis_id") or (res.get("result") or {}).get("analysis_id")
    assert analysis_id
    d = requests.get(f"{BASE_URL}/api/regime-lab/{analysis_id}",
                     headers=auth_headers, timeout=15).json()
    analysis = d.get("analysis") or (d.get("result") or {}).get("analysis") or d
    combined = analysis.get("combined") or {}
    unc = combined.get("uncertainty") or {}
    assert unc, f"no combined.uncertainty: {combined.keys()}"
    assert unc.get("score_is_calibrated_probability") is False
    per_sym = unc.get("per_symbol") or {}
    btc = per_sym.get("BTCUSDT") or {}
    for k in ("basis", "n", "gap_pp", "verdict", "bins"):
        assert k in btc, f"missing {k} in per_symbol.BTCUSDT: {btc}"
