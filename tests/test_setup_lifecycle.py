"""Regressionstests Setup-Lebenszyklus (services/setup_lifecycle.py + ai_playbook).

Ausführen: cd /app && python tests/test_setup_lifecycle.py
"""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_setup_lifecycle"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def test_promotion_rules():
    from services import setup_lifecycle as lc
    assert lc.promotion_ok(None)[0] is False
    assert lc.promotion_ok({"trades": 4, "wins": 4, "pnl": 10})[0] is False       # < 5 Trades
    assert lc.promotion_ok({"trades": 5, "wins": 1, "pnl": 0.5})[0] is True       # PnL > 0
    assert lc.promotion_ok({"trades": 5, "wins": 3, "pnl": -1.0})[0] is True      # WR 60 %
    assert lc.promotion_ok({"trades": 6, "wins": 2, "pnl": -1.0})[0] is False     # beides schlecht
    # ai_playbook.live_ready nutzt dieselbe Regel (+ 'schwach' bleibt gesperrt)
    from services import ai_playbook as pb
    assert pb.live_ready({"trades": 5, "wins": 1, "pnl": 0.5, "verdict": "neutral"})[0] is True
    assert pb.live_ready({"trades": 5, "wins": 1, "pnl": -0.5, "verdict": "neutral"})[0] is False
    assert pb.live_ready({"trades": 9, "wins": 2, "pnl": -5, "verdict": "schwach"})[0] is False
    print("  ✓ Freischaltung: ≥5 Trades & (PnL>0 oder WR≥55 %)")


def test_demotion_rules():
    from services import setup_lifecycle as lc
    assert lc.demotion_reason({"trades": 7, "wins": 0, "pnl": -50, "margin": 100}) is None   # < 8
    assert lc.demotion_reason({"trades": 8, "wins": 2, "pnl": -1, "margin": 100}) is not None  # WR 25 %
    assert lc.demotion_reason({"trades": 8, "wins": 4, "pnl": -4, "margin": 100}) is not None  # -4 %
    assert lc.demotion_reason({"trades": 8, "wins": 4, "pnl": -2, "margin": 100}) is None      # -2 %, WR 50
    assert lc.demotion_reason({"trades": 10, "wins": 6, "pnl": 3, "margin": 100}) is None
    # ohne Margin-Daten: alter Maßstab
    assert lc.demotion_reason({"trades": 15, "wins": 1, "pnl": -21.07}) is not None
    assert lc.demotion_reason({"trades": 8, "wins": 4, "pnl": -1}) is None
    print("  ✓ Rückstufung: ≥8 Live-Trades & (PnL ≤ -3 % Margin oder WR < 35 %)")


def _trade(i, pnl, sl_pct=0.4, tp_ratio=2.0, lev=10, tf="5m", days=1.0):
    entry = 100.0
    sl = entry * (1 - sl_pct / 100)
    return {"setup": "x", "entry": entry, "sl": sl, "initial_sl": sl,
            "tpf": entry + (entry - sl) * tp_ratio, "leverage": lev, "timeframe": tf,
            "realized_pnl": pnl, "opened_at": iso(days=days, minutes=-i)}


def test_profile_and_versions():
    from services import setup_lifecycle as lc
    trades = [_trade(i, 1.0 if i % 2 else -0.5) for i in range(10)]
    prof = lc.profile_from_trades(trades)
    assert prof and abs(prof["sl_pct"] - 0.4) < 1e-6 and abs(prof["tp_ratio"] - 2.0) < 1e-6
    assert prof["timeframe"] == "5m" and prof["max_leverage"] == 10
    assert lc.profile_from_trades(trades[:5]) is None   # zu wenig Daten
    # Tuning-Schritt begrenzt (±20 %) – Anti-Overfitting
    assert lc.clamp_step(1.0, 0.5) == 0.6 and lc.clamp_step(0.1, 0.5) == 0.4
    assert lc.clamp_step(0.55, 0.5) == 0.55
    # v1 anlegen
    entry, ev = lc.evolve_versions({}, trades, now_iso=iso(days=0.9))
    assert entry["active"] == 1 and "v1" in ev
    # Neue Trades mit deutlich anderem Profil (SL 0.8) -> Tuning, aber nur bis 0.48
    newer = [_trade(i, 1.0, sl_pct=0.8, tp_ratio=3.0, days=0.5) for i in range(10)]
    entry, ev = lc.evolve_versions(entry, trades + newer, now_iso=iso(days=0.4))
    assert entry["active"] == 2 and "Profil v2" in ev
    assert abs(entry["versions"][-1]["params"]["sl_pct"] - 0.48) < 1e-6
    assert abs(entry["versions"][-1]["params"]["tp_ratio"] - 2.4) < 1e-6
    # v2 läuft ins Minus (6 Verlierer) -> Rollback auf v1
    losers = [_trade(i, -2.0, sl_pct=0.5, days=0.3) for i in range(6)]
    entry, ev = lc.evolve_versions(entry, trades + newer + losers, now_iso=iso(days=0.1))
    assert entry["active"] == 3 and "Rollback" in ev and entry["versions"][-1]["rollback_of"] == 1
    assert entry["versions"][-1]["params"] == entry["versions"][0]["params"]
    # Stabil: kein Ereignis ohne neue Daten
    entry2, ev = lc.evolve_versions(entry, trades + newer + losers)
    assert ev is None and entry2["active"] == 3
    assert lc.context_lines({"x": entry2})[1].startswith("- x: Profil v3")
    print("  ✓ Profil-Versionen: initial, Tuning (±20 %), Auto-Rollback")


async def _db_tests():
    from services import ai_playbook as pb
    from services import setup_lifecycle as lc
    client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)
    rows = []
    for i in range(20):    # Paper-Sammlung gut
        rows.append({"id": f"c{i}", "strategy_id": "ai_trader", "status": "closed",
                     "mode": "paper", "data_collection": True, "setup": "breakout",
                     "realized_pnl": 1.5 if i % 4 else -0.8, "opened_at": iso(days=3)})
    for i in range(10):    # Live schlecht: 1/10, -3.5 % der Margin
        rows.append({"id": f"l{i}", "strategy_id": "ai_trader", "status": "closed",
                     "mode": "live", "setup": "breakout", "margin_used": 100.0,
                     "realized_pnl": 1.0 if i == 0 else -4.0, "opened_at": iso(days=2)})
    await db.auto_trades.insert_many(rows)
    data = await pb.refresh(db)
    assert "breakout" in data["live_blocked"], data["live_blocked"]
    assert data["live_ready"]["breakout"] is False
    assert data["live_blocked"]["breakout"]["paper_since"]["trades"] == 0
    st = await pb.status(db)
    row = {r["setup"]: r for r in st["maturity"]}["breakout"]
    assert row["phase"] == "rückgestuft" and row["paper_since_demotion"] == 0
    assert st["rules"]["promote_min_trades"] == lc.MIN_TRADES_PROMOTE
    print("  ✓ Rückstufung live->paper (10 Live-Trades, WR 10 %)")
    # 5 gute Paper-Trades SEIT Rückstufung -> wieder live (ohne Re-Test-Datum)
    await db.auto_trades.insert_many([
        {"id": f"p{i}", "strategy_id": "ai_trader", "status": "closed", "mode": "paper",
         "data_collection": True, "setup": "breakout", "realized_pnl": 2.0,
         "opened_at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}
        for i in range(5)])
    data = await pb.refresh(db)
    assert "breakout" not in data["live_blocked"]
    assert data["live_ready"]["breakout"] is True
    assert data["live_since"].get("breakout")
    # alte Live-Verluste vor der Freischaltung zählen nicht mehr
    assert data["live_stats"].get("breakout", {}).get("trades", 0) == 0
    feed = await db.ai_chat.find({"role": "playbook", "setup": "breakout"}).to_list(20)
    assert any("wieder LIVE" in f["text"] for f in feed)
    assert sum(1 for f in feed if "LIVE-freigeschaltet" in f["text"]) == 1
    print("  ✓ Wieder-Freischaltung nach 5 guten Paper-Trades seit Rückstufung")
    await client.drop_database(TEST_DB)


if __name__ == "__main__":
    test_promotion_rules()
    test_demotion_rules()
    test_profile_and_versions()
    asyncio.run(_db_tests())
    print("ALLE TESTS OK")
