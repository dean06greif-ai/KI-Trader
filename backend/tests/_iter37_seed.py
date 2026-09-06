"""Seed 3 open paper trades (2x BTCUSDT, 1x ETHUSDT) for the chart-badge test."""
import os
from datetime import datetime, timezone, timedelta

import pymongo
from dotenv import dotenv_values

env = dotenv_values("/app/backend/.env")
client = pymongo.MongoClient(os.environ.get("MONGO_URL") or env["MONGO_URL"])
db = client[env["DB_NAME"].strip('"')]

now = datetime.now(timezone.utc)


def doc(tid, symbol, side, entry, sl, tpf):
    return {
        "id": tid,
        "symbol": symbol,
        "side": side,
        "status": "open",
        "mode": "paper",
        "strategy_id": "TEST_STRAT",
        "strategy_name": "TEST Strategy",
        "entry": entry,
        "sl": sl,
        "tp1": tpf * 0.995,
        "tpf": tpf,
        "qty": 0.01,
        "qty_remaining": 0.01,
        "leverage": 5,
        "margin_used": entry * 0.01 / 5,
        "opened_at": (now - timedelta(minutes=30)).isoformat(),
        "tp1_hit": False,
        "horizon": "intraday",
        "realized_pnl": 0.0,
    }


rows = [
    doc("TEST-BTC-1", "BTCUSDT", "LONG", 60000.0, 59000.0, 62000.0),
    doc("TEST-BTC-2", "BTCUSDT", "SHORT", 61000.0, 62000.0, 59000.0),
    doc("TEST-ETH-1", "ETHUSDT", "LONG", 3000.0, 2900.0, 3200.0),
]
for r in rows:
    db.auto_trades.replace_one({"id": r["id"]}, r, upsert=True)
print("seeded:", db.auto_trades.count_documents({"status": "open"}))
for t in db.auto_trades.find({"status": "open"}, {"_id": 0, "id": 1, "symbol": 1}):
    print(t)
