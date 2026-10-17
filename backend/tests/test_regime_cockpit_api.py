"""Regression tests for Regime-Cockpit backend endpoints (PLAN_REGIME_COCKPIT B5)."""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrader-staging.preview.emergentagent.com").rstrip("/")
TIMEOUT_SHORT = 60
TIMEOUT_LONG = 180

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    if r.status_code != 200:
        pytest.skip(f"Admin login failed: {r.status_code} {r.text[:200]}")
    return r.json().get("token") or r.json().get("access_token")


# -- Regime-Cockpit Symbol Endpoint ------------------------------------------
class TestRegimeCockpitSymbol:
    def test_btcusdt_days14(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/BTCUSDT", params={"days": 14}, timeout=TIMEOUT_SHORT)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["symbol"] == "BTCUSDT"
        assert d["days"] == 14
        assert isinstance(d["prices"], list)
        assert len(d["prices"]) > 0
        # each price entry is [ts, close]
        assert len(d["prices"][0]) == 2
        obs = d["observer"]
        assert "points" in obs and "segments" in obs and "hits" in obs
        assert obs["horizon_hours"] == 4
        assert "current" in obs
        hits = obs["hits"]
        assert "n" in hits and "hit_pct" in hits and "per_label" in hits
        assert hits["n"] > 0, "expected observer.hits.n > 0 with real snapshots"
        stru = d["structural"]
        assert stru["stage"] == "none"
        assert "points" in stru and "segments" in stru and "hits" in stru
        assert stru["horizon_days"] == 3
        assert isinstance(d["trades"], list)
        assert "agreement" in d

    def test_ethusdt_days7(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/ETHUSDT", params={"days": 7}, timeout=TIMEOUT_SHORT)
        assert r.status_code == 200
        d = r.json()
        assert d["days"] == 7
        assert d["symbol"] == "ETHUSDT"

    def test_days_clamped_low(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/BTCUSDT", params={"days": 1}, timeout=TIMEOUT_SHORT)
        assert r.status_code == 200
        assert r.json()["days"] == 3

    def test_days_clamped_high(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/BTCUSDT", params={"days": 999}, timeout=TIMEOUT_LONG)
        assert r.status_code == 200
        assert r.json()["days"] == 60

    def test_unknown_symbol_no_500(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/FOOBARXYZ", params={"days": 7}, timeout=TIMEOUT_SHORT)
        assert r.status_code in (200, 502), f"got {r.status_code}: {r.text[:200]}"
        if r.status_code == 200:
            d = r.json()
            # empty data is acceptable
            assert isinstance(d.get("prices", []), list)


# -- Regime-Cockpit Overview -------------------------------------------------
class TestRegimeCockpitOverview:
    def test_overview_with_two_symbols(self):
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/overview",
                         params={"days": 14, "symbols": "BTCUSDT,ETHUSDT"},
                         timeout=TIMEOUT_LONG)
        assert r.status_code == 200
        d = r.json()
        assert d["days"] == 14
        rows = d["rows"]
        assert len(rows) == 2
        got = {row["symbol"] for row in rows}
        assert got == {"BTCUSDT", "ETHUSDT"}
        for row in rows:
            if "error" in row:
                continue
            assert "observer_hit_pct" in row
            assert "observer_n" in row
            assert "structural_stage" in row
            assert "agreement_pct" in row
            assert "trades" in row and "wins" in row and "pnl" in row

    def test_overview_cache_second_call_fast(self):
        # ensure warm cache
        requests.get(f"{BASE_URL}/api/regime-cockpit/overview",
                     params={"days": 14, "symbols": "BTCUSDT,ETHUSDT"}, timeout=TIMEOUT_LONG)
        t0 = time.time()
        r = requests.get(f"{BASE_URL}/api/regime-cockpit/overview",
                         params={"days": 14, "symbols": "BTCUSDT,ETHUSDT"}, timeout=TIMEOUT_LONG)
        dur = time.time() - t0
        assert r.status_code == 200
        # cached: should be much faster (<10s vs. ~30s+ cold)
        assert dur < 15, f"expected cached second call fast, took {dur:.1f}s"


# -- Dynamic list orphaned flag ---------------------------------------------
class TestDynamicList:
    def test_orphaned_flag_present(self):
        r = requests.get(f"{BASE_URL}/api/dynamic/list", timeout=30)
        assert r.status_code == 200
        d = r.json()
        # response may be list or {items:[...]}
        items = d if isinstance(d, list) else d.get("items") or d.get("strategies") or []
        # if empty (fresh DB), pass; else assert 'orphaned' key exists on each
        for s in items:
            assert "orphaned" in s, f"missing 'orphaned' in strategy row: {list(s.keys())[:10]}"
            assert isinstance(s["orphaned"], bool)


# -- AI config regime_context_enabled ---------------------------------------
class TestAiConfigRegimeContext:
    def _find_config(self):
        for path in ("/api/ai/config", "/api/ai/trader/config", "/api/ai/status"):
            r = requests.get(f"{BASE_URL}{path}", timeout=30)
            if r.status_code == 200:
                try:
                    j = r.json()
                except Exception:
                    continue
                if isinstance(j, dict) and self._locate(j) is not None:
                    return path, j
        return None, None

    def _locate(self, obj):
        # search for regime_context_enabled anywhere in dict
        if isinstance(obj, dict):
            if "regime_context_enabled" in obj:
                return obj["regime_context_enabled"]
            for v in obj.values():
                r = self._locate(v)
                if r is not None:
                    return r
        return None

    def test_regime_context_enabled_default(self):
        path, cfg = self._find_config()
        if not path:
            pytest.skip("could not locate ai config endpoint with regime_context_enabled")
        val = self._locate(cfg)
        assert isinstance(val, bool)
        # default is expected to be True
        assert val is True, f"expected regime_context_enabled True by default, got {val}"

    def test_regime_context_toggle(self, admin_token):
        headers = {"Authorization": f"Bearer {admin_token}"}
        read_path, _ = self._find_config()
        if not read_path:
            pytest.skip("no readable config endpoint")
        write = f"{BASE_URL}/api/ai/config"
        try:
            r = requests.post(write, json={"regime_context_enabled": False}, headers=headers, timeout=30)
            assert r.status_code == 200, r.text[:200]
            assert self._locate(r.json().get("config") or {}) is False
            # verify via read endpoint
            r2 = requests.get(f"{BASE_URL}{read_path}", timeout=30)
            assert self._locate(r2.json()) is False
        finally:
            # restore
            r3 = requests.post(write, json={"regime_context_enabled": True}, headers=headers, timeout=30)
            assert r3.status_code == 200
            assert self._locate(r3.json().get("config") or {}) is True
