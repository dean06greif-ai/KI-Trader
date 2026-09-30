"""Iteration 33: End-to-end API test of Regime-Lab label_basis, walkforward with assignment gating,
and dynamic strategy build (mapping regime_strategies)."""
import os, time, requests, pytest

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrader-audit.preview.emergentagent.com").rstrip("/")
ANALYSIS_ID = "ra_cceda77f"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE}/api/auth/login", json={"username": "Admin", "password": "LocalTest06!"}, timeout=30)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _poll(job_id, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/api/regime-lab/status/{job_id}", timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        if j.get("status") in ("done", "error", "failed"):
            return j
        time.sleep(2)
    pytest.fail(f"job {job_id} timeout")


def _post_optimize(headers, label_basis=None):
    body = {
        "scope": "combined",
        "regime_id": 2,
        "mode": "params",
        "strategy_id": "nnfx_trend",
        "iterations": 5,
        "min_trades": 3,
        "regime_walk_forward": False,
    }
    if label_basis is not None:
        body["label_basis"] = label_basis
    # only one job at a time - retry on 409
    for _ in range(30):
        r = requests.post(f"{BASE}/api/regime-lab/{ANALYSIS_ID}/optimize", json=body, headers=headers, timeout=30)
        if r.status_code == 409:
            time.sleep(3)
            continue
        break
    assert r.status_code == 200, r.text
    return r.json()["job_id"]


def test_optimize_label_basis_live(headers):
    job_id = _post_optimize(headers, "live")
    j = _poll(job_id)
    assert j.get("status") == "done", j
    res = j.get("result") or {}
    assert res.get("label_basis") == "causal_live", res


def test_optimize_label_basis_final(headers):
    job_id = _post_optimize(headers, "final")
    j = _poll(job_id)
    assert j.get("status") == "done", j
    res = j.get("result") or {}
    assert res.get("label_basis") == "retrospective_reference", res


def test_optimize_label_basis_default(headers):
    job_id = _post_optimize(headers, None)
    j = _poll(job_id)
    assert j.get("status") == "done", j
    res = j.get("result") or {}
    assert res.get("label_basis") == "causal_live", res


def test_assign_and_walkforward_untraded(headers):
    assign_body = {
        "scope": "combined",
        "regime_id": 2,
        "candidate": {
            "mode": "params",
            "strategy_id": "nnfx_trend",
            "strategy_name": "NNFX Trend",
            "trade_params": {},
            "strategy_params": {},
        },
    }
    r = requests.post(f"{BASE}/api/regime-lab/{ANALYSIS_ID}/assign", json=assign_body, headers=headers, timeout=30)
    assert r.status_code == 200, r.text

    for _ in range(30):
        r = requests.post(
            f"{BASE}/api/regime-lab/{ANALYSIS_ID}/walkforward",
            json={"scope": "combined", "strategy_id": "nnfx_trend"},
            headers=headers,
            timeout=30,
        )
        if r.status_code == 409:
            time.sleep(3)
            continue
        break
    assert r.status_code == 200, r.text
    job_id = r.json()["job_id"]
    j = _poll(job_id, timeout=240)
    assert j.get("status") == "done", j
    res = j.get("result") or {}
    assert res.get("label_basis") == "causal_live", res
    assert int(res.get("untraded_bars", 0)) > 0, res
    per_regime = res.get("per_regime") or []
    if isinstance(per_regime, dict):
        keys = [str(k) for k in per_regime.keys()]
    else:
        keys = [str(item.get("regime_id", item.get("id", item.get("regime")))) for item in per_regime]
    assert "2" in keys, keys
    assert len(keys) == 1, f"Expected only regime 2, got {keys} / raw={per_regime}"


def test_build_dynamic_strategy(headers):
    r = requests.post(
        f"{BASE}/api/regime-lab/{ANALYSIS_ID}/build",
        json={"scope": "combined", "strategy_id": "nnfx_trend", "name": "E2E Build"},
        headers=headers,
        timeout=30,
    )
    assert r.status_code == 200, r.text
    dyn_id = r.json().get("id") or r.json().get("strategy_id")
    assert dyn_id, r.json()

    # Fetch dynamic strategies list and locate
    r2 = requests.get(f"{BASE}/api/dynamic/list", headers=headers, timeout=30)
    assert r2.status_code == 200, r2.text
    data = r2.json()
    doc = None
    if isinstance(data, dict) and "strategies" in data:
        for d in data["strategies"]:
            if d.get("id") == dyn_id or d.get("strategy_id") == dyn_id:
                doc = d
                break
    elif isinstance(data, list):
        for d in data:
            if d.get("id") == dyn_id or d.get("strategy_id") == dyn_id:
                doc = d
                break
    assert doc, f"dynamic strategy {dyn_id} not found in {data}"
    rs = doc.get("regime_strategies") or (doc.get("config") or {}).get("regime_strategies")
    assert rs == {"2": "nnfx_trend"}, f"regime_strategies mismatch: {rs}"
