"""Tests: Manuell-Trade-Sync (Hebel-Setting vs. effektiver Hebel), PnL-%-Basis
wie Bitunix, Boot-Migration >200x, Momentum-News-Bremse, trade_bound_margin.
Ohne Netzwerk (lokale Mongo, eigene Test-DB)."""
import asyncio
import math
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_manualsync"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


async def main():
    from core.utils import _enrich_trade
    from core.state import autotrader
    from services.boot_migrations import migrate_manual_lev_display
    from services.ai_engine import ai_engine, DEFAULT_AI_CONFIG

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)

    # ---------- 1) PnL-%-Basis wie Bitunix (Bug: +335,81% statt ~+158%) ----------
    t = {"id": "m1", "status": "closed", "side": "LONG", "entry": 78784.9,
         "qty": 0.0479, "qty_remaining": 0, "leverage": 200, "margin_used": 9.076,
         "realized_pnl": 30.478183, "exit_price": 79435.6, "fee_percent": 0.06,
         "opened_at": iso(days=1), "closed_at": iso(hours=1)}
    c = _enrich_trade(dict(t))["computed"]
    # Initial-Marge = 78784.9*0.0479/200 = 18.87 -> 30.478/18.87 = ~161.5%
    assert 150 < c["pnl_pct_margin"] < 175, f"erwartet ~161%, war {c['pnl_pct_margin']}"
    assert c["pnl_pct_margin"] < 300, "aufgeblähte Marge-Basis (9.076) darf nicht greifen"
    # Normaler Paper-Trade: margin_used == Notional/Hebel -> Wert unverändert
    t2 = {"id": "m2", "status": "closed", "side": "LONG", "entry": 100.0,
          "qty": 10.0, "qty_remaining": 0, "leverage": 10, "margin_used": 100.0,
          "realized_pnl": 10.0, "exit_price": 101.5, "fee_percent": 0.06,
          "opened_at": iso(days=1), "closed_at": iso(hours=1)}
    c2 = _enrich_trade(dict(t2))["computed"]
    assert math.isclose(c2["pnl_pct_margin"], 10.0, abs_tol=0.01)
    # Offener Trade: Basis bleibt die aktuell gebundene Marge (Live-Anzeige)
    t3 = {"id": "m3", "status": "open", "side": "LONG", "entry": 100.0,
          "qty": 1.0, "qty_remaining": 1.0, "leverage": 10, "margin_used": 5.0,
          "realized_pnl": 0.0, "fee_percent": 0.0, "opened_at": iso(hours=2)}
    c3 = _enrich_trade(dict(t3), current_price=101.0)["computed"]
    assert math.isclose(c3["upnl_pct_margin"], 20.0, abs_tol=0.2), c3["upnl_pct_margin"]
    print("PASS 1: PnL-%-Basis – geschlossen wie Bitunix-History (Initial-Marge), "
          "offen wie Live-Anzeige, normale Trades unverändert")

    # ---------- 2) Sync speichert Hebel-SETTING + effektiven Hebel ----------
    old_db = autotrader.db
    autotrader.db = db
    try:
        local = {"id": "s1", "symbol": "BTCUSDT", "side": "LONG", "status": "open",
                 "mode": "live", "entry": 78784.9, "qty": 0.0479,
                 "qty_remaining": 0.0384, "leverage": 39.05, "margin_used": 77.47,
                 "fee_percent": 0.06, "events": []}
        await db.auto_trades.insert_one(dict(local))
        pos = {"qty": 0.0384, "margin": 9.076, "leverage": 200}
        changes = await autotrader.sync_position_state(local, pos)
        assert changes and "effektiv" in changes[-1], changes
        doc = await db.auto_trades.find_one({"id": "s1"})
        assert doc["leverage"] == 200.0, f"Anzeige-Hebel muss Setting sein: {doc['leverage']}"
        assert 330 < doc["effective_leverage"] < 336, doc["effective_leverage"]
        assert math.isclose(doc["margin_used"], 9.076, abs_tol=0.001)
        # trade_bound_margin nutzt den effektiven Hebel -> reale Marge
        bm = autotrader.trade_bound_margin(doc)
        assert math.isclose(bm, 9.076, rel_tol=0.01), f"gebundene Marge falsch: {bm}"
        # Ohne Börsen-Setting (leverage 0): Fallback effektiver Hebel als Anzeige
        local2 = {"id": "s2", "symbol": "ETHUSDT", "side": "SHORT", "status": "open",
                  "mode": "live", "entry": 2000.0, "qty": 1.0, "qty_remaining": 1.0,
                  "leverage": 10, "margin_used": 200.0, "fee_percent": 0.06, "events": []}
        await db.auto_trades.insert_one(dict(local2))
        await autotrader.sync_position_state(local2, {"qty": 1.0, "margin": 100.0,
                                                      "leverage": 0})
        doc2 = await db.auto_trades.find_one({"id": "s2"})
        assert doc2["leverage"] == 20.0 and doc2["effective_leverage"] == 20.0
        print("PASS 2: Sync – Hebel-Setting (200x) angezeigt, effektiver Hebel "
              "(333x) separat, gebundene Marge korrekt")

        # ---------- 3) Boot-Migration: unmögliche Alt-Hebel > 200x ----------
        await db.auto_trades.insert_many([
            {"id": "h1", "mode": "live", "leverage": 333.33, "status": "closed"},
            {"id": "h2", "mode": "live", "leverage": 150.0, "status": "closed"},
            {"id": "h3", "mode": "paper", "leverage": 250.0, "status": "closed"}])
        await migrate_manual_lev_display(db)
        h1 = await db.auto_trades.find_one({"id": "h1"})
        assert h1["leverage"] == 200 and h1["effective_leverage"] == 333.33
        assert (await db.auto_trades.find_one({"id": "h2"}))["leverage"] == 150.0
        assert (await db.auto_trades.find_one({"id": "h3"}))["leverage"] == 250.0, \
            "Paper-Trades sind nie von der Börse gesynct – unangetastet"
        # idempotent
        await migrate_manual_lev_display(db)
        assert (await db.auto_trades.find_one({"id": "h1"}))["leverage"] == 200
        print("PASS 3: Migration deckelt nur unmögliche Live-Hebel (>200x), idempotent")
    finally:
        autotrader.db = old_db

    # ---------- 4) Momentum-News-Bremse ----------
    old_cfg, old_aidb = ai_engine.config, ai_engine.db
    ai_engine.config = dict(DEFAULT_AI_CONFIG)
    ai_engine.db = db  # update_config persistiert – nicht in die Dev-DB schreiben
    try:
        blk = ai_engine._momentum_brake_block(
            {"setup": "momentum_news", "confidence": 72})
        assert blk and "Momentum-News-Bremse" in blk and "70-74" in blk
        assert ai_engine._momentum_brake_block(
            {"setup": "momentum_news", "confidence": 69}) is None
        assert ai_engine._momentum_brake_block(
            {"setup": "momentum_news", "confidence": 75}) is None
        assert ai_engine._momentum_brake_block(
            {"setup": "squeeze_breakout", "confidence": 72}) is None
        ai_engine.config["momentum_news_conf_block"] = []
        assert ai_engine._momentum_brake_block(
            {"setup": "momentum_news", "confidence": 72}) is None, "[] = aus"
        # Klemmen-Handler
        ai_engine.config = dict(DEFAULT_AI_CONFIG)
        await ai_engine.update_config({"momentum_news_conf_block": [65, 200]})
        assert ai_engine.config["momentum_news_conf_block"] == [65, 100]
        await ai_engine.update_config({"momentum_news_conf_block": []})
        assert ai_engine.config["momentum_news_conf_block"] == []
        await ai_engine.update_config({"momentum_news_conf_block": [80, 70]})
        assert ai_engine.config["momentum_news_conf_block"] == [], "lo>hi ignoriert"
        print("PASS 4: Momentum-News-Bremse (Band 70-74, nur momentum_news, "
              "abschaltbar, Klemmen ok)")
    finally:
        ai_engine.config, ai_engine.db = old_cfg, old_aidb

    await client.drop_database(TEST_DB)
    print("ALLE TESTS GRÜN (4/4)")


asyncio.run(main())
