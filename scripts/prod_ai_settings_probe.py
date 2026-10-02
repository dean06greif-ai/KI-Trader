"""Read-only: KI-Trader-Performance je Welt/Setup + Kapital-/Risiko-Settings (Prod-DB)."""
import asyncio, os, collections, json
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"])[os.environ["PROD_DB_NAME"]]
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    agg = collections.defaultdict(lambda: [0, 0, 0.0, 0.0, 0.0, []])
    async for t in db.auto_trades.find({"strategy_id": "ai_trader", "status": "closed", "closed_at": {"$gte": since}}):
        w = "coll" if t.get("data_collection") else t.get("mode")
        for key in (w, f"{w}:{t.get('setup')}", f"{w}:cls:{t.get('asset_class') or '-'}"):
            a = agg[key]; p = float(t.get("realized_pnl") or 0)
            a[0] += 1; a[1] += p > 0; a[2] += p; a[3] += float(t.get("fees_paid") or 0); a[4] += float(t.get("leverage") or 0)
            if t.get("close_reason") or t.get("result"):
                a[5].append(t.get("result") or t.get("close_reason"))
    for k in sorted(agg, key=lambda x: (x.split(":")[0], -agg[x][0])):
        n, w, p, f, lev, res = agg[k]
        print(f"{k:40s} n={n:4d} WR={100*w/n:5.1f}% PnL={p:9.2f} Fees={f:8.2f} avgLev={lev/n:5.1f} {collections.Counter(res).most_common(3)}")
    for sid in ("capital_config", "risk_budget_config", "trade_mode", "autotrader_config"):
        d = await db.settings.find_one({"_id": sid})
        if d:
            d.pop("_id", None)
            print(sid, json.dumps({k: v for k, v in d.items() if len(json.dumps(v, default=str)) < 150}, default=str)[:700])
    live_open = await db.auto_trades.count_documents({"status": "open", "mode": "live"})
    print("open live", live_open)

asyncio.run(main())
