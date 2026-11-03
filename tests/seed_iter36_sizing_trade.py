"""Seed/Cleanup des Sizing-Testtrades (Iteration 36)."""
import os
import sys
from datetime import datetime, timezone

from dotenv import load_dotenv
from pymongo import MongoClient

load_dotenv("/app/backend/.env")
client = MongoClient(os.environ["MONGO_URL"])
db = client[os.environ["DB_NAME"]]
TID = "TEST-SIZING-1"

if len(sys.argv) > 1 and sys.argv[1] == "clean":
    print("deleted:", db.auto_trades.delete_many({"id": TID}).deleted_count)
else:
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": TID, "symbol": "BTCUSDT", "side": "LONG", "mode": "paper",
        "status": "closed", "strategy_id": "ai_trader", "strategy_name": "KI Trader",
        "entry": 77000, "exit_price": 77300, "sl": 76800, "tp1": 77400, "tpf": 77800,
        "qty": 0.01, "qty_remaining": 0, "leverage": 50, "max_capital": 40,
        "realized_pnl": 3.0, "fees_paid": 0.05, "setup": "liquidity_sweep",
        "ai_confidence": 72, "ai_reasoning": "Testtrade",
        "opened_at": now, "closed_at": now,
        "trade_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "result": "win", "events": ["OPEN LONG @ 77000"],
        "sizing": {"risk_usdt": 10, "risk_pct_eff": 1.0, "equity": 1000,
                   "sl_dist_pct": 0.26, "leverage": 50, "margin": 40,
                   "scale": 0.5, "capped": False, "note": "Test"},
    }
    db.auto_trades.replace_one({"id": TID}, doc, upsert=True)
    print("seeded", TID)
