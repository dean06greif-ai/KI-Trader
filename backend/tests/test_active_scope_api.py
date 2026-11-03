"""E2E-Regression (07.09.2026): Strategie-Vergleich und Balance/Paper-Badge
zählen nur Trades derzeit freigeschalteter Strategie×Asset-Kombinationen.
Braucht laufendes Backend (REACT_APP_BACKEND_URL) + dieselbe DB (MONGO_URL)."""
import os
import uuid

import pytest
import requests
from pymongo import MongoClient

BASE = os.environ.get("REACT_APP_BACKEND_URL", "http://localhost:8001").rstrip("/")
SYMBOL = "ZZTESTUSDT"          # Kunst-Symbol: kollidiert mit keinem echten Asset
STRAT = "rsi_only"             # existierende Strategie (keine Karteileiche)


@pytest.fixture(scope="module")
def db():
    client = MongoClient(os.environ["MONGO_URL"])
    yield client[os.environ.get("DB_NAME", "crypto_scanner")]
    client.close()


def _trade(mode: str, pnl: float) -> dict:
    tid = f"TEST-ACTIVE-{uuid.uuid4().hex[:8]}"
    return {"id": tid, "symbol": SYMBOL, "side": "LONG", "mode": mode,
            "status": "closed", "strategy_id": STRAT, "strategy_name": "RSI Only",
            "result": "win" if pnl > 0 else "loss", "realized_pnl": pnl,
            "opened_at": "2099-01-01T00:00:00+00:00",
            "closed_at": "2099-01-01T01:00:00+00:00"}


@pytest.fixture()
def inactive_pair_trade(db):
    """Geschlossener Paper-Trade einer Kombination OHNE Freischaltung (mode off)."""
    key = f"{STRAT}_{SYMBOL}"
    db.strategy_coin_configs.replace_one(
        {"_id": key}, {"_id": key, "config": {"mode": "off"}}, upsert=True)
    doc = _trade("paper", 77.77)
    db.auto_trades.insert_one(dict(doc))
    yield doc
    db.auto_trades.delete_many({"id": doc["id"]})
    db.strategy_coin_configs.delete_one({"_id": key})


def _row(comparison, sid):
    return next((r for r in comparison if r["strategy_id"] == sid), None)


def test_comparison_hides_inactive_pair_by_default(inactive_pair_trade):
    r = requests.get(f"{BASE}/api/analytics/strategy-comparison",
                     params={"mode": "paper"}, timeout=30)
    assert r.status_code == 200
    d = r.json()
    assert d["only_active"] is True
    assert d["inactive_hidden"] >= 1
    row = _row(d["comparison"], STRAT)
    assert row is None or not any(s["symbol"] == SYMBOL for s in row["by_symbol"])


def test_comparison_shows_inactive_pair_when_requested(inactive_pair_trade):
    r = requests.get(f"{BASE}/api/analytics/strategy-comparison",
                     params={"mode": "paper", "only_active": "false"}, timeout=30)
    d = r.json()
    assert d["only_active"] is False and d["inactive_hidden"] == 0
    row = _row(d["comparison"], STRAT)
    assert row is not None
    assert any(s["symbol"] == SYMBOL for s in row["by_symbol"])


def test_comparison_counts_pair_once_activated(inactive_pair_trade, db):
    key = f"{STRAT}_{SYMBOL}"
    db.strategy_coin_configs.replace_one(
        {"_id": key}, {"_id": key, "config": {"mode": "paper"}}, upsert=True)
    r = requests.get(f"{BASE}/api/analytics/strategy-comparison",
                     params={"mode": "paper"}, timeout=30)
    row = _row(r.json()["comparison"], STRAT)
    assert row is not None and any(s["symbol"] == SYMBOL for s in row["by_symbol"])


def test_balance_paper_stats_exclude_inactive_pair(inactive_pair_trade, db):
    r = requests.get(f"{BASE}/api/autotrade/balance", timeout=60)
    assert r.status_code == 200
    d = r.json()
    assert d.get("active_only") is True
    key = f"{STRAT}_{SYMBOL}"
    if d.get("mode") == "paper":
        before = d["closed_trades"]
        db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": {"mode": "paper"}}, upsert=True)
        after = requests.get(f"{BASE}/api/autotrade/balance", timeout=60).json()["closed_trades"]
        assert after == before + 1
    else:
        before = d.get("paper_closed_trades", 0)
        db.strategy_coin_configs.replace_one(
            {"_id": key}, {"_id": key, "config": {"mode": "paper"}}, upsert=True)
        after = requests.get(f"{BASE}/api/autotrade/balance", timeout=60).json()
        assert after.get("paper_closed_trades", 0) == before + 1
