"""Read-only Probe gegen die Produktions-DB (nur find/aggregate, keine Writes)."""
import asyncio
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta

from motor.motor_asyncio import AsyncIOMotorClient


def _env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


async def main():
    env = _env("/app/backend/.env.prod")
    cl = AsyncIOMotorClient(env["MONGO_URL"])
    db = cl[env.get("DB_NAME", "crypto_scanner")]
    doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
    classes = doc.get("classes") or {}
    print("classes:", list(classes))
    for cls, scope in classes.items():
        print(f"\n=== {cls} ===")
        print(" live_blocked:", {k: v.get("at", "")[:10] for k, v in (scope.get("live_blocked") or {}).items()})
        print(" live_since:", {k: v[:10] for k, v in (scope.get("live_since") or {}).items()})
        print(" eval_since:", {k: v[:10] for k, v in (scope.get("eval_since") or {}).items()})
        print(" revisions:", {k: (v.get("version"), str(v.get("since"))[:10]) for k, v in (scope.get("revisions") or {}).items()})
        lc = scope.get("setup_lifecycle") or scope.get("lifecycle") or {}
        for sid, e in lc.items():
            vs = e.get("versions") or []
            if vs:
                print(f" profile {sid}: v{vs[-1].get('v')} since {str(vs[-1].get('since'))[:10]} ({len(vs)} versions)")
    print("\nscope keys:", list(next(iter(classes.values()), {}).keys()) if classes else None)
    # Stale-Forex: letzte Blockaden
    cutoff = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    rows = await db.ai_decisions.find({"ts": {"$gte": cutoff}, "blocked_by": re.compile("Stale")},
                                      {"ts": 1, "symbol": 1, "blocked_by": 1}).sort("ts", -1).to_list(15)
    for r in rows:
        print(r.get("ts"), r.get("symbol"), str(r.get("blocked_by"))[:110])
    # Setup-PnL total vs since eval/live
    agg = await db.auto_trades.aggregate([
        {"$match": {"strategy_id": "ai_trader", "status": "closed", "setup": {"$nin": [None, ""]}}},
        {"$group": {"_id": "$setup", "n": {"$sum": 1}, "pnl": {"$sum": "$realized_pnl"},
                    "first": {"$min": "$opened_at"}, "last": {"$max": "$opened_at"}}},
        {"$sort": {"pnl": 1}}]).to_list(50)
    print("\nSetups total:")
    for r in agg:
        print(f" {r['_id']:<24} n={r['n']:<5} pnl={r['pnl']:+9.2f} {str(r['first'])[:10]}..{str(r['last'])[:10]}")


asyncio.run(main())
