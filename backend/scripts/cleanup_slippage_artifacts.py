"""Bereinigt Phantom-Slippage-Artefakte (RCA custom_23a30b65): MARKET-Order-
Messungen, deren |slippage_pct| > 2% ist, stammen vom Bitunix-Schutzpreis
('price'-Feld) statt vom echten Fill und zerstören die Fill-Qualitäts-Statistik.

Default = Dry-Run (zeigt nur, was passieren würde). --apply schreibt.
--apply gegen PROD_MONGO_URL wird HART verweigert (auf Render ist MONGO_URL
selbst die Prod-DB -> dort ausführen: python scripts/cleanup_slippage_artifacts.py --apply).
"""
import argparse
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient

THRESHOLD_PCT = 2.0


async def main(apply: bool):
    mongo_url = os.environ["MONGO_URL"]
    prod_url = os.environ.get("PROD_MONGO_URL")
    if apply and prod_url and prod_url.split("@")[-1] in mongo_url:
        print("ABBRUCH: --apply gegen PROD_MONGO_URL ist verboten (nur lesend!). "
              "Auf Render ausführen, dort ist MONGO_URL die eigene DB.")
        sys.exit(2)
    db = AsyncIOMotorClient(mongo_url)[os.environ["DB_NAME"]]

    query = {"slippage_pct": {"$ne": None},
             "order_kind": {"$in": ["market", "taker_fallback", None]},
             "$or": [{"slippage_pct": {"$gt": THRESHOLD_PCT}},
                     {"slippage_pct": {"$lt": -THRESHOLD_PCT}}]}
    rows = await db.auto_trades.find(query, {"id": 1, "symbol": 1, "strategy_id": 1,
        "mode": 1, "slippage_pct": 1, "slippage_usdt": 1, "opened_at": 1}).to_list(5000)
    print(f"Kandidaten (|slippage_pct| > {THRESHOLD_PCT}%, market/taker): {len(rows)}")
    for t in rows:
        print(f"  {str(t.get('opened_at'))[:16]} {t.get('strategy_id')} {t.get('symbol')} "
              f"mode={t.get('mode')} slip={t.get('slippage_pct')}% ({t.get('slippage_usdt')} USDT)")
    if not apply:
        print("\nDRY-RUN: nichts geändert. Mit --apply werden slippage_pct/slippage_usdt "
              "entfernt (signal_price bleibt) + Marker slippage_artifact_cleared=true.")
        return
    res = await db.auto_trades.update_many(query, {
        "$unset": {"slippage_pct": "", "slippage_usdt": ""},
        "$set": {"slippage_artifact_cleared": True}})
    print(f"\nAPPLY: {res.modified_count} Trades bereinigt.")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="wirklich schreiben (Default: Dry-Run)")
    args = ap.parse_args()
    asyncio.run(main(args.apply))
