"""Read-only: Prod-KI-Config gegen empfohlene Einstellungen + Rollen-Team prüfen."""
import asyncio, os, re, sys
sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv; load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient
from services import ai_recommended_settings as rec
async def main():
    raw = os.environ['PROD_MONGO_URL']; url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    db = AsyncIOMotorClient(url)['crypto_scanner']
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    for r in rec.diff(cfg):
        print(f"{'ABWEICHUNG' if r['differs'] else 'ok        '} {r['key']:28s} prod={r['current']!r:12} empfohlen={r['value']!r}")
    for k in ("enabled", "risk_per_trade_pct", "risk_max_leverage", "risk_max_margin_pct", "min_confidence",
              "signal_broker_enabled", "signal_review_enabled", "signal_min_ki_trades", "setup_trigger_enabled"):
        print(f"           {k:28s} prod={cfg.get(k)!r}")
    tg = await db.settings.find_one({"_id": "telegram_state"}) or {}
    print("telegram_state keys:", [k for k in tg if k != "_id"][:15])
    at = await db.settings.find_one({"_id": "autotrade_config"}) or {}
    print("autotrade live:", {k: at.get(k) for k in ("live_enabled", "mode", "enabled", "trade_mode") if k in at})
    last = await db.auto_trades.find({"strategy_id": "ai_trader"}).sort("opened_at", -1).limit(1).to_list(1)
    print("letzter KI-Trade:", (last[0].get("opened_at"), last[0].get("mode"), last[0].get("status")) if last else None)
asyncio.run(main())
