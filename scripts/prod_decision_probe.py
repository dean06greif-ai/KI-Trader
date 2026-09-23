"""Read-only: KI-Entscheidungen der Prod-DB nach Setup/Ergebnis auswerten."""
import asyncio, os, json, collections
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"])[os.environ["PROD_DB_NAME"]]
    d = await db.ai_decisions.find_one(sort=[("_id", -1)])
    print("KEYS", sorted(d.keys()))
    since = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
    rows = await db.ai_decisions.find({"ts": {"$gte": since}}, {"_id": 0, "setup": 1, "action": 1, "status": 1, "executed": 1, "skip_reason": 1, "reject_reason": 1, "block_reason": 1, "confidence": 1, "data_collection": 1}).to_list(50000)
    print("N14d", len(rows))
    c = collections.Counter()
    for r in rows:
        c[(r.get("setup"), r.get("action"), r.get("status") or r.get("executed"))] += 1
    for k, v in c.most_common(60): print(v, k)
asyncio.run(main())
