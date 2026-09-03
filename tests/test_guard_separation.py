"""Tests: Getrennte Welten für Guards (Richtungs-/Korrelations-/Cluster-Guard,
MasterPrompt-Zähler, Tagesrisiko) + Proposal-Aufräumer (supersede bei Insert).
Ohne Netzwerk (lokale Mongo, eigene Test-DB)."""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_guardsep"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


def trade(sym, side, dc, entry=100.0, **kw):
    return {"id": f"{sym}-{side}-{dc}-{entry}", "status": "open",
            "strategy_id": "ai_trader", "symbol": sym, "side": side,
            "entry": entry, "data_collection": dc,
            "opened_at": iso(hours=2), **kw}


async def main():
    from services.ai_engine import ai_engine, DEFAULT_AI_CONFIG

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)
    old = (ai_engine.db, ai_engine.config, ai_engine.scanner)
    ai_engine.db = db
    ai_engine.config = dict(DEFAULT_AI_CONFIG)  # max_same=3, collection_max=5
    ai_engine.scanner = None
    try:
        # ---------- 1) Bug-Repro: Sammel-LONGs blockieren Live nicht mehr ----------
        # 10 offene Sammel-LONGs (unkorrelierte Symbole), KEIN Risiko-Trade offen.
        syms = ["AVAXUSDT", "BNBUSDT", "DOGEUSDT", "DOTUSDT", "EURUSD",
                "ADAUSDT", "XRPUSDT", "GOLD", "SILVER", "USDJPY"]
        await db.auto_trades.insert_many([trade(s, "LONG", True) for s in syms])
        dec = {"symbol": "BTCUSDT", "action": "LONG", "price": 50000.0, "setup": "trend"}
        allowed, why = await ai_engine._diversification_gate("BTCUSDT", dec, collection=False)
        assert allowed, f"Live-Entry darf nicht von Sammel-Trades geblockt werden: {why}"
        print("PASS 1: 10 offene Sammel-LONGs blocken Live-Einstieg NICHT mehr")

        # ---------- 2) Live-Limit greift weiter (nur echte Risiko-Trades) ----------
        await db.auto_trades.insert_many([
            trade("ADAUSDT", "LONG", False), trade("EURUSD", "LONG", False),
            trade("XRPUSDT", "LONG", False)])
        allowed, why = await ai_engine._diversification_gate("BTCUSDT", dec, collection=False)
        assert not allowed and "Richtungs-Guard" in why and "Limit 3" in why
        # Gegenrichtung bleibt erlaubt (Hedge)
        allowed, _ = await ai_engine._diversification_gate(
            "BTCUSDT", {**dec, "action": "SHORT"}, collection=False)
        assert allowed
        print("PASS 2: Live-Richtungs-Guard zählt nur echte Risiko-Trades (Limit 3)")

        # ---------- 3) Sammel-Welt: eigenes Limit (collection_max_same_direction=5) ----------
        allowed, why = await ai_engine._diversification_gate("BTCUSDT", dec, collection=True)
        assert not allowed and "Richtungs-Guard" in why and "Limit 5" in why, \
            f"10 Sammel-LONGs >= Limit 5 muss blocken: {why}"
        await db.auto_trades.delete_many({"data_collection": True,
                                          "symbol": {"$nin": syms[:4]}})
        allowed, why = await ai_engine._diversification_gate("BTCUSDT", dec, collection=True)
        assert allowed, f"4 Sammel-LONGs < Limit 5 muss erlaubt sein: {why}"
        print("PASS 3: Sammel-Richtungs-Guard nutzt collection_max_same_direction "
              "(vorher tote Einstellung) und zählt nur Sammel-Trades")

        # ---------- 4) Cluster-Guard getrennt ----------
        await db.auto_trades.delete_many({})
        await db.auto_trades.insert_one(trade("BTCUSDT", "LONG", True, entry=50000.0))
        allowed, why = await ai_engine._diversification_gate(
            "BTCUSDT", {**dec, "price": 50010.0}, collection=False)
        assert allowed, f"Sammel-Entry in gleicher Zone darf Live nicht clustern: {why}"
        await db.auto_trades.insert_one(trade("BTCUSDT", "LONG", False, entry=50005.0))
        allowed, why = await ai_engine._diversification_gate(
            "BTCUSDT", {**dec, "price": 50010.0}, collection=False)
        assert not allowed and "Cluster" in why or not allowed, \
            f"Risiko-Entry in gleicher Zone muss clustern: {why}"
        print("PASS 4: Cluster-Guard prüft je Welt getrennt")

        # ---------- 5) MasterPrompt-Zähler je Welt ----------
        await db.auto_trades.delete_many({})
        await db.auto_trades.insert_many(
            [trade(s, "LONG", True) for s in ("ADAUSDT", "XRPUSDT")]
            + [trade("EURUSD", "SHORT", False)])
        assert await ai_engine._open_ai_trades_count(collection=False) == 1
        assert await ai_engine._open_ai_trades_count(collection=True) == 2
        print("PASS 5: _open_ai_trades_count trennt Sammel- und Risiko-Welt")

        # ---------- 6) Tagesrisiko ohne Sammel-Trades ----------
        ai_engine.scanner = SimpleNamespace(berlin_date=lambda: "2026-08-26")
        await db.auto_trades.insert_many([
            {"strategy_id": "ai_trader", "trade_date": "2026-08-26",
             "realized_pnl": -50.0, "status": "closed", "data_collection": True},
            {"strategy_id": "ai_trader", "trade_date": "2026-08-26",
             "realized_pnl": -2.0, "status": "closed"}])
        pnl, n = await ai_engine._today_risk()
        assert pnl == -2.0 and n == 1, f"Sammel-PnL darf Tageslimit nicht verbrauchen: {pnl}/{n}"
        print("PASS 6: Tagesrisiko (max_daily_loss/max_trades) zählt nur echte Trades")

        # ---------- 7) Proposal-Aufräumer bei Insert ----------
        await db.ai_proposals.insert_many([
            {"id": "a1", "status": "needs_confirmation", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 6}, "ts": iso(days=1)},
            {"id": "a2", "status": "needs_confirmation", "scope": "coin",
             "symbol": "EURUSD", "changes": {"sl_pct": 1.0}, "ts": iso(days=1)},
            {"id": "a3", "status": "needs_confirmation", "scope": "coin",
             "symbol": "DOTUSDT", "changes": {"leverage": 5}, "ts": iso(days=1)},
            {"id": "a4", "status": "rejected", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 9}, "ts": iso(days=3)}])
        await ai_engine._insert_proposal(
            {"id": "n1", "status": "needs_confirmation", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 7}, "ts": iso()})
        assert (await db.ai_proposals.find_one({"id": "a1"}))["status"] == "superseded"
        assert (await db.ai_proposals.find_one({"id": "a2"}))["status"] == "needs_confirmation"
        assert (await db.ai_proposals.find_one({"id": "a3"}))["status"] == "needs_confirmation"
        assert (await db.ai_proposals.find_one({"id": "a4"}))["status"] == "rejected"
        assert (await db.ai_proposals.find_one({"id": "n1"}))["status"] == "needs_confirmation"
        # blockierter neuer Vorschlag verdrängt nichts
        await ai_engine._insert_proposal(
            {"id": "n2", "status": "blocked_master", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 8}, "ts": iso()})
        assert (await db.ai_proposals.find_one({"id": "n1"}))["status"] == "needs_confirmation"
        print("PASS 7: Aufräumer ersetzt nur gleiche offene Änderungs-Keys; "
              "blockierte Vorschläge verdrängen nichts")

        print("ALLE TESTS GRÜN (7/7)")
    finally:
        ai_engine.db, ai_engine.config, ai_engine.scanner = old
        await client.drop_database(TEST_DB)


asyncio.run(main())
