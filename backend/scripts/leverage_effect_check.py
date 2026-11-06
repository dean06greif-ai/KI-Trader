"""Hebel-Wirkungs-Check (read-only): vergleicht je Symbol den konfigurierten
KI-Hebel (strategy_coin_configs 'ai_trader_<SYM>' + Coin-Basis, Auto-Hebel-Status)
mit dem tatsächlich gefahrenen Hebel der ai_trader-Trades der letzten N Tage.

Aufruf:  python scripts/leverage_effect_check.py [--days 3] [--prod]
--prod nutzt PROD_MONGO_URL (nur lesend), sonst die lokale MONGO_URL.
Auf Render einfach ohne --prod ausführen (dort ist MONGO_URL die eigene DB).
"""
import argparse
import asyncio
import os
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient


async def main(days: int, prod: bool):
    url = os.environ["PROD_MONGO_URL" if prod else "MONGO_URL"]
    dbname = os.environ["PROD_DB_NAME" if prod else "DB_NAME"]
    db = AsyncIOMotorClient(url, serverSelectionTimeoutMS=10000)[dbname]

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    trades = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "opened_at": {"$gte": cutoff}},
        {"symbol": 1, "leverage": 1, "data_collection": 1, "opened_at": 1}).to_list(5000)
    at = await db.settings.find_one({"_id": "autotrade_config"}) or {}
    coins = at.get("coins") or {}

    by_sym = defaultdict(list)
    for t in trades:
        by_sym[t.get("symbol")].append(t)

    print(f"Hebel-Wirkungs-Check ({'PROD' if prod else 'lokal'}, letzte {days} Tage, "
          f"{len(trades)} Trades)\n")
    print(f"{'Symbol':12s} {'konfiguriert':22s} {'gefahren (Trades)':28s} Urteil")
    n_mm = 0
    for sym in sorted(by_sym):
        scc = await db.strategy_coin_configs.find_one({"_id": f"ai_trader_{sym}"}) or {}
        merged = {**(coins.get(sym) or {}), **(scc.get("config") or {})}
        auto_on = bool(merged.get("auto_leverage_enabled"))
        cfg_lev = merged.get("leverage")
        cfg_txt = (f"auto (max {merged.get('auto_lev_max', 50)})" if auto_on
                   else f"fix {cfg_lev}x")
        levs = sorted({round(float(t.get("leverage") or 0), 2) for t in by_sym[sym]})
        levs_txt = ",".join(f"{v:g}" for v in levs[:6]) + f"  n={len(by_sym[sym])}"
        if auto_on:
            verdict = "auto aktiv – fester Hebel greift NICHT"
            n_mm += 1
        else:
            try:
                ok = all(abs(v - float(cfg_lev)) < 0.011 for v in levs)
            except (TypeError, ValueError):
                ok = False
            verdict = "OK ✓" if ok else "MISMATCH ✗ (Trade-Hebel != Config)"
            n_mm += 0 if ok else 1
        print(f"{sym:12s} {cfg_txt:22s} {levs_txt:28s} {verdict}")
    print(f"\nAbweichungen/Auto-Hebel: {n_mm} von {len(by_sym)} Symbolen. "
          "Nach dem Deploy der Boot-Migration sollten Senkungs-Symbole 'fix ...x / OK' zeigen.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--prod", action="store_true", help="PROD_MONGO_URL nur-lesend nutzen")
    args = ap.parse_args()
    asyncio.run(main(args.days, args.prod))
