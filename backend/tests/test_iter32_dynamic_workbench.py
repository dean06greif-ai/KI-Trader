"""Iteration 32 backend tests: dynamic workbench + dynamic strategy backtest."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ki-trader-optimize.preview.emergentagent.com").rstrip("/")
TIMEOUT = 90


def _req(method, path, **kw):
    kw.setdefault("timeout", TIMEOUT)
    last = None
    for _ in range(6):
        try:
            r = requests.request(method, f"{BASE_URL}{path}", **kw)
            if r.status_code in (502, 503, 504):
                last = r
                time.sleep(10)
                continue
            return r
        except requests.exceptions.RequestException as e:
            last = e
            time.sleep(5)
    if isinstance(last, requests.Response):
        return last
    raise last


@pytest.fixture(scope="module")
def token():
    r = _req("POST","/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"})
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


# --- Strategies list must include dynamic strategies ---
def test_strategies_list_has_dynamic():
    r = _req("GET","/api/strategies")
    assert r.status_code == 200
    items = r.json()
    if isinstance(items, dict):
        items = items.get("strategies") or items.get("items") or []
    dyn = [x for x in items if isinstance(x, dict) and x.get("is_dynamic")]
    assert len(dyn) >= 1
    for d in dyn:
        assert (d.get("dynamic") or {}).get("analysis_id"), f"missing analysis_id: {d}"


# --- Dynamic workbench start validation ---
def test_workbench_start_requires_auth():
    r = _req("POST","/api/dynamic-workbench/start", json={"kind": "create"})
    assert r.status_code in (401, 403), r.text


def test_workbench_start_bad_kind(auth):
    r = _req("POST","/api/dynamic-workbench/start",
                      json={"kind": "foo"}, headers=auth)
    # If a job is running the server may reply 409 first — accept both, but prefer 400
    assert r.status_code in (400, 409), r.text


def test_workbench_start_unknown_strategy(auth):
    body = {"kind": "create", "analysis_id": "ra_ef49af42", "mapping": {"0": "unbekannt_xyz"}}
    r = _req("POST","/api/dynamic-workbench/start",
                      json=body, headers=auth)
    # 400 unbekannte Strategie ODER 409 wenn gerade ein Job läuft (nicht cancel)
    assert r.status_code in (400, 409), r.text
    txt = r.text.lower()
    if r.status_code == 400:
        assert "unbekannt" in txt or "unknown" in txt


def test_workbench_active_has_kind(auth):
    r = _req("GET","/api/dynamic-workbench/active", headers=auth)
    assert r.status_code == 200
    data = r.json()
    job = data.get("job")
    # Es kann None sein wenn kein Job läuft — sonst muss 'kind' vorhanden sein
    if job is not None:
        assert "kind" in job, job
        assert job.get("kind") in ("refine", "create", "discover")


# --- Backtest with dynamic strategy: local execution must 400 ---
def test_backtest_local_dynamic_rejected(auth):
    body = {
        "strategy_ids": ["dyn_0080b023"],
        "symbols": ["BTCUSDT"],
        "days": 30,
        "max_capital": 100,
        "fee_percent": 0.06,
        "execution": "local",
    }
    r = _req("POST","/api/backtest/run", json=body, headers=auth)
    assert r.status_code == 400, r.text
    assert "cloud" in r.text.lower() or "wolke" in r.text.lower()


# --- Full dynamic backtest happy path ---
@pytest.mark.timeout(300)
def test_backtest_dynamic_full_flow(auth):
    body = {
        "strategy_ids": ["dyn_0080b023"],
        "symbols": ["BTCUSDT", "ETHUSDT"],
        "days": 30,
        "max_capital": 100,
        "fee_percent": 0.06,
    }
    # retry on 409 (already running)
    for _ in range(6):
        r = _req("POST","/api/backtest/run", json=body, headers=auth)
        if r.status_code == 409:
            time.sleep(15)
            continue
        break
    assert r.status_code == 200, r.text
    job = r.json()
    assert job.get("status") in ("started", "queued", "running"), job
    job_id = job.get("job_id") or job.get("id")
    assert job_id

    # Poll
    result = None
    for _ in range(60):
        time.sleep(5)
        s = _req("GET", f"/api/backtest/status/{job_id}", headers=auth)
        if s.status_code != 200:
            continue
        sd = s.json()
        status = sd.get("status")
        if status in ("done", "finished", "completed", "success"):
            result = sd.get("result") or sd
            break
        if status in ("error", "failed"):
            pytest.fail(f"backtest failed: {sd}")
    assert result is not None, "backtest didn't finish in 5 minutes"

    # per_strategy
    per = result.get("per_strategy") or []
    if isinstance(per, list):
        entry = next((p for p in per if p.get("strategy_id") == "dyn_0080b023"), None)
        assert entry is not None, [p.get("strategy_id") for p in per]
        assert entry.get("timeframe") == "1h"
    else:
        assert "dyn_0080b023" in per
        assert per["dyn_0080b023"].get("timeframe") == "1h"

    # dynamic_breakdown
    db = result.get("dynamic_breakdown") or {}
    assert "dyn_0080b023" in db, list(db.keys())
    entry = db["dyn_0080b023"]
    assert "name" in entry
    assert "switches" in entry
    assert "total" in entry
    tot = entry["total"]
    for k in ("trades", "win_rate", "pnl", "max_drawdown"):
        assert k in tot, f"missing {k} in total: {tot}"
    regs = entry.get("regimes") or []
    assert isinstance(regs, list) and len(regs) >= 1
    r0 = regs[0]
    for k in ("regime", "label", "traded", "strategy_name", "share_pct", "metrics", "recommendation", "alternatives"):
        assert k in r0, f"missing {k} in regime entry: {r0.keys()}"
    rec = r0["recommendation"]
    assert "action" in rec and "text" in rec
    points = entry.get("points") or []
    assert isinstance(points, list) and len(points) > 0
    assert "regime" in points[0]
    assert "skip_recommended" in entry
