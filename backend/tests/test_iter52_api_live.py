"""Live API tests for iter52 improvements: robustness in Backtester, limit-fill, optimizer ETA/asset insight, RAM."""
import os, time, json, pytest, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL") or open("/app/frontend/.env").read().split("REACT_APP_BACKEND_URL=")[1].split("\n")[0]
BASE = BASE.rstrip("/")
TOKEN = open("/tmp/token.txt").read().strip()
H = {"Content-Type": "application/json", "Authorization": f"Bearer {TOKEN}"}


def _poll_bt(job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/api/backtest/status/{job_id}", headers=H, timeout=30)
        assert r.status_code == 200, r.text
        j = r.json()
        if j.get("status") in ("done", "completed", "finished", "error", "failed"):
            return j
        time.sleep(2)
    pytest.fail(f"backtest {job_id} timed out: last={j}")


def _poll_opt(job_id, timeout=180, check_eta=False):
    saw_eta = False
    t0 = time.time()
    poll_i = 0
    while time.time() - t0 < timeout:
        if check_eta:
            ra = requests.get(f"{BASE}/api/optimizer/active", headers=H, timeout=30)
            if ra.status_code == 200:
                aj = ra.json()
                candidates = []
                if isinstance(aj, list):
                    candidates = aj
                elif isinstance(aj, dict):
                    if aj.get("active"):
                        a = aj["active"]
                        candidates = [a] if isinstance(a, dict) else list(a)
                    candidates += aj.get("jobs", []) or []
                for job in candidates:
                    if not isinstance(job, dict):
                        continue
                    eta = job.get("eta_seconds")
                    if isinstance(eta, int):
                        saw_eta = True
        r = requests.get(f"{BASE}/api/optimizer/result/{job_id}", headers=H, timeout=30)
        if r.status_code == 200:
            raw = r.json()
            inner = raw.get("result") if isinstance(raw.get("result"), dict) else raw
            if inner and inner.get("top5") and len(inner.get("top5")) > 0 and poll_i > 1:
                return inner, saw_eta
        poll_i += 1
        time.sleep(0.6)
    pytest.fail(f"optimizer {job_id} timed out")


def test_backtest_with_full_robustness():
    body = {
        "strategy_ids": ["rsi_only", "bollinger_reversion"],
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
        "days": 7, "timeframe": "15m", "execution": "cloud",
        "robustness": {
            "walk_forward": {"enabled": True, "mode": "rolling", "windows": 3},
            "dd_filter": {"enabled": True, "max_dd_pct": 60},
            "constancy": {"enabled": True, "chunk_days": 2},
            "stress_test": {"enabled": True, "cost_multiplier": 1.5},
            "monte_carlo": {"enabled": True, "runs": 200},
            "regime_analysis": {"enabled": True},
        },
    }
    r = requests.post(f"{BASE}/api/backtest/run", headers=H, json=body, timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json().get("job_id") or r.json().get("id")
    j = _poll_bt(job_id, timeout=180)
    assert j.get("status") in ("done", "completed", "finished"), j
    result = j.get("result") or j
    rob = result.get("robustness")
    assert rob and "per_strategy" in rob, f"no robustness: keys={list(result.keys())}"
    ps = rob["per_strategy"]
    for sid in ("rsi_only", "bollinger_reversion"):
        assert sid in ps, f"missing sid {sid}"
        entry = ps[sid]
        for key in ("metrics", "wf", "checks", "recommendation"):
            assert key in entry, f"{sid} missing {key}: {list(entry.keys())}"
        wfw = entry.get("wf_windows") or entry.get("wf", {}).get("windows")
        assert wfw and len(wfw) == 3, f"wf_windows len wrong: {wfw}"
        assert "regimes" in entry, f"{sid} missing regimes"
        ps_sym = entry.get("per_symbol") or {}
        assert ps_sym, f"{sid} missing per_symbol"
        first_sym = next(iter(ps_sym.values()))
        assert "regimes" in first_sym, f"per_symbol missing regimes: {list(first_sym.keys())}"
        assert "stress" in entry
        assert "monte_carlo" in entry


def test_backtest_without_robustness_backward_compat():
    body = {"strategy_ids": ["rsi_only"], "symbols": ["BTCUSDT"], "days": 3, "timeframe": "15m", "execution": "cloud"}
    r = requests.post(f"{BASE}/api/backtest/run", headers=H, json=body, timeout=30)
    assert r.status_code == 200
    job_id = r.json().get("job_id") or r.json().get("id")
    j = _poll_bt(job_id, timeout=120)
    result = j.get("result") or j
    assert "robustness" not in result, f"robustness should be absent, got keys={list(result.keys())}"


def test_backtest_limit_order_realistic_vs_touch():
    base_body = {
        "strategy_ids": ["rsi_only"], "symbols": ["BTCUSDT", "ETHUSDT"],
        "days": 5, "timeframe": "15m", "execution": "cloud",
        "strategy_configs": {"rsi_only": {"entry_order_type": "limit", "limit_offset_pct": 0.1}},
    }
    fills = {}
    for mode in ("touch", "realistic"):
        body = dict(base_body); body["limit_fill_mode"] = mode
        r = requests.post(f"{BASE}/api/backtest/run", headers=H, json=body, timeout=30)
        assert r.status_code == 200
        job_id = r.json().get("job_id") or r.json().get("id")
        j = _poll_bt(job_id, timeout=120)
        result = j.get("result") or j
        per_pair = result.get("per_pair") or result.get("rows") or []
        total_filled = 0; total_expired = 0; found_counter = False
        for row in per_pair:
            if "limit_filled" in row or "limit_expired" in row:
                found_counter = True
                total_filled += row.get("limit_filled", 0) or 0
                total_expired += row.get("limit_expired", 0) or 0
        assert found_counter, f"no limit_filled/limit_expired columns in {mode}: sample={per_pair[:1]}"
        fills[mode] = total_filled
    assert fills["realistic"] <= fills["touch"], f"realistic should fill <= touch: {fills}"


def test_optimizer_eta_asset_insight_regimes():
    body = {
        "mode": "params", "strategy_id": "rsi_only",
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        "days": 7, "timeframe": "15m", "iterations": 30, "execution": "cloud",
        "optimize": {"tpsl": True, "entry_order": True},
        "walk_forward": {"enabled": True, "train_pct": 70},
        "regime_analysis": {"enabled": True},
    }
    r = requests.post(f"{BASE}/api/optimizer/run", headers=H, json=body, timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json().get("job_id") or r.json().get("id")
    j, saw_eta = _poll_opt(job_id, timeout=240, check_eta=True)
    assert saw_eta, "never saw integer eta_seconds in /optimizer/active"
    result = j
    top5 = result.get("top5") or []
    assert top5, f"no top5: {list(result.keys())}"
    first = top5[0]
    psym = first.get("per_symbol") or {}
    assert psym, f"top5[0] missing per_symbol: {list(first.keys())}"
    some_sym = next(iter(psym.values()))
    for k in ("pnl", "trades", "win_rate"):
        assert k in some_sym, f"per_symbol entry missing {k}: {list(some_sym.keys())}"
    assert "test" in some_sym, f"per_symbol missing 'test': {list(some_sym.keys())}"
    reg = some_sym.get("regimes")
    assert reg, f"per_symbol missing regimes: {list(some_sym.keys())}"
    for rk in ("bull", "bear", "sideways"):
        assert rk in reg, f"regime {rk} missing"
        for mk in ("pnl", "trades", "win_rate"):
            assert mk in reg[rk], f"regime {rk} missing {mk}"
        assert "wins" not in reg[rk], f"regime {rk} should not have 'wins'"


def test_system_ram_candle_cache_estimation():
    r = requests.get(f"{BASE}/api/system/ram", headers=H, timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    cc = j.get("candle_cache") or {}
    est_mb = cc.get("estimated_mb")
    total = cc.get("total_candles") or cc.get("candles") or 0
    assert est_mb is not None, f"no estimated_mb: {cc}"
    if total > 0:
        bytes_per = (est_mb * 1024 * 1024) / total
        # ~48 bytes per candle (not 500)
        assert 10 <= bytes_per <= 150, f"bytes_per_candle={bytes_per}, est_mb={est_mb}, total={total}"
