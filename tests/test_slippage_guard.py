"""Regressionstests Slippage-Wächter (services/slippage_guard.py) + Hebel-Deckel-Migration.

Ausführen: cd /app && python tests/test_slippage_guard.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_slippage_guard"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def test_rules():
    from services import slippage_guard as sg
    cfg = dict(sg.DEFAULTS)
    st = sg.slippage_stats([{"slippage_pct": 0.86}, {"slippage_pct": -0.1}, {"slippage_pct": 0.3}])
    assert st["n"] == 3 and abs(st["avg_pct"] - 0.3867) < 1e-3 and st["max_pct"] == 0.86
    # nur Momentum-Setups
    assert sg.block_reason("DOGEUSDT", "squeeze_breakout", st, cfg) is not None
    assert sg.block_reason("DOGEUSDT", "liquidity_sweep", st, cfg) is None
    assert sg.block_reason("DOGEUSDT", "liquidity_sweep", st, {**cfg, "slippage_guard_all_setups": True}) is not None
    # zu wenig Messwerte / unter Limit / aus
    assert sg.block_reason("X", "breakout", {"n": 2, "avg_pct": 2.0, "max_pct": 2.0}, cfg) is None
    assert sg.block_reason("X", "breakout", {"n": 5, "avg_pct": 0.25, "max_pct": 0.9}, cfg) is None
    assert sg.block_reason("X", "breakout", st, {**cfg, "slippage_guard_max_pct": 0}) is None
    assert sg.block_reason("X", "breakout", st, {**cfg, "slippage_guard_enabled": False}) is None
    # Klemmen
    c = {}
    sg.clamp_updates({"slippage_guard_max_pct": 99, "slippage_guard_min_trades": 0,
                      "slippage_guard_days": "x", "slippage_guard_enabled": 0}, c)
    assert c["slippage_guard_max_pct"] == 5.0 and c["slippage_guard_min_trades"] == 1
    assert "slippage_guard_days" not in c and c["slippage_guard_enabled"] is False
    # ai_engine kennt die Defaults
    from services.ai_engine import DEFAULT_AI_CONFIG
    assert DEFAULT_AI_CONFIG["slippage_guard_max_pct"] == 0.3
    print("  ✓ Slippage-Wächter: Regeln, Setup-Filter, Klemmen")


async def _db_tests():
    from services import slippage_guard as sg
    from services import boot_migrations as bm
    from services import position_sizing as ps
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)
    await db.auto_trades.insert_many([
        {"id": f"d{i}", "symbol": "DOGEUSDT", "mode": "live", "strategy_id": "ai_trader",
         "slippage_pct": v, "opened_at": iso(days=1)}
        for i, v in enumerate([0.86, 0.5, -0.15, 0.4])] + [
        {"id": "c1", "symbol": "DOGEUSDT", "mode": "paper", "data_collection": True,
         "slippage_pct": 3.0, "opened_at": iso(days=1)},               # Sammel-Trade zählt nicht
        {"id": "old", "symbol": "DOGEUSDT", "mode": "live", "slippage_pct": 5.0,
         "opened_at": iso(days=40)},                                      # außerhalb Fenster
        {"id": "b1", "symbol": "BTCUSDT", "mode": "live", "slippage_pct": 0.05,
         "opened_at": iso(days=1)},
    ])
    sg.invalidate()
    st = await sg.symbol_stats(db, "DOGEUSDT", sg.DEFAULTS)
    assert st["n"] == 4 and abs(st["avg_pct"] - 0.44) < 1e-3, st
    assert sg.block_reason("DOGEUSDT", "squeeze_breakout", st, sg.DEFAULTS)
    st_btc = await sg.symbol_stats(db, "BTCUSDT", sg.DEFAULTS)
    assert st_btc["n"] == 1 and sg.block_reason("BTCUSDT", "squeeze_breakout", st_btc, sg.DEFAULTS) is None
    print("  ✓ Slippage-Statistik aus echten Live-Trades (Fenster, keine Sammel-Trades)")

    # Hebel-Deckel-Migration: 50 (alter Migrations-Standard) -> 15, Trader-Wahl bleibt
    await db.settings.insert_one({"_id": "ai_trader_config", "sizing_mode": "risk", "risk_max_leverage": 50})
    await bm.migrate_leverage_cap(db, None)
    doc = await db.settings.find_one({"_id": "ai_trader_config"})
    assert doc["risk_max_leverage"] == ps.DEFAULTS["risk_max_leverage"] == 15
    await db.settings.update_one({"_id": "ai_trader_config"}, {"$set": {"risk_max_leverage": 50}})
    await bm.migrate_leverage_cap(db, None)   # idempotent
    assert (await db.settings.find_one({"_id": "ai_trader_config"}))["risk_max_leverage"] == 50
    await db.settings.delete_many({})
    await db.settings.insert_one({"_id": "ai_trader_config", "risk_max_leverage": 25})
    await bm.migrate_leverage_cap(db, None)   # eigene Wahl (25) bleibt
    assert (await db.settings.find_one({"_id": "ai_trader_config"}))["risk_max_leverage"] == 25
    print("  ✓ Boot-Migration leverage_cap_v1 (50 -> 15, idempotent, Trader-Wahl bleibt)")
    await client.drop_database(TEST_DB)


if __name__ == "__main__":
    test_rules()
    asyncio.run(_db_tests())
    print("ALLE TESTS OK")
