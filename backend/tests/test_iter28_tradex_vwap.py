"""Iter28: API tests for new tradex_vwap_scalping strategy + generic trade settings.

Covers:
- /api/auth/login
- /api/strategies -> tradex_vwap_scalping present with recommended_trade_cfg + requires_positioning
- /api/backtest/run for tradex_vwap_scalping and horst_vwap_obv (regression)
"""
import os
import time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
USER = "Admin"
PWD = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    last = None
    for _ in range(5):
        try:
            r = requests.post(f"{BASE}/api/auth/login",
                              json={"username": USER, "password": PWD}, timeout=60)
            last = r
            if r.status_code == 200 and r.json().get("token"):
                return r.json()["token"]
        except Exception as e:
            last = e
        time.sleep(5)
    pytest.fail(f"login failed: {last}")


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_strategies_lists_tradex(auth):
    r = requests.get(f"{BASE}/api/strategies", headers=auth, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    items = data if isinstance(data, list) else data.get("strategies") or data.get("items") or []
    ids = [it.get("id") or it.get("key") for it in items]
    assert "tradex_vwap_scalping" in ids, f"missing tradex_vwap_scalping in {ids}"
    assert "horst_vwap_obv" in ids
    tradex = next(it for it in items if (it.get("id") or it.get("key")) == "tradex_vwap_scalping")
    assert tradex.get("recommended_trade_cfg"), "recommended_trade_cfg missing"
    rc = tradex["recommended_trade_cfg"]
    # sanity of recommended values
    assert rc.get("entry_order_type") in ("limit", "market")
    assert rc.get("tp_order_type") in ("limit", "market")
    assert tradex.get("requires_positioning") is True


def _run_backtest(auth, strategy_id, days=30, symbols=None):
    payload = {
        "strategy_ids": [strategy_id],
        "symbols": symbols or ["BTCUSDT"],
        "days": days,
    }
    r = requests.post(f"{BASE}/api/backtest/run", headers=auth, json=payload, timeout=60)
    assert r.status_code in (200, 202), r.text
    body = r.json()
    job_id = body.get("job_id") or body.get("id")
    # if run is sync
    if body.get("status") == "done" and body.get("result"):
        return body
    assert job_id, f"no job_id: {body}"
    # poll
    deadline = time.time() + 240
    last = None
    while time.time() < deadline:
        s = requests.get(f"{BASE}/api/backtest/status/{job_id}", headers=auth, timeout=30)
        if s.status_code == 200:
            last = s.json()
            status = last.get("status")
            if status in ("done", "error", "failed"):
                return last
        time.sleep(3)
    raise AssertionError(f"backtest did not finish: {last}")


def test_backtest_tradex_vwap(auth):
    res = _run_backtest(auth, "tradex_vwap_scalping", days=30)
    assert res.get("status") == "done", f"backtest not done: {res}"
    result = res.get("result") or res
    per_pair = result.get("per_pair") or result.get("perPair") or {}
    assert per_pair, f"no per_pair result: {result}"
    # per_pair may be list of dicts or dict
    entries = per_pair if isinstance(per_pair, list) else list(per_pair.values())
    btc = None
    for e in entries:
        sym = (e.get("symbol") or e.get("pair") or "") if isinstance(e, dict) else ""
        if "BTC" in str(sym).upper():
            btc = e
            break
    if btc is None and isinstance(per_pair, dict):
        for k, v in per_pair.items():
            if "BTC" in k.upper():
                btc = v
                break
    assert btc, f"BTCUSDT missing in per_pair: {per_pair}"
    pp = btc if isinstance(btc, dict) else (btc[0] if btc else {})
    assert "time_exits" in pp, f"time_exits missing in per_pair entry: {pp}"
    assert "limit_expired" in pp, f"limit_expired missing in per_pair entry: {pp}"


def test_backtest_horst_regression(auth):
    res = _run_backtest(auth, "horst_vwap_obv", days=14)
    assert res.get("status") == "done", f"horst backtest not done: {res}"
