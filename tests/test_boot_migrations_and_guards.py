"""Tests: Boot-Migrationen (Cerebras-Aus, Heatmap-Aus, Slippage-Artefakte,
Hebel-Proposals) + Hebel-Wirksamkeit in _apply_changes + Duplikat-Guard +
Rohstoff-Stale-Limit. Ohne Netzwerk (lokale Mongo, eigene Test-DB)."""
import asyncio
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient

TEST_DB = os.environ["DB_NAME"] + "_test_bootmig"


def iso(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).isoformat()


async def main():
    from services.boot_migrations import run_boot_migrations
    from services.ai_engine import ai_engine, DEFAULT_AI_CONFIG

    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    db = client[TEST_DB]
    await client.drop_database(TEST_DB)
    old_db, old_cfg = ai_engine.db, ai_engine.config
    ai_engine.db = db
    ai_engine.config = dict(DEFAULT_AI_CONFIG)
    try:
        # ---------- Seeds ----------
        await db.settings.insert_one({"_id": "ai_roles_config",
            "analyst": {"enabled": True, "provider": "cerebras", "model": "gpt-oss-120b",
                        "fallback_provider": "openrouter",
                        "fallback_model": "nvidia/nemotron-3-super-120b-a12b:free",
                        "fallback2_provider": "openrouter",
                        "fallback2_model": "deepseek/deepseek-v4-flash",
                        "user_configured": True},
            "chat": {"enabled": True, "provider": "gemini", "model": "gemini-3.5-flash",
                     "fallback_provider": "cerebras", "fallback_model": "gemma-4-31b"},
            "learner": {"enabled": True, "provider": "openrouter",
                        "model": "deepseek/deepseek-v4-pro-0813"}})
        await db.settings.insert_one({"_id": "ai_trader_config", **DEFAULT_AI_CONFIG,
                                      "use_heatmap_data": True})
        await db.settings.insert_one({"_id": "autotrade_config", "coins": {
            "EURUSD": {"leverage": 20, "auto_leverage_enabled": True, "auto_lev_max": 50}}})
        await db.strategy_coin_configs.insert_one(
            {"_id": "ai_trader_AVAXUSDT",
             "config": {"leverage": 8, "auto_leverage_enabled": False}})
        await db.auto_trades.insert_many([
            {"id": "t1", "symbol": "DOTUSDT", "order_kind": "market",
             "slippage_pct": 4.9, "slippage_usdt": 10.0, "status": "closed"},
            {"id": "t2", "symbol": "BTCUSDT", "order_kind": "market",
             "slippage_pct": 0.5, "slippage_usdt": 0.4, "status": "closed"},
            {"id": "t3", "symbol": "ETHUSDT", "order_kind": "maker",
             "slippage_pct": 3.0, "slippage_usdt": 2.0, "status": "closed"}])
        await db.ai_proposals.insert_many([
            {"id": "p1", "status": "needs_confirmation", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 6}, "ts": iso(days=2)},
            {"id": "p2", "status": "needs_confirmation", "scope": "coin",
             "symbol": "EURUSD", "changes": {"leverage": 7}, "ts": iso(hours=3)},
            {"id": "p3", "status": "needs_confirmation", "scope": "coin",
             "symbol": "AVAXUSDT", "changes": {"leverage": 9.6}, "ts": iso(hours=2)},
            {"id": "p4", "status": "needs_confirmation", "scope": "engine",
             "changes": {"max_same_direction": 2}, "ts": iso(hours=5)},
            {"id": "p5", "status": "needs_confirmation", "scope": "engine",
             "changes": {"cooldown_min": 30}, "ts": iso(hours=5)},
            {"id": "p6", "status": "needs_confirmation", "scope": "engine",
             "changes": {"min_confidence": 70}, "ts": iso(hours=5)}])

        await run_boot_migrations(db, ai_engine)

        # ---------- 1) Cerebras-Migration ----------
        roles = await db.settings.find_one({"_id": "ai_roles_config"})
        a = roles["analyst"]
        from services.boot_migrations import ANALYST_CHAIN, CEREBRAS_REPLACEMENT
        assert a["provider"] == ANALYST_CHAIN["provider"] and a["model"] == ANALYST_CHAIN["model"]
        assert a["fallback_provider"] == ANALYST_CHAIN["fallback_provider"]
        assert a["fallback_model"] == ANALYST_CHAIN["fallback_model"]
        assert a["fallback2_model"] == ANALYST_CHAIN["fallback2_model"]
        assert roles["chat"]["fallback_provider"] == CEREBRAS_REPLACEMENT["provider"]
        assert roles["chat"]["fallback_model"] == CEREBRAS_REPLACEMENT["model"]
        assert roles["chat"]["provider"] == "gemini"  # gesunde Slots unangetastet
        assert roles["learner"]["model"] == "deepseek/deepseek-v4-pro-0813"
        print("PASS 1: tote Cerebras-Slots ersetzt, gesunde Slots unangetastet")

        # ---------- 2) Heatmap aus + idempotent ----------
        cfg = await db.settings.find_one({"_id": "ai_trader_config"})
        assert cfg["use_heatmap_data"] is False
        assert ai_engine.config["use_heatmap_data"] is False
        await db.settings.update_one({"_id": "ai_trader_config"},
                                     {"$set": {"use_heatmap_data": True}})
        await run_boot_migrations(db, ai_engine)  # 2. Lauf: Marker greift
        cfg = await db.settings.find_one({"_id": "ai_trader_config"})
        assert cfg["use_heatmap_data"] is True, "User-Wahl nach Migration muss bleiben"
        print("PASS 2: use_heatmap_data aus, 2. Lauf respektiert spätere User-Wahl")

        # ---------- 3) Slippage-Artefakte ----------
        t1 = await db.auto_trades.find_one({"id": "t1"})
        assert "slippage_pct" not in t1 and t1.get("slippage_artifact_cleared") is True
        t2 = await db.auto_trades.find_one({"id": "t2"})
        assert t2["slippage_pct"] == 0.5 and "slippage_artifact_cleared" not in t2
        t3 = await db.auto_trades.find_one({"id": "t3"})
        assert t3["slippage_pct"] == 3.0, "Maker-Messungen (Limitpreis=Fill) bleiben"
        print("PASS 3: nur Market-Artefakte >2% bereinigt, ehrliche Werte bleiben")

        # ---------- 4) Hebel-Proposals ----------
        p1 = await db.ai_proposals.find_one({"id": "p1"})
        p2 = await db.ai_proposals.find_one({"id": "p2"})
        p3 = await db.ai_proposals.find_one({"id": "p3"})
        assert p1["status"] == "superseded"
        assert p2["status"] == "applied"
        assert p3["status"] == "needs_confirmation", "Erhöhung 8->9.6 bleibt liegen"
        scc = await db.strategy_coin_configs.find_one({"_id": "ai_trader_EURUSD"})
        assert scc["config"]["leverage"] == 7
        assert scc["config"]["auto_leverage_enabled"] is False, \
            "Auto-Hebel muss mit deaktiviert werden, sonst wirkungslos"
        assert (await db.ai_proposals.find_one({"id": "p4"}))["status"] == "rejected"
        assert (await db.ai_proposals.find_one({"id": "p5"}))["status"] == "rejected"
        assert (await db.ai_proposals.find_one({"id": "p6"}))["status"] == "needs_confirmation"
        print("PASS 4: Senkung angewendet (Auto-Hebel aus), Duplikat überholt, "
              "Erhöhung + min_confidence bleiben offen, restriktive abgelehnt")

        # ---------- 5) _apply_changes-Regel direkt ----------
        await ai_engine._apply_changes("coin", "BTCUSDT", {"leverage": 5})
        scc = await db.strategy_coin_configs.find_one({"_id": "ai_trader_BTCUSDT"})
        assert scc["config"]["leverage"] == 5
        assert scc["config"]["auto_leverage_enabled"] is False
        await ai_engine._apply_changes("coin", "BTCUSDT",
                                       {"leverage": 4, "auto_leverage_enabled": True})
        scc = await db.strategy_coin_configs.find_one({"_id": "ai_trader_BTCUSDT"})
        assert scc["config"]["auto_leverage_enabled"] is True, \
            "explizit mitgeschicktes Flag wird respektiert"
        print("PASS 5: Hebel-Apply deaktiviert Auto-Hebel, explizites Flag respektiert")

        # ---------- 6) Duplikat-Guard ----------
        await db.auto_trades.insert_one({"id": "o1", "status": "open",
            "strategy_id": "ai_trader", "symbol": "BTCUSDT", "side": "LONG",
            "opened_at": iso(minutes=10)})
        why = await ai_engine._dup_entry_block("BTCUSDT", "LONG")
        assert why and "Duplikat-Guard" in why
        assert await ai_engine._dup_entry_block("BTCUSDT", "SHORT") is None
        assert await ai_engine._dup_entry_block("ETHUSDT", "LONG") is None
        await db.auto_trades.update_one({"id": "o1"},
                                        {"$set": {"opened_at": iso(hours=2)}})
        assert await ai_engine._dup_entry_block("BTCUSDT", "LONG") is None, \
            "alter offener Trade blockt nicht"
        ai_engine.config["dup_entry_window_min"] = 0
        await db.auto_trades.update_one({"id": "o1"},
                                        {"$set": {"opened_at": iso(minutes=1)}})
        assert await ai_engine._dup_entry_block("BTCUSDT", "LONG") is None, "0 = aus"
        ai_engine.config["dup_entry_window_min"] = 30
        print("PASS 6: Duplikat-Guard (Fenster, Richtung, Symbol, 0=aus)")

        # ---------- 7) Rohstoff-Stale-Limit ----------
        assert ai_engine._stale_limit_for("GOLD") == 20
        assert ai_engine._stale_limit_for("SILVER") == 20
        assert ai_engine._stale_limit_for("BTCUSDT") == 10
        ai_engine.config["stale_price_max_min_commodity"] = 30
        assert ai_engine._stale_limit_for("OIL") == 30
        ai_engine.config["stale_price_max_min"] = 5
        assert ai_engine._stale_limit_for("EURUSD") == 5, "Forex nutzt Standard-Limit"
        print("PASS 7: Rohstoffe eigenes Stale-Limit (Default 20), Rest Standard")

        print("ALLE TESTS GRÜN (7/7)")
    finally:
        ai_engine.db, ai_engine.config = old_db, old_cfg
        await client.drop_database(TEST_DB)


def test_main():
    """T1: Skript-Asserts gekapselt - pytest-kompatibel, laeuft NICHT mehr beim Import."""
    asyncio.run(main())


if __name__ == "__main__":
    asyncio.run(main())
