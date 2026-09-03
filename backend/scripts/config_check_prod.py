"""Read-only: Config-Aufloesung Hebel + Reward-Scores + Gate-Metriken. NIE schreiben."""
import os, asyncio, json
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    c = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"], serverSelectionTimeoutMS=10000)
    db = c[os.environ["PROD_DB_NAME"]]

    keys = ("leverage", "auto_leverage_enabled", "auto_lev_max", "mode", "max_capital",
            "sl_mode", "max_leverage")

    print("## Q1a) Collection strategy_coin_configs ai_trader_*")
    cur = db.strategy_coin_configs.find({"_id": {"$regex": "^ai_trader_"}})
    async for d in cur:
        cfg = d.get("config") or {}
        keep = {k: cfg.get(k) for k in keys if k in cfg}
        print(f"  {d.get('_id'):26s} {json.dumps(keep, ensure_ascii=False)}")

    print("\n## Q1b) settings.autotrade_config.strategy_coin_configs ai_trader_*")
    at = await db.settings.find_one({"_id": "autotrade_config"}) or {}
    scc_map = at.get("strategy_coin_configs") or {}
    for k in sorted(scc_map):
        if k.startswith("ai_trader_"):
            cfg = scc_map[k] or {}
            keep = {kk: cfg.get(kk) for kk in keys if kk in cfg}
            print(f"  {k:26s} {json.dumps(keep, ensure_ascii=False)}")

    print("\n## Q2) Coin-Settings (autotrade_config.coins) BTC/EURUSD/USDJPY/DOT/SILVER/SPY/ETH")
    coins = at.get("coins") or {}
    for sym in ("BTCUSDT", "EURUSD", "USDJPY", "DOTUSDT", "SILVER", "SPYUSDT", "ETHUSDT"):
        cfg = coins.get(sym) or {}
        keep = {k: cfg.get(k) for k in keys if k in cfg}
        print(f"  {sym:10s} {json.dumps(keep, ensure_ascii=False)[:160]}")

    print("\n## Q3) ai_rewards: score-Verteilung (Feld heisst score, nicht reward)")
    rows = await db.ai_rewards.find({}, {"score": 1, "regime": 1, "ts": 1}).to_list(1000)
    pos = sum(1 for r in rows if float(r.get("score") or 0) > 0)
    neg = sum(1 for r in rows if float(r.get("score") or 0) < 0)
    print(f"  n={len(rows)}, score>0: {pos}, score<0: {neg}, "
          f"Ø={sum(float(r.get('score') or 0) for r in rows)/max(1,len(rows)):+.2f}")

    print("\n## Q4) ml_gate v37 Metriken (korrekte Keys)")
    m = await db.ml_gate_models.find_one({}, sort=[("version", -1)], projection={"booster_b64": 0})
    met = (m or {}).get("metrics") or {}
    print(f"  v{m.get('version')} samples={m.get('samples')} win_rate_data={m.get('win_rate_data')}")
    print(f"  metrics: {json.dumps(met, ensure_ascii=False)[:600]}")
    imp = (m or {}).get("importances") or []
    print("  Top-Features:", [(x.get('feature'), x.get('share_pct')) for x in imp[:6]])

    print("\n## Q5) Trade-Manager-Settings (max_leverage etc.)")
    tm = await db.settings.find_one({"_id": "ai_trade_manager"}) or {}
    print("  ", json.dumps({k: v for k, v in tm.items() if k != "_id"}, ensure_ascii=False)[:400])

    print("\n## Q6) BTC-Trade 25.08 02:22: welcher Hebel-Pfad? (auto_leverage-Flag am Trade)")
    trs = await db.auto_trades.find({"strategy_id": "ai_trader", "symbol": "BTCUSDT"},
        {"opened_at": 1, "leverage": 1, "auto_leverage": 1, "mode": 1, "data_collection": 1}) \
        .sort("opened_at", -1).to_list(6)
    for t in trs:
        print(f"    {str(t.get('opened_at'))[:16]} lev={t.get('leverage')} "
              f"auto={t.get('auto_leverage')} dc={t.get('data_collection')}")
    trs = await db.auto_trades.find({"strategy_id": "ai_trader", "symbol": "EURUSD"},
        {"opened_at": 1, "leverage": 1, "auto_leverage": 1}).sort("opened_at", -1).to_list(4)
    for t in trs:
        print(f"    EURUSD {str(t.get('opened_at'))[:16]} lev={t.get('leverage')} auto={t.get('auto_leverage')}")

    print("\n## Q7) analyst-Calls/Tag vs. Decisions: smart_skip-Wirkung")
    d7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    n_dec = await db.ai_decisions.count_documents({"ts": {"$gte": d7}})
    print(f"  Decisions 7d: {n_dec} (Analyst-Calls 7d: 1979) -> Gruppen-Analyse bündelt Symbole")

    c.close()

asyncio.run(main())
