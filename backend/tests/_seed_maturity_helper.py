"""Seed script: setzt live_ready.breakout=False und legt 6 Testtrades an."""
import asyncio
import sys
from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient

MONGO_URL = "mongodb://localhost:27017"
DB_NAME = "crypto_scanner"
TEST_ID_PREFIX = "TEST-MATURITY-"


async def main(action):
    c = AsyncIOMotorClient(MONGO_URL)
    db = c[DB_NAME]
    if action == "seed":
        doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
        lr = dict(doc.get("live_ready") or {})
        lr["breakout"] = False
        await db.settings.update_one(
            {"_id": "ai_playbook_state"},
            {"$set": {"live_ready": lr}}, upsert=True)
        pnls = [5.0, 4.0, 3.0, 2.0, -1.0, -2.0]
        now = datetime.now(timezone.utc).isoformat()
        trades = [{
            "id": f"{TEST_ID_PREFIX}{i}",
            "strategy_id": "ai_trader", "status": "closed",
            "setup": "breakout", "opened_at": now, "closed_at": now,
            "realized_pnl": p, "mode": "paper", "symbol": "BTCUSDT",
            "side": "LONG",
        } for i, p in enumerate(pnls)]
        # remove any previous
        await db.auto_trades.delete_many(
            {"id": {"$regex": f"^{TEST_ID_PREFIX}"}})
        await db.auto_trades.insert_many(trades)
        # also remove any previous playbook msg
        await db.ai_chat.delete_many(
            {"role": "playbook", "setup": "breakout",
             "text": {"$regex": "LIVE-freigeschaltet"}})
        print("SEEDED")
    elif action == "cleanup":
        tr = await db.auto_trades.delete_many(
            {"id": {"$regex": f"^{TEST_ID_PREFIX}"}})
        msg = await db.ai_chat.delete_many(
            {"role": "playbook", "setup": "breakout",
             "text": {"$regex": "LIVE-freigeschaltet"}})
        doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
        lr = dict(doc.get("live_ready") or {})
        if "breakout" in lr:
            lr["breakout"] = False
        await db.settings.update_one(
            {"_id": "ai_playbook_state"},
            {"$set": {"live_ready": lr}}, upsert=True)
        print(f"CLEANED trades={tr.deleted_count} msgs={msg.deleted_count}")
    elif action == "verify":
        tr = await db.auto_trades.count_documents(
            {"id": {"$regex": f"^{TEST_ID_PREFIX}"}})
        msg = await db.ai_chat.count_documents(
            {"role": "playbook", "setup": "breakout",
             "text": {"$regex": "LIVE-freigeschaltet"}})
        doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
        lr = (doc or {}).get("live_ready", {})
        print(f"trades={tr} msgs={msg} live_ready.breakout={lr.get('breakout')}")
    c.close()

asyncio.run(main(sys.argv[1]))
