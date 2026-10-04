"""Seed für die Nachanalyse: geschlossene KI-Trades + verfallene Limit-Order
(lokale Test-DB). Nutzt echte BTCUSDT-1m-Kerzen der letzten Stunden, damit der
Review reale Daten sieht. Aufruf: python tests/_seed_postmortem.py"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tests import conftest  # noqa: F401,E402  (lädt .env)
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    import aiohttp
    from services import candle_cache, candles as _c
    async with aiohttp.ClientSession() as s:
        arr = await candle_cache.get_candles(s, "BTCUSDT", 2)
    rows = _c.as_list(arr)
    if len(rows) < 600:
        print("zu wenig Kerzen", len(rows))
        return
    now = datetime.now(timezone.utc)
    docs = []
    # 12 Trades: Entry vor ~5h..3h, Dauer 20 min, Nachlauf längst vorbei
    for i in range(12):
        idx = len(rows) - 360 - i * 25
        e_c, x_c = rows[idx], rows[idx + 20]
        entry = e_c["close"]
        side = "LONG" if i % 3 else "SHORT"
        sign = 1 if side == "LONG" else -1
        risk = entry * 0.003
        exit_p = x_c["close"]
        pnl = (exit_p - entry) * sign * 0.01
        docs.append({
            "id": f"seed-pm-{i}", "symbol": "BTCUSDT", "side": side, "mode": "paper" if i % 2 else "live",
            "strategy_id": "ai_trader", "strategy_name": "KI Trader", "status": "closed",
            "entry": entry, "initial_sl": entry - sign * risk, "sl": entry - sign * risk,
            "tp1": entry + sign * risk, "tpf": entry + sign * risk * 2, "risk": risk,
            "qty": 0.01, "qty_remaining": 0, "leverage": 20, "max_capital": 50,
            "exit_price": exit_p, "realized_pnl": round(pnl, 4),
            "result": "win" if pnl > 0 else "loss",
            "setup": "breakout" if i % 2 else "pullback", "horizon": "scalp",
            "opened_at": datetime.fromtimestamp(e_c["timestamp"] / 1000, tz=timezone.utc).isoformat(),
            "closed_at": datetime.fromtimestamp(x_c["timestamp"] / 1000, tz=timezone.utc).isoformat(),
            "events": ["SEED"],
        })
    await db.auto_trades.delete_many({"id": {"$regex": "^seed-pm-"}})
    await db.auto_trades.insert_many(docs)
    lim_c = rows[len(rows) - 300]
    created = datetime.fromtimestamp(lim_c["timestamp"] / 1000, tz=timezone.utc)
    await db.ai_limit_orders.delete_many({"id": "seed-pm-limit"})
    await db.ai_limit_orders.insert_one({
        "id": "seed-pm-limit", "symbol": "BTCUSDT", "side": "LONG",
        "limit_price": lim_c["close"] * 0.995, "ref_price": lim_c["close"], "dist_pct": 0.5,
        "valid_min": 60, "created_at": created.isoformat(),
        "expires_at": (created + timedelta(minutes=60)).isoformat(),
        "closed_at": (created + timedelta(minutes=60)).isoformat(),
        "status": "expired", "setup": "pullback",
        "signal": {"type": "LONG", "entry_price": lim_c["close"] * 0.995,
                   "stop_loss": lim_c["close"] * 0.99, "take_profit_full": lim_c["close"] * 1.01},
    })
    print(f"seeded {len(docs)} trades + 1 limit order (uuid {uuid.uuid4().hex[:6]})")


asyncio.run(main())
