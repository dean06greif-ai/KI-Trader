"""Iteration 37 – API-Shape-Tests fuer die neuen Aenderungen:
  * GET /api/ai/playbook  -> rules / lifecycle / maturity
  * GET /api/klines/{GOLD,SILVER,OIL} -> frische Bitunix-Kerzen (<= 5 min)
  * GET /api/ai/diagnosis -> data_quality last_candle_age_min fuer Rohstoffe
  * GET /api/autotrade/trades -> Seed-Trades TEST-BTC-1/2, TEST-ETH-1 offen
"""
import os
import time

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

RESOURCES = ["GOLD", "SILVER", "OIL"]


@pytest.fixture(scope="module")
def api():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


# ---- Setup-Lebenszyklus / Playbook ----
class TestPlaybookShape:
    @pytest.fixture(scope="class")
    def payload(self, api):
        r = api.get(f"{BASE_URL}/api/ai/playbook", timeout=90)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        return r.json()

    def test_rules(self, payload):
        assert "rules" in payload, f"keys={list(payload)}"
        rules = payload["rules"]
        assert isinstance(rules, dict)
        assert rules.get("promote_min_trades") == 5
        assert rules.get("promote_min_winrate") == 55
        assert rules.get("demote_min_live_trades") == 8
        assert rules.get("demote_pnl_pct") == -3
        assert rules.get("demote_max_winrate") == 35

    def test_lifecycle_dict(self, payload):
        assert "lifecycle" in payload, f"keys={list(payload)}"
        assert isinstance(payload["lifecycle"], dict)

    def test_maturity_rows(self, payload):
        assert "maturity" in payload, f"keys={list(payload)}"
        mat = payload["maturity"]
        assert isinstance(mat, list)
        allowed = {"sammelt", "live", "rückgestuft", "gesperrt"}
        for row in mat:
            assert row.get("phase") in allowed, f"bad phase: {row}"
            assert "profile" in row, f"missing profile: {row}"
            assert row["profile"] is None or isinstance(row["profile"], dict)
            assert "paper_since_demotion" in row, f"missing paper_since_demotion: {row}"


# ---- Rohstoff-Kerzen (Bitunix statt Yahoo) ----
class TestResourceKlinesFresh:
    @pytest.mark.parametrize("symbol", RESOURCES)
    def test_klines_recent(self, api, symbol):
        r = api.get(f"{BASE_URL}/api/klines/{symbol}?limit=5", timeout=90)
        assert r.status_code == 200, f"{symbol} {r.status_code}: {r.text[:300]}"
        data = r.json()
        candles = data.get("candles") or data.get("klines") or []
        assert candles, f"{symbol}: no candles -> {str(data)[:300]}"
        newest = max(int(c["timestamp"]) for c in candles)
        age_min = (time.time() * 1000 - newest) / 60000.0
        assert age_min <= 5, f"{symbol}: newest candle {age_min:.1f} min old"

    def test_btc_reference(self, api):
        r = api.get(f"{BASE_URL}/api/klines/BTCUSDT?limit=5", timeout=90)
        assert r.status_code == 200
        candles = r.json().get("candles") or []
        assert candles
        age_min = (time.time() * 1000 - max(int(c["timestamp"]) for c in candles)) / 60000.0
        assert age_min <= 5, f"BTCUSDT newest candle {age_min:.1f} min old"


# ---- Diagnose: Datenqualitaet ----
class TestDiagnosisDataQuality:
    @pytest.fixture(scope="class")
    def diag(self, api):
        r = api.get(f"{BASE_URL}/api/ai/diagnosis?days=7", timeout=120)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:400]}"
        return r.json()

    def test_data_quality_present(self, diag):
        assert "data_quality" in diag, f"keys={list(diag)}"

    @pytest.mark.parametrize("symbol", RESOURCES + ["BTCUSDT"])
    def test_age(self, diag, symbol):
        dq = diag["data_quality"]
        rows = dq if isinstance(dq, list) else dq.get("rows", dq)
        if isinstance(rows, dict):
            entry = rows.get(symbol)
        else:
            entry = next((r for r in rows if r.get("symbol") == symbol), None)
        assert entry is not None, f"{symbol} missing in data_quality: {str(dq)[:400]}"
        age = entry.get("last_candle_age_min")
        assert age is not None, f"{symbol}: no last_candle_age_min -> {entry}"
        assert age <= 5, f"{symbol}: last_candle_age_min={age}"


# ---- Seed-Trades fuer den Badge-Test ----
class TestSeedTradesVisible:
    def test_open_seed_trades(self, api):
        r = api.get(f"{BASE_URL}/api/autotrade/trades?limit=200", timeout=60)
        assert r.status_code == 200, f"{r.status_code}: {r.text[:300]}"
        trades = r.json().get("trades", [])
        by_id = {t["id"]: t for t in trades}
        for tid, sym in [("TEST-BTC-1", "BTCUSDT"), ("TEST-BTC-2", "BTCUSDT"),
                         ("TEST-ETH-1", "ETHUSDT")]:
            assert tid in by_id, f"{tid} not returned by API"
            assert by_id[tid]["symbol"] == sym
            assert by_id[tid]["status"] == "open"
        assert not any("_id" in t for t in trades), "MongoDB _id leaked in response"
