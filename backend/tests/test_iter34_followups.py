"""Iteration 34: end-to-end tests for 3 follow-up features."""
import os, time
import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrader-audit.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "LocalTest06!"}, timeout=60)
    assert r.status_code == 200, r.text
    return r.json()["token"]


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- Feature 1: data repair -----------------
class TestDataRepair:
    def test_missing_symbol_400(self, hdr):
        r = requests.post(f"{BASE_URL}/api/localworker/data/repair", json={}, headers=hdr, timeout=15)
        assert r.status_code == 400, r.text

    def test_repair_hypeusdt(self, hdr):
        r = requests.post(f"{BASE_URL}/api/localworker/data/repair",
                          json={"symbol": "HYPEUSDT"}, headers=hdr, timeout=30)
        assert r.status_code in (200, 202), r.text
        body = r.json()
        assert body.get("queued") or body.get("status") in ("queued", "ok")

        # Poll worker status for data_repair completion
        deadline = time.time() + 60
        job = None
        while time.time() < deadline:
            s = requests.get(f"{BASE_URL}/api/localworker/status", headers=hdr, timeout=15)
            assert s.status_code == 200
            data = s.json()
            recent = ((data.get("data_jobs") or {}).get("recent")) or []
            for entry in recent:
                if entry.get("kind") == "data_repair" and entry.get("status") == "done":
                    sym = (entry.get("summary") or {}).get("symbol") or entry.get("symbol")
                    if sym == "HYPEUSDT" or "HYPEUSDT" in str(entry):
                        job = entry
                        break
            if job:
                break
            time.sleep(2)
        assert job is not None, f"data_repair done job not found; last status={data}"
        summary = job.get("summary") or {}
        for key in ("before", "after", "added_primary", "added_secondary",
                    "primary_source", "secondary_source"):
            assert key in summary, f"missing {key} in summary {summary}"
        assert summary["primary_source"] == "bitunix"
        assert summary["secondary_source"] == "binance"


# ---------------- Feature 2: regime performance ----------
class TestRegimePerformance:
    DID = "dyn_8f1af31e"

    def test_default_mode(self, hdr):
        r = requests.get(f"{BASE_URL}/api/dynamic/{self.DID}/regime-performance", headers=hdr, timeout=15)
        assert r.status_code == 200, r.text
        js = r.json()
        assert "regimes" in js and isinstance(js["regimes"], list) and js["regimes"]
        for reg in js["regimes"]:
            assert "live" in reg and all(k in reg["live"] for k in ("trades", "win_rate", "pnl"))
            assert "walkforward" in reg
            assert "verdict" in reg and "status" in reg["verdict"] and "text" in reg["verdict"]

    def test_mode_live_and_paper(self, hdr):
        for m in ("live", "paper", "all"):
            r = requests.get(f"{BASE_URL}/api/dynamic/{self.DID}/regime-performance",
                             params={"mode": m}, headers=hdr, timeout=15)
            assert r.status_code == 200, f"mode {m}: {r.text}"

    def test_regime2_has_walkforward(self, hdr):
        r = requests.get(f"{BASE_URL}/api/dynamic/{self.DID}/regime-performance",
                         params={"mode": "all"}, headers=hdr, timeout=15)
        js = r.json()
        reg2 = next((x for x in js["regimes"] if x.get("regime") == 2 or x.get("id") == 2), None)
        assert reg2, f"regime 2 not present in {[r.get('regime') for r in js['regimes']]}"
        wf = reg2.get("walkforward") or {}
        # WF numbers should be present (non-empty dict/values)
        assert wf, f"walkforward empty for regime 2: {reg2}"

    def test_paper_trade_aggregation(self, hdr):
        # insert paper trades directly into Mongo, verify win_rate/pnl
        mongo_url = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
        db_name = os.environ.get("DB_NAME", "test_database")
        cli = MongoClient(mongo_url)
        col = cli[db_name]["auto_trades"]
        docs = [
            {"id": "TEST_it34_t1", "strategy_id": self.DID, "status": "closed",
             "mode": "paper", "realized_pnl": 2.5, "result": "win",
             "fees_paid": 0.1, "dynamic": {"regime": 2}},
            {"id": "TEST_it34_t2", "strategy_id": self.DID, "status": "closed",
             "mode": "paper", "realized_pnl": -1.0, "result": "loss",
             "fees_paid": 0.1, "dynamic": {"regime": 2}},
        ]
        try:
            col.insert_many(docs)
            r = requests.get(f"{BASE_URL}/api/dynamic/{self.DID}/regime-performance",
                             params={"mode": "paper"}, headers=hdr, timeout=15)
            assert r.status_code == 200
            js = r.json()
            reg2 = next((x for x in js["regimes"] if x.get("regime") == 2 or x.get("id") == 2), None)
            assert reg2
            live = reg2.get("live") or {}
            assert live["trades"] >= 2, live
            # win_rate is expressed as percent (0-100)
            assert 40.0 <= float(live["win_rate"]) <= 60.0, live
        finally:
            col.delete_many({"id": {"$in": ["TEST_it34_t1", "TEST_it34_t2"]}})


# ---------------- Feature 3: skip losing regimes ---------
class TestSkipLosingRegimes:
    RA = "ra_cceda77f"

    def test_build_all_skipped_400(self, hdr):
        r = requests.post(f"{BASE_URL}/api/regime-lab/{self.RA}/build",
                          json={"scope": "combined", "strategy_id": "nnfx_trend", "skip_regimes": [2]},
                          headers=hdr, timeout=30)
        assert r.status_code == 400, r.text
        assert "Alle Regime abgeschaltet" in r.text or "Alle" in r.text

    def test_build_without_skip_ok(self, hdr):
        r = requests.post(f"{BASE_URL}/api/regime-lab/{self.RA}/build",
                          json={"scope": "combined", "strategy_id": "nnfx_trend"},
                          headers=hdr, timeout=60)
        assert r.status_code in (200, 201, 202), r.text
