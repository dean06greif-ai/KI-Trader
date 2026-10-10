"""Read-only: Setup-Nutzung (Entscheidungen -> Signale -> Trades) der Prod-DB."""
import asyncio, os, collections
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"])[os.environ["PROD_DB_NAME"]]
    since = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
    d = await db.ai_decisions.find_one({"action": {"$ne": "HOLD"}}, sort=[("_id", -1)])
    print("DEC", {k: (str(v)[:80]) for k, v in d.items() if k not in ("reasoning", "entry_market_snapshot")})
    sig = collections.Counter(); tot = collections.Counter()
    async for r in db.ai_decisions.find({"ts": {"$gte": since}, "action": {"$ne": "HOLD"}}, {"setup": 1, "signaled": 1, "skip": 1}):
        tot[r.get("setup")] += 1; sig[r.get("setup")] += 1 if r.get("signaled") else 0
    print("SIGNALED/TOTAL", {k: f"{sig[k]}/{v}" for k, v in tot.most_common()})
    tc = collections.Counter()
    async for t in db.auto_trades.find({"opened_at": {"$gte": since}}, {"ai_setup": 1, "setup": 1, "mode": 1, "data_collection": 1, "strategy_id": 1}):
        tc[(t.get("ai_setup") or t.get("setup"), t.get("mode"), bool(t.get("data_collection")))] += 1
    for k, v in sorted(tc.items(), key=lambda x: -x[1]): print(v, k)
    pb = await db.settings.find_one({"_id": "ai_playbook"}) or {}
    print("PLAYBOOK keys", list(pb.keys())[:30])
    for c in await db.ai_strategy_candidates.find({}, {"_id": 0, "id": 1, "name": 1, "status": 1, "setup": 1, "ghost_trades": 1, "stats": 1}).to_list(20):
        print("CAND", str(c)[:300])
asyncio.run(main())
