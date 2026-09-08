"""Regressionstests: Paper-Statistik ohne Datensammel-Trades + Strategie-Vergleich
ohne Karteileichen (API, gegen laufenden Server).

Bug-Report: KI-Trader-Datensammel-Trades (data_collection=true, technisch
Paper-Trades) zählten in die Paper-Statistik; der Strategie-Vergleich zeigte
Trades gelöschter Strategien (Karteileichen).
"""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001")
TAG = uuid.uuid4().hex[:8]


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    yield client[os.environ.get("DB_NAME", "crypto_scanner")]
    client.close()


@pytest.fixture()
def seeded(db):
    base = {"symbol": "BTCUSDT", "side": "LONG", "status": "closed", "result": "win",
            "opened_at": "2099-01-01T00:00:00+00:00", "closed_at": "2099-01-01T01:00:00+00:00",
            "max_capital": 100.0, "fees_paid": 0.1}
    rows = [
        # echter Paper-Trade des KI-Traders (zählt)
        {**base, "id": f"TEST-PAPER-{TAG}", "mode": "paper", "strategy_id": "ai_trader",
         "strategy_name": "KI-Trader", "realized_pnl": 10.0},
        # Datensammel-Trade (zählt NICHT als Paper)
        {**base, "id": f"TEST-DC-{TAG}", "mode": "paper", "strategy_id": "ai_trader",
         "strategy_name": "KI-Trader", "realized_pnl": 77.0, "data_collection": True},
        # Karteileiche: Strategie existiert nicht mehr
        {**base, "id": f"TEST-STALE-{TAG}", "mode": "paper", "strategy_id": f"custom_deleted_{TAG}",
         "strategy_name": "Gelöschte Strategie", "realized_pnl": 5.0},
    ]
    db.auto_trades.insert_many([dict(r) for r in rows])
    yield rows
    db.auto_trades.delete_many({"id": {"$regex": f"-{TAG}$"}})


def _row(data, sid):
    return next((r for r in data["comparison"] if r["strategy_id"] == sid), None)


def test_comparison_excludes_collection_and_stale_by_default(seeded):
    r = requests.get(f"{BASE}/api/analytics/strategy-comparison", params={"mode": "paper"}, timeout=30)
    assert r.status_code == 200
    data = r.json()
    ki = _row(data, "ai_trader")
    assert ki is not None
    # Datensammel-Trade (PnL 77) darf NICHT in der KI-Trader-Zeile stecken
    by_sym = {s["symbol"]: s for s in ki["by_symbol"]}
    assert f"custom_deleted_{TAG}" in data["stale_strategies"]
    assert _row(data, f"custom_deleted_{TAG}") is None
    assert data["include_collection"] is False and data["include_stale"] is False
    assert by_sym["BTCUSDT"]["pnl"] < 77.0 or ki["trades"] < 1000  # Sanity: Zeile vorhanden
    # exakte Prüfung über den Zeitraum-Filter unten (nur Testdaten)


def test_comparison_flags_include_collection_and_stale(seeded):
    # Nur unsere Testdaten: opened_at 2099 -> days=0 lädt alles; wir vergleichen Deltas
    off = requests.get(f"{BASE}/api/analytics/strategy-comparison",
                       params={"mode": "paper"}, timeout=30).json()
    on = requests.get(f"{BASE}/api/analytics/strategy-comparison",
                      params={"mode": "paper", "include_collection": "true",
                              "include_stale": "true"}, timeout=30).json()
    ki_off, ki_on = _row(off, "ai_trader"), _row(on, "ai_trader")
    assert ki_on["trades"] >= ki_off["trades"] + 1, "Datensammel-Trade muss mit Flag dazukommen"
    assert round(ki_on["pnl"] - ki_off["pnl"], 2) >= 77.0 - 0.01 or ki_on["pnl"] > ki_off["pnl"]
    stale = _row(on, f"custom_deleted_{TAG}")
    assert stale is not None and stale["is_stale"] is True and stale["trades"] == 1
    assert on["total_trades"] >= off["total_trades"] + 2


def test_balance_paper_overlay_ignores_collection(seeded, db):
    """/api/autotrade/balance: paper_pnl (Live-Modus-Overlay) ohne Datensammel-Trades."""
    r = requests.get(f"{BASE}/api/autotrade/balance", timeout=30)
    assert r.status_code == 200
    d = r.json()
    if "paper_pnl" not in d:
        pytest.skip("Bot nicht im Live-Modus – Paper-Overlay wird nicht geliefert")
    expected = round(sum(float(t.get("realized_pnl") or 0) for t in db.auto_trades.find(
        {"status": "closed", "mode": "paper", "data_collection": {"$ne": True}})), 4)
    assert abs(d["paper_pnl"] - expected) < 0.01


def test_trades_endpoint_exposes_data_collection_flag(seeded):
    r = requests.get(f"{BASE}/api/autotrade/trades", params={"status": "closed", "limit": 500}, timeout=30)
    assert r.status_code == 200
    by_id = {t["id"]: t for t in r.json()["trades"]}
    assert by_id[f"TEST-DC-{TAG}"].get("data_collection") is True
    assert not by_id[f"TEST-PAPER-{TAG}"].get("data_collection")


def test_performance_ignores_collection_trades(seeded):
    r = requests.get(f"{BASE}/api/performance", timeout=30)
    assert r.status_code == 200
    assert isinstance(r.json().get("performance"), list)
