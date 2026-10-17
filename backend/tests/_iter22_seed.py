"""Seed script for iteration 22: custom strategy + closed trade for slippage-name test."""
import asyncio
import os
from datetime import datetime, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")

SID = "custom_test9999"
TID = "TEST_iter22_trade_1"


async def main():
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[os.environ["DB_NAME"]]
    await db.custom_strategies.update_one(
        {"id": SID}, {"$set": {"id": SID, "name": "Mein Test Strat"}}, upsert=True)
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": TID, "strategy_id": SID, "strategy_name": "Mein Test Strat",
        "slippage_pct": 0.05, "slippage_usdt": 0.1, "mode": "paper",
        "order_kind": "market", "side": "LONG", "entry": 100.0,
        "opened_at": now, "closed_at": now, "status": "closed",
        "exit_price": 101.0, "result": "win", "realized_pnl": 1.0,
        "qty": 1.0, "qty_remaining": 0.0, "sl": 99.0, "tp1": 101.0,
        "tpf": 102.0, "initial_sl": 99.0, "peak_price": 101.5,
        "trough_price": 99.5, "leverage": 10, "max_capital": 10,
        "fee_percent": 0.06, "fees_paid": 0.12, "symbol": "BTCUSDT",
    }
    await db.auto_trades.update_one({"id": TID}, {"$set": doc}, upsert=True)
    ext = await db.auto_trades.count_documents({"strategy_id": "external",
                                               "slippage_pct": {"$ne": None}})
    print("seeded", SID, TID, "external_slippage_trades=", ext)
    if ext == 0:
        edoc = dict(doc)
        edoc.update({"id": "TEST_iter22_trade_ext", "strategy_id": "external",
                     "strategy_name": None, "symbol": "ETHUSDT"})
        await db.auto_trades.update_one({"id": edoc["id"]}, {"$set": edoc},
                                        upsert=True)
        print("seeded external trade TEST_iter22_trade_ext")
    client.close()


asyncio.run(main())
