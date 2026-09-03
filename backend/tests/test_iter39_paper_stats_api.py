"""Iteration 39 – BUGFIX-Verifikation (API, gegen laufendes Backend):
  * Datensammel-Trades (data_collection=true) zaehlen NICHT in die Paper-Statistik
  * Strategie-Vergleich blendet Karteileichen (gelöschte Strategien) aus
  * /api/autotrade/trades liefert data_collection-Flag
  * Regressions-Endpoints (watchdog, playbook, backtest/optimizer active)
"""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
if not BASE:
    raise RuntimeError("REACT_APP_BACKEND_URL fehlt")
TAG = uuid.uuid4().hex[:8]
STALE_ID = f"custom_deleted_qa_{TAG}"
TIMEOUT = 60


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    yield client[os.environ["DB_NAME"]]
    client.close()


@pytest.fixture(scope="module")
def seeded(db):
    base = {"symbol": "BTCUSDT", "side": "LONG", "status": "closed", "result": "win",
            "opened_at": "2099-01-01T00:00:00+00:00",
            "closed_at": "2099-01-01T01:00:00+00:00",
            "max_capital": 100.0, "fees_paid": 0.0, "mode": "paper"}
    rows = [
        {**base, "id": f"TEST_QA_PAPER_{TAG}", "strategy_id": "ai_trader",
         "strategy_name": "KI-Trader", "realized_pnl": 10.0},
        {**base, "id": f"TEST_QA_DC_{TAG}", "strategy_id": "ai_trader",
         "strategy_name": "KI-Trader", "realized_pnl": 77.0, "data_collection": True},
        {**base, "id": f"TEST_QA_STALE_{TAG}", "strategy_id": STALE_ID,
         "strategy_name": "Gelöscht", "realized_pnl": 5.0},
    ]
    db.auto_trades.insert_many([dict(r) for r in rows])
    yield rows
    db.auto_trades.delete_many({"id": {"$regex": f"_{TAG}$"}})


def _cmp(**params):
    r = requests.get(f"{BASE}/api/analytics/strategy-comparison", params=params, timeout=TIMEOUT)
    assert r.status_code == 200, r.text[:300]
    return r.json()


def _row(data, sid):
    return next((x for x in data["comparison"] if x["strategy_id"] == sid), None)


class TestPaperStatsCollectionStale:
    """/api/analytics/strategy-comparison – data_collection + Karteileichen"""

    def test_default_excludes_collection_and_stale(self, seeded):
        d = _cmp(mode="paper")
        assert d["include_collection"] is False
        assert d["include_stale"] is False
        assert isinstance(d["total_trades"], int)
        assert _row(d, STALE_ID) is None, "Karteileiche darf nicht erscheinen"
        assert STALE_ID in d["stale_strategies"]
        ki = _row(d, "ai_trader")
        assert ki is not None and ki["is_stale"] is False

    def test_include_collection_delta(self, seeded):
        off = _cmp(mode="paper")
        on = _cmp(mode="paper", include_collection="true")
        ki_off, ki_on = _row(off, "ai_trader"), _row(on, "ai_trader")
        assert ki_on["trades"] == ki_off["trades"] + 1
        assert round(ki_on["pnl"] - ki_off["pnl"], 2) == 77.0
        assert on["include_collection"] is True
        assert on["total_trades"] == off["total_trades"] + 1

    def test_include_stale_shows_deleted_strategy(self, seeded):
        d = _cmp(mode="paper", include_stale="true")
        row = _row(d, STALE_ID)
        assert row is not None, "mit include_stale muss die Karteileiche erscheinen"
        assert row["is_stale"] is True
        assert row["trades"] == 1
        assert round(row["pnl"], 2) == 5.0
        assert d["include_stale"] is True

    def test_closed_trades_expose_data_collection_flag(self, seeded):
        r = requests.get(f"{BASE}/api/autotrade/trades",
                         params={"status": "closed", "limit": 500}, timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        payload = r.json()
        trades = payload if isinstance(payload, list) else payload.get("trades", [])
        assert trades, "keine geschlossenen Trades geliefert"
        assert any("data_collection" in t for t in trades), \
            "kein Trade traegt das data_collection-Flag"

    def test_performance_endpoint_ok(self, seeded):
        r = requests.get(f"{BASE}/api/performance", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        rows = data if isinstance(data, list) else data.get("performance", [])
        assert isinstance(rows, list)


class TestRegressionEndpoints:
    """Regression: Kern-Endpoints der letzten Iterationen"""

    @pytest.mark.parametrize("path", [
        "/api/autotrade/watchdog/status",
        "/api/ai/playbook",
        "/api/backtest/active",
        "/api/optimizer/active",
    ])
    def test_endpoint_200(self, path):
        r = requests.get(f"{BASE}{path}", timeout=TIMEOUT)
        assert r.status_code == 200, f"{path}: {r.status_code} {r.text[:200]}"
        assert r.json() is not None
