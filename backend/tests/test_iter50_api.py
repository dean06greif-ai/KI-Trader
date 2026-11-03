"""Iteration 50 – Backend API verification for active-scope, master-prompt, ai-team roles, autotrade balance."""
import os
import time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "crypto_scanner_dev"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=10)
    assert r.status_code == 200, r.text
    return r.json().get("token") or r.json().get("access_token")


@pytest.fixture(scope="module")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- strategy-comparison active-scope ----------------
class TestStrategyComparisonActiveScope:
    def test_live_only_active(self):
        r = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "live"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("only_active") is True
        assert d.get("inactive_hidden", 0) >= 1
        names = [s.get("strategy") for s in d.get("comparison", [])]
        assert "scalping_4_rules" not in names

    def test_live_all(self):
        r = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "live", "only_active": "false"}, timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("only_active") is False
        assert d.get("inactive_hidden", 0) == 0
        names = [s.get("strategy") for s in d.get("comparison", [])]
        assert "scalping_4_rules" in names

    def test_totals_differ(self):
        a = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "live"}, timeout=15).json()
        b = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "live", "only_active": "false"}, timeout=15).json()
        # Expect ≥2 more in "all" than active-only
        assert b["total_trades"] >= a["total_trades"] + 2
        # Documented spec values
        assert a["total_trades"] == 90
        assert b["total_trades"] == 92


# ---------------- autotrade balance ----------------
class TestAutotradeBalance:
    def test_balance_returns_200(self):
        r = requests.get(f"{BASE_URL}/api/autotrade/balance", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        # Fields required
        for f in ("mode", "realized_pnl", "open_trades", "closed_trades"):
            assert f in d, f"missing field {f}: {d}"
        # active_only field
        assert d.get("active_only") is True


# ---------------- ai/master-prompt ----------------
class TestMasterPrompt:
    def test_get_master_prompt(self):
        r = requests.get(f"{BASE_URL}/api/ai/master-prompt", timeout=15)
        assert r.status_code == 200, r.text
        d = r.json()
        mp = d.get("master_prompt") or d
        rules = mp.get("rules", {})
        assert rules.get("block_coin_ranking_lessons") is True
        rl = mp.get("rule_labels") or d.get("rule_labels") or {}
        assert "block_coin_ranking_lessons" in rl
        policy = mp.get("lesson_policy") or d.get("lesson_policy") or ""
        assert isinstance(policy, str)
        assert "10." in policy and "KEINE Coin-Ranglisten" in policy
        assert "11." in policy and "Lektionen sind Handlungsregeln" in policy

    def test_post_master_prompt_toggle(self, auth_headers):
        # get current rules
        cur = requests.get(f"{BASE_URL}/api/ai/master-prompt", timeout=10).json()
        mp = cur.get("master_prompt") or cur
        rules = dict(mp.get("rules", {}))
        rules["block_coin_ranking_lessons"] = False
        payload = {"rules": rules}
        r = requests.post(f"{BASE_URL}/api/ai/master-prompt", json=payload, headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text
        after = requests.get(f"{BASE_URL}/api/ai/master-prompt", timeout=10).json()
        mp2 = after.get("master_prompt") or after
        assert mp2.get("rules", {}).get("block_coin_ranking_lessons") is False
        # restore
        rules["block_coin_ranking_lessons"] = True
        r2 = requests.post(f"{BASE_URL}/api/ai/master-prompt", json={"rules": rules}, headers=auth_headers, timeout=15)
        assert r2.status_code == 200
        after2 = requests.get(f"{BASE_URL}/api/ai/master-prompt", timeout=10).json()
        mp3 = after2.get("master_prompt") or after2
        assert mp3.get("rules", {}).get("block_coin_ranking_lessons") is True


# ---------------- ai team roles fallbacks ----------------
class TestAIRolesFallback:
    def _fetch_roles(self):
        # try /api/ai/team first
        r = requests.get(f"{BASE_URL}/api/ai/team", timeout=15)
        if r.status_code != 200:
            r = requests.get(f"{BASE_URL}/api/ai/status", timeout=15)
        assert r.status_code == 200, r.text
        return r.json()

    def test_role_fallbacks(self):
        d = self._fetch_roles()
        roles = d.get("roles") or {}
        assert isinstance(roles, dict) and roles, f"roles missing/empty: {list(d.keys())}"
        tm = roles.get("trade_manager")
        nw = roles.get("news_watcher")
        mo = roles.get("market_observer")
        assert tm and tm.get("fallback2_model") == "nvidia/nemotron-3-super-120b-a12b:free"
        assert nw and nw.get("fallback2_model") == "gemini-3.1-flash-lite"
        assert mo and mo.get("fallback_provider") == "groq"
        assert mo and mo.get("fallback_model") == "openai/gpt-oss-20b"


# ---------------- Regression: DB-based scenario ----------------
class TestActiveScopeRegressionDB:
    @pytest.fixture(scope="class")
    def db(self):
        cli = MongoClient(MONGO_URL, serverSelectionTimeoutMS=3000)
        db = cli[DB_NAME]
        yield db
        # cleanup
        db.auto_trades.delete_many({"id": {"$regex": "^TEST-"}})
        db.strategy_coin_configs.delete_one({"_id": "rsi_only_ZZTESTUSDT"})
        cli.close()

    def test_off_config_not_in_paper_comparison(self, db):
        # insert closed paper trade
        db.auto_trades.delete_many({"id": {"$regex": "^TEST-"}})
        db.strategy_coin_configs.delete_one({"_id": "rsi_only_ZZTESTUSDT"})
        db.auto_trades.insert_one({
            "id": "TEST-iter50-1",
            "strategy": "rsi_only",
            "symbol": "ZZTESTUSDT",
            "mode": "paper",
            "status": "closed",
            "realized_pnl": 77.77,
            "opened_at": time.time() - 3600,
            "closed_at": time.time(),
        })
        db.strategy_coin_configs.insert_one({
            "_id": "rsi_only_ZZTESTUSDT",
            "strategy": "rsi_only",
            "symbol": "ZZTESTUSDT",
            "mode": "off",
            "config": {"mode": "off"},
        })
        r = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "paper"}, timeout=15).json()
        strategies = r.get("comparison", [])
        rsi = next((s for s in strategies if s.get("strategy") == "rsi_only"), None)
        if rsi is not None:
            # If present it must not include our test symbol (ZZTESTUSDT)
            assets = rsi.get("assets") or rsi.get("symbols") or []
            assert "ZZTESTUSDT" not in assets, f"ZZTESTUSDT should be filtered out in off-config: {assets}"

    def test_paper_config_visible(self, db):
        db.strategy_coin_configs.update_one(
            {"_id": "rsi_only_ZZTESTUSDT"},
            {"$set": {"mode": "paper", "config.mode": "paper"}},
            upsert=True,
        )
        r = requests.get(f"{BASE_URL}/api/analytics/strategy-comparison", params={"mode": "paper"}, timeout=15).json()
        strategies = r.get("comparison", [])
        rsi = next((s for s in strategies if s.get("strategy") == "rsi_only"), None)
        # rsi_only should exist (either from pre-existing paper trades or the injected one)
        assert rsi is not None, "rsi_only should appear when config.mode=paper"
