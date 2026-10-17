"""Read-only: Trade-Aktivität je KI-Trader-Setup über die Zeit (Prod-DB, NUR LESEND)."""
import asyncio
import collections
import os
import sys

from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"])[os.environ["PROD_DB_NAME"]]
    t = await db.trades.find_one({"source": "ai_trader"}, sort=[("_id", -1)])
    d = await db.ai_decisions.find_one({"action": {"$in": ["LONG", "SHORT"]}}, sort=[("_id", -1)])
    h = await db.ai_decisions.find_one({"action": "HOLD"}, sort=[("_id", -1)])
    if "keys" in sys.argv:
        print("TRADE keys:", sorted((t or {}).keys()))
        print("DECISION keys:", sorted((d or {}).keys()))
        for k in ("setup", "setup_id", "playbook_setup", "setup_key", "setup_name", "playbook"):
            print(k, "trade=", str((t or {}).get(k))[:200], "| dec=", str((d or {}).get(k))[:200])
        print("HOLD sample:", {k: str(v)[:160] for k, v in (h or {}).items() if k not in ("_id", "prompt", "raw")})
        return
    field = sys.argv[1] if len(sys.argv) > 1 else "setup_id"
    by_week = collections.defaultdict(collections.Counter)
    async for x in db.trades.find({"source": "ai_trader"}, {field: 1, "opened_at": 1, "created_at": 1}):
        wk = str(x.get("opened_at") or x.get("created_at") or "")[:7]
        by_week[str(x.get(field))[:40]][wk] += 1
    for k, c in sorted(by_week.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"{k:40s}", dict(sorted(c.items())))


asyncio.run(main())
