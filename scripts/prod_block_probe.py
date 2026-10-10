"""Read-only: Warum wurden KI-Entscheidungen (LONG/SHORT) nicht gehandelt? (Prod-DB)"""
import asyncio, os, re, collections
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

def norm(s):
    s = re.sub(r"[0-9.,:]+", "#", str(s or ""))
    return s[:90]

async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"])[os.environ["PROD_DB_NAME"]]
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    print("CFG", {k: cfg.get(k) for k in ("min_confidence", "collection_enabled", "collection_min_confidence", "collection_cooldown_min", "cooldown_min", "setup_live_gate", "max_open_trades", "dup_entry_window_min")})
    since = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()
    by = collections.Counter(); per_setup = collections.defaultdict(collections.Counter)
    async for r in db.ai_decisions.find({"ts": {"$gte": since}, "action": {"$in": ["LONG", "SHORT"]}, "signaled": {"$ne": True}}, {"setup": 1, "blocked_by": 1, "live_gate": 1, "confidence": 1}):
        reason = norm(r.get("blocked_by")) if r.get("blocked_by") else ("conf<" if float(r.get("confidence") or 0) < 60 else "no-reason(cooldown/session?) conf=%s" % r.get("confidence"))
        by[reason] += 1; per_setup[r.get("setup")][reason[:40]] += 1
    for k, v in by.most_common(25): print(v, k)
    for s, c in per_setup.items(): print("SETUP", s, c.most_common(4))
asyncio.run(main())
