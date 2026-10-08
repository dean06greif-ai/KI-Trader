"""Iteration 27 review tests: HYPEUSDT instrument, settings key-persistence,
optimizer apply, custom strategy persistence and Strategie-Copilot endpoints."""
import os
import time
import uuid

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(client):
    r = client.post(f"{BASE_URL}/api/auth/login",
                    json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"Admin login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token")
    assert isinstance(tok, str) and tok
    return tok


@pytest.fixture(scope="module")
def auth(token):
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json",
                      "Authorization": f"Bearer {token}"})
    return s


# ---------------- HYPEUSDT (Bitunix) ----------------
class TestHyperliquid:
    def test_coins_contains_hype(self, client):
        r = client.get(f"{BASE_URL}/api/coins", timeout=30)
        assert r.status_code == 200
        d = r.json()
        assert "HYPEUSDT" in d.get("coins", []), d.get("coins")
        assert "HYPEUSDT" in d.get("crypto", []), d.get("crypto")
        assert "HYPEUSDT" in d.get("tradable", []), d.get("tradable")

    def test_klines_hype_real_candles(self, client):
        r = client.get(f"{BASE_URL}/api/klines/HYPEUSDT?interval=1m&limit=5", timeout=60)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        candles = data.get("klines") or data.get("candles") or data
        assert isinstance(candles, list) and len(candles) > 0, str(data)[:300]
        c = candles[-1]
        for k in ("open", "high", "low", "close"):
            assert float(c[k]) > 0, c


# ---------------- Settings key persistence ----------------
class TestSettingsPersistence:
    def test_unknown_key_ignored(self, auth):
        r = auth.post(f"{BASE_URL}/api/settings", json={"foo": 1}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert r.json().get("ignored_keys") == ["foo"], r.json()

    def test_known_key_saved_and_persists(self, auth):
        val = 7
        r = auth.post(f"{BASE_URL}/api/settings",
                      json={"notify_cooldown_min": val}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        assert not r.json().get("ignored_keys")
        g = auth.get(f"{BASE_URL}/api/settings", timeout=30)
        assert g.status_code == 200
        assert g.json().get("notify_cooldown_min") == val


# ---------------- Custom strategy + persistence ----------------
class TestCustomStrategy:
    created = []

    def test_create_custom_strategy_persisted(self, auth):
        name = f"TEST_RSI_{uuid.uuid4().hex[:6]}"
        payload = {"name": name, "timeframe": "5m",
                   "indicators": {"rsi_period": 14},
                   "long_rules": [{"indicator": "rsi", "op": "<", "value": 30}],
                   "short_rules": [{"indicator": "rsi", "op": ">", "value": 70}]}
        r = auth.post(f"{BASE_URL}/api/strategies/custom", json=payload, timeout=60)
        assert r.status_code in (200, 201), r.text[:500]
        body = r.json()
        sid = body.get("id") or (body.get("definition") or {}).get("id")
        assert sid, body
        TestCustomStrategy.created.append(sid)

        ls = auth.get(f"{BASE_URL}/api/strategies", timeout=30)
        assert ls.status_code == 200
        strats = ls.json()
        items = strats.get("strategies", strats) if isinstance(strats, dict) else strats
        ids = [s.get("id") for s in items]
        assert sid in ids, ids

        st = auth.get(f"{BASE_URL}/api/settings", timeout=30).json()
        assert st.get("strategy_timeframes", {}).get(sid) == "5m", st.get("strategy_timeframes")
        assert sid in st.get("enabled_strategies", []), st.get("enabled_strategies")

    @classmethod
    def teardown_class(cls):
        s = requests.Session()
        r = s.post(f"{BASE_URL}/api/auth/login",
                   json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
        if r.status_code != 200:
            return
        h = {"Authorization": f"Bearer {r.json()['token']}"}
        for sid in cls.created:
            s.delete(f"{BASE_URL}/api/strategies/custom/{sid}", headers=h, timeout=30)


# ---------------- Optimizer apply ----------------
class TestOptimizerApply:
    def test_apply_params(self, auth):
        r = auth.post(f"{BASE_URL}/api/optimizer/apply",
                      json={"type": "params", "strategy_id": "scalping_4_rules",
                            "params": {"rsi_period": 13}, "timeframe": "1m"}, timeout=60)
        assert r.status_code == 200, r.text[:500]
        st = auth.get(f"{BASE_URL}/api/settings", timeout=30).json()
        assert st["strategy_params"]["scalping_4_rules"]["rsi_period"] == 13
        assert st["strategy_timeframes"]["scalping_4_rules"] == "1m"


# ---------------- Copilot ----------------
class TestCopilot:
    def test_status(self, client):
        r = client.get(f"{BASE_URL}/api/copilot/status", timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["provider"] == "openrouter"
        assert isinstance(d.get("models"), list) and len(d["models"]) > 0
        assert d.get("ready") is True, d

    def test_chat_requires_auth(self, client):
        r = client.post(f"{BASE_URL}/api/copilot/chat", json={"message": "hi"}, timeout=30)
        assert r.status_code in (401, 403), r.status_code

    def test_chat_empty_message_400(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/chat", json={"message": "  "}, timeout=30)
        assert r.status_code == 400, r.text[:200]

    def test_chat_real_llm(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/chat",
                      json={"message": "Bewerte kurz: trades=10, wins=6, losses=4, win_rate=60",
                            "context": {"panel": "optimizer"}}, timeout=180)
        assert r.status_code == 200, r.text[:500]
        d = r.json()
        assert isinstance(d.get("reply"), str) and len(d["reply"]) > 5, d
        assert d.get("model")

    def test_review_detects_inconsistency(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/review",
                      json={"metrics": {"trades": 10, "wins": 5, "losses": 4,
                                        "breakevens": 0, "win_rate": 60}}, timeout=30)
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert d["ok"] is False
        joined = " ".join(d["checks"])
        assert "Trade-Summe" in joined, d
        assert "Win-Rate" in joined, d

    def test_review_ok_metrics(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/review",
                      json={"metrics": {"trades": 10, "wins": 6, "losses": 4,
                                        "breakevens": 0, "win_rate": 60}}, timeout=30)
        assert r.status_code == 200
        assert r.json()["ok"] is True, r.json()

    def test_apply_params_proposal(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/apply",
                      json={"proposal": {"type": "params", "strategy_id": "scalping_4_rules",
                                         "params": {"rsi_period": 14},
                                         "summary": "TEST_copilot"}}, timeout=60)
        assert r.status_code == 200, r.text[:500]
        assert r.json()["status"] == "success"
        st = auth.get(f"{BASE_URL}/api/settings", timeout=30).json()
        assert st["strategy_params"]["scalping_4_rules"]["rsi_period"] == 14

    def test_apply_invalid_definition_422(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/apply",
                      json={"proposal": {"type": "definition",
                                         "definition": {"name": "TEST_bad", "timeframe": "5m"}}},
                      timeout=60)
        assert r.status_code == 422, f"{r.status_code}: {r.text[:400]}"
        body = r.json()
        detail = body.get("detail", body)
        assert "problems" in str(detail), detail

    def test_apply_bad_type_400(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/apply",
                      json={"proposal": {"type": "nonsense"}}, timeout=30)
        assert r.status_code == 400

    def test_apply_unknown_strategy_400(self, auth):
        r = auth.post(f"{BASE_URL}/api/copilot/apply",
                      json={"proposal": {"type": "params", "strategy_id": "does_not_exist",
                                         "params": {"rsi_period": 9}}}, timeout=30)
        assert r.status_code == 400

    def test_history_and_clear(self, auth):
        h = auth.get(f"{BASE_URL}/api/copilot/history", timeout=30)
        assert h.status_code == 200
        msgs = h.json().get("messages")
        assert isinstance(msgs, list)
        assert all("_id" not in m for m in msgs)
        d = auth.delete(f"{BASE_URL}/api/copilot/history", timeout=30)
        assert d.status_code == 200, d.text[:200]
        assert d.json()["status"] == "success"
        time.sleep(0.5)
        h2 = auth.get(f"{BASE_URL}/api/copilot/history", timeout=30)
        assert h2.json().get("messages") == []

    def test_copilot_note_in_memory(self, auth):
        r = auth.get(f"{BASE_URL}/api/ai/memory?kind=copilot_note&limit=20", timeout=30)
        if r.status_code == 404:
            pytest.skip("no /api/ai/memory endpoint")
        assert r.status_code == 200, r.text[:200]
