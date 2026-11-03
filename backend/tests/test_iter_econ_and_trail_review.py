"""Review-Tests iter: CPI/NFP-Event-Endpoints + Trail-Optimizer Apply."""
import os
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://event-turbo-cache.preview.emergentagent.com").rstrip("/")
# Session with retries for transient preview-ingress 502/timeouts
_SESSION = requests.Session()


def _get(path, **kw):
    kw.setdefault("timeout", 45)
    last = None
    for _ in range(3):
        try:
            r = _SESSION.get(f"{BASE_URL}{path}", **kw)
            if r.status_code < 500:
                return r
            last = r
        except requests.RequestException as e:
            last = e
        time.sleep(2)
    if isinstance(last, Exception):
        raise last
    return last


def _post(path, **kw):
    kw.setdefault("timeout", 45)
    last = None
    for _ in range(3):
        try:
            r = _SESSION.post(f"{BASE_URL}{path}", **kw)
            if r.status_code < 500:
                return r
            last = r
        except requests.RequestException as e:
            last = e
        time.sleep(2)
    if isinstance(last, Exception):
        raise last
    return last

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"
STRAT_ID = "scalping_4_rules"


@pytest.fixture(scope="module")
def token():
    # login via internal port to avoid preview ingress 502 timeouts on slow endpoints
    last_err = None
    for url in (f"http://127.0.0.1:8001/api/auth/login", f"{BASE_URL}/api/auth/login"):
        try:
            r = requests.post(url,
                              json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
            if r.status_code == 200:
                tok = r.json().get("token")
                if tok:
                    return tok
            last_err = f"{url} -> {r.status_code} {r.text[:200]}"
        except Exception as e:
            last_err = f"{url} -> {e}"
    pytest.fail(f"login failed: {last_err}")


@pytest.fixture(scope="module")
def auth_hdr(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------- ECON endpoints -------------------
class TestEconStatus:
    def test_econ_status_all_has_cpi_and_nfp(self):
        r = _get("/api/econ/status")
        assert r.status_code == 200, r.text
        data = r.json()
        assert "events" in data
        evs = data["events"]
        assert "cpi" in evs and "nfp" in evs
        for k in ("cpi", "nfp"):
            s = evs[k]
            assert "phase" in s
            assert "next_release_utc" in s
            assert "windows" in s
            assert "backtest_running" in s

    def test_cpi_status_has_engine(self):
        r = _get("/api/econ/cpi/status")
        assert r.status_code == 200
        s = r.json()
        assert "phase" in s and "backtest_running" in s
        assert "engine" in s

    def test_nfp_status_has_engine(self):
        r = _get("/api/econ/nfp/status")
        assert r.status_code == 200
        assert "engine" in r.json()

    def test_unknown_event_returns_404(self):
        r = _get("/api/econ/xyz/status")
        assert r.status_code == 404


class TestEconBacktestResults:
    def test_cpi_backtest_validated_true_66_trades(self):
        r = _get("/api/econ/cpi/backtest")
        assert r.status_code == 200
        data = r.json()
        assert "result" in data and data["result"], "CPI backtest result missing in DB"
        res = data["result"]
        # walk nested aggregate.total.trades
        agg = res.get("aggregate") or res.get("result", {}).get("aggregate")
        # search in any nested shape
        def find_val(d, path):
            cur = d
            for p in path:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(p)
            return cur
        validated = res.get("validated")
        if validated is None:
            validated = find_val(res, ["result", "validated"])
        trades = find_val(res, ["aggregate", "total", "trades"])
        if trades is None:
            trades = find_val(res, ["result", "aggregate", "total", "trades"])
        per_event = res.get("per_event") or find_val(res, ["result", "per_event"])
        print(f"CPI validated={validated} trades={trades} per_event_count={len(per_event) if per_event else None}")
        assert validated is True
        assert trades == 66, f"expected 66 CPI trades, got {trades}"
        assert per_event and len(per_event) > 0

    def test_nfp_backtest_validated_false(self):
        r = _get("/api/econ/nfp/backtest")
        assert r.status_code == 200
        res = r.json().get("result")
        assert res, "NFP backtest result missing"
        validated = res.get("validated")
        if validated is None:
            validated = (res.get("result") or {}).get("validated")
        print(f"NFP validated={validated}")
        assert validated is False


class TestEconAuth:
    def test_backtest_start_requires_admin(self):
        r = _post("/api/econ/cpi/backtest", json={})
        assert r.status_code in (401, 403), r.status_code

    def test_config_requires_admin(self):
        r = requests.post(f"{BASE_URL}/api/econ/cpi/config",
                          json={"live_enabled": True}, timeout=20)
        assert r.status_code in (401, 403)


class TestEconLiveToggle:
    def test_cpi_config_toggle_live_enabled(self, auth_hdr):
        # ON
        r = requests.post(f"{BASE_URL}/api/econ/cpi/config",
                          json={"live_enabled": True}, headers=auth_hdr, timeout=20)
        assert r.status_code == 200, r.text
        # verify
        s = _get("/api/econ/cpi/status").json()
        assert s.get("live_enabled") is True
        # OFF (restore)
        r2 = requests.post(f"{BASE_URL}/api/econ/cpi/config",
                           json={"live_enabled": False}, headers=auth_hdr, timeout=20)
        assert r2.status_code == 200
        s2 = _get("/api/econ/cpi/status").json()
        assert s2.get("live_enabled") is False


# ------------------- FOMC regression -------------------
class TestFomcRegression:
    def test_fomc_status_still_works(self):
        r = _get("/api/fomc/status")
        assert r.status_code == 200
        s = r.json()
        assert "phase" in s


# ------------------- Optimizer: Trail -------------------
class TestOptimizerTrail:
    def test_start_trail_optimizer_and_poll(self, auth_hdr):
        # ensure no stale running job
        try:
            requests.post(f"{BASE_URL}/api/optimizer/reset", headers=auth_hdr, timeout=15)
        except Exception:
            pass
        time.sleep(2)
        body = {
            "mode": "params",
            "strategy_id": STRAT_ID,
            "symbols": ["BTCUSDT"],
            "days": 3,
            "timeframe": "5m",
            "iterations": 5,
            "algorithm": "random",
            "optimize": {"tpsl": False, "trail": True},
        }
        r = requests.post(f"{BASE_URL}/api/optimizer/run",
                          json=body, headers=auth_hdr, timeout=30)
        # 409 if another job is running: allow retry after cancel
        if r.status_code == 409:
            pytest.skip(f"optimizer busy: {r.text}")
        assert r.status_code == 200, r.text
        job_id = r.json().get("job_id")
        assert job_id
        time.sleep(3)  # give job time to spin up so status endpoint sees running job
        # poll
        deadline = time.time() + 180
        last = None
        while time.time() < deadline:
            s = _get(f"/api/optimizer/status/{job_id}")
            assert s.status_code == 200
            last = s.json()
            st = last.get("status")
            if st in ("completed", "failed", "cancelled", "done", "finished"):
                break
            time.sleep(3)
        print(f"Optimizer final status={last.get('status')} keys={list(last.keys())}")
        print(f"BEST_RAW={last.get('best')}")
        assert last and last.get("status") in ("completed", "done", "finished"), f"job did not complete: {last}"
        # find best
        best = last.get("best") or last.get("result") or {}
        tp = best.get("trade_params") or (best.get("best") or {}).get("trade_params") or {}
        # fallback: /api/optimizer/result/{job_id}
        if not tp:
            r2 = _get(f"/api/optimizer/result/{job_id}")
            if r2.status_code == 200:
                rj = r2.json()
                tp = (rj.get("best") or {}).get("trade_params") or rj.get("trade_params") or {}
                # look deeper
                if not tp and isinstance(rj.get("result"), dict):
                    tp = (rj["result"].get("best") or {}).get("trade_params") or {}
        print(f"trade_params={tp}")
        assert "trail_after_tp1" in tp or "trail_atr_mult" in tp, f"trail params missing: {tp}"

    def test_apply_trail_params_global(self, auth_hdr):
        body = {
            "type": "params",
            "strategy_id": STRAT_ID,
            "scope": "global",
            "params": {},
            "trade_params": {"trail_after_tp1": True, "trail_atr_mult": 2.5},
        }
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json=body, headers=auth_hdr, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "success"
        tp = data.get("trade_params") or {}
        assert tp.get("trail_after_tp1") is True
        assert tp.get("trail_atr_mult") == 2.5

    def test_apply_existing_key_regression(self, auth_hdr):
        # tp1_crv still supported
        body = {
            "type": "params",
            "strategy_id": STRAT_ID,
            "scope": "global",
            "params": {},
            "trade_params": {"tp1_crv": 1.2},
        }
        r = requests.post(f"{BASE_URL}/api/optimizer/apply",
                          json=body, headers=auth_hdr, timeout=30)
        assert r.status_code == 200, r.text
        tp = r.json().get("trade_params") or {}
        assert tp.get("tp1_crv") == 1.2
