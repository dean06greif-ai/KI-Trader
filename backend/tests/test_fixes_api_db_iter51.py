"""API-/DB-Level-Regressionstests (Iter 51) fuer die 06/2026-Verbesserungen.

Ergaenzt tests/test_fixes_lev_collection_tp1_activity.py um:
- /api/health erreichbar (Backend-Smoke nach Refactoring)
- used_margin ignoriert data_collection=True in Mongo
- sync_position_state schaltet TP1-Hit + Break-Even bei externem Partial-Close
- revise_setup persistiert trade_target und loescht das inactive-Flag

Keine Bitunix-/LLM-Keys noetig: externe Calls werden lokal gepatcht.
Testdaten werden nach jedem Test wieder entfernt (symbol TESTUSDT).
"""
import asyncio
import os
import uuid

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

from services import ai_playbook
from services.bitunix_trade import breakeven_price
from core import state

# Lokale Instanz nutzt localhost (kein Preview-Proxy noetig -> stabile Tests).
BASE_URL = os.environ.get("BACKEND_INTERNAL_URL", "http://localhost:8001").rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "crypto_scanner")


def _run(factory):
    """factory: callable(db) -> coroutine. Erzeugt Motor-Client in NEUER Loop
    (Motor koppelt intern an die Loop des Erstellungs-Zeitpunkts)."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        client = AsyncIOMotorClient(MONGO_URL)
        database = client[DB_NAME]
        state.autotrader.db = database
        return loop.run_until_complete(factory(database))
    finally:
        loop.close()


# ---------- 1) Backend Smoke ----------
class TestHealth:
    def test_health_alive(self):
        last = None
        for _ in range(3):
            try:
                r = requests.get(f"{BASE_URL}/api/health", timeout=15)
                assert r.status_code == 200
                assert r.json().get("status") == "alive"
                return
            except Exception as e:  # noqa: BLE001
                last = e
        raise AssertionError(f"/api/health nicht erreichbar: {last}")


# ---------- 2) used_margin ignoriert Datensammel-Trades ----------
class TestUsedMarginIgnoresCollection:
    def test_collection_trade_not_counted(self):
        async def _do(db):
            col = db.auto_trades
            tag = f"TEST_{uuid.uuid4().hex[:8]}"
            normal = {
                "id": f"{tag}_normal", "symbol": "TESTUSDT", "mode": "paper",
                "status": "open", "side": "LONG", "entry": 100.0, "sl": 99.0,
                "tp1": 102.0, "qty": 1.0, "qty_remaining": 1.0,
                "leverage": 10.0, "max_capital": 50.0,
                "data_collection": False, "_test_tag": tag,
            }
            collection = {
                "id": f"{tag}_collect", "symbol": "TESTUSDT", "mode": "paper",
                "status": "open", "side": "LONG", "entry": 100.0, "sl": 99.0,
                "tp1": 102.0, "qty": 1.0, "qty_remaining": 1.0,
                "leverage": 10.0, "max_capital": 100.0,
                "data_collection": True, "_test_tag": tag,
            }
            try:
                # Baseline zuerst (ohne unsere Docs)
                baseline = await state.autotrader.used_margin("paper")
                await col.insert_many([normal, collection])
                used = await state.autotrader.used_margin("paper")
                delta = round(used - baseline, 6)
                # trade_bound_margin: rem*entry/lev = 1*100/10 = 10 USDT
                assert 5.0 < delta < 20.0, (
                    f"unerwartetes Delta {delta} (used={used}, base={baseline}) - "
                    f"Datensammel-Trade darf NICHT gezaehlt werden")
            finally:
                await col.delete_many({"_test_tag": tag})
        _run(_do)


# ---------- 3) sync_position_state: TP1 extern + Break-Even ----------
class TestSyncPositionTP1External:
    def test_external_partial_close_marks_tp1_and_moves_to_breakeven(self, monkeypatch):
        async def _do(db):
            col = db.auto_trades
            tid = f"TEST_sync_{uuid.uuid4().hex[:8]}"
            trade = {
                "id": tid, "symbol": "TESTUSDT", "mode": "live", "status": "open",
                "side": "LONG", "entry": 100.0, "sl": 98.0, "tp1": 102.0,
                "qty": 1.0, "qty_remaining": 1.0, "tp1_close_percent": 50,
                "tp1_hit": False, "breakeven_moved": False, "be_mode": "tp1",
                "tp1_exchange_placed": True, "fee_percent": 0.06,
                "leverage": 10.0, "max_capital": 10.0, "events": [],
                "_test_tag": tid,
            }
            await col.insert_one(trade)
            try:
                at = state.autotrader

                async def fake_mark(symbol):
                    return 102.1

                async def fake_move_sl(t, new_sl, qty_rem):
                    return True

                monkeypatch.setattr(at, "_current_mark", fake_mark)
                monkeypatch.setattr(at, "_live_move_sl", fake_move_sl)

                local = await col.find_one({"id": tid})
                changes = await at.sync_position_state(
                    local, {"qty": 0.5, "margin": 0, "leverage": 0})
                joined = " || ".join(changes)
                assert "TP1 extern" in joined, joined
                assert "Break-Even" in joined, joined

                after = await col.find_one({"id": tid})
                assert after.get("tp1_hit") is True
                assert after.get("breakeven_moved") is True
                be = breakeven_price(100.0, "LONG", 0.06)
                assert abs(float(after["sl"]) - be) < 1e-6
            finally:
                await col.delete_many({"_test_tag": tid})
        _run(_do)


# ---------- 4) revise_setup persistiert trade_target + entfernt inactive ----------
class TestReviseSetupInactive:
    def test_revise_setup_clears_inactive_and_stores_trade_target(self):
        async def _do(db):
            settings = db.settings
            original = await settings.find_one({"_id": ai_playbook.STATE_ID})
            try:
                asset_class = "crypto"
                sid = "breakout"
                await settings.update_one(
                    {"_id": ai_playbook.STATE_ID},
                    {"$set": {f"classes.{asset_class}": {
                        "live_blocked": {},
                        "inactive": {sid: {"reason": "nur 0 Trades (0.0/Woche vs. Ziel ~5)"}},
                        "revisions": {},
                        "eval_since": {},
                    }}}, upsert=True)

                res = await ai_playbook.revise_setup(
                    db, asset_class, sid,
                    desc="TEST-Revision: nach 3 Tagen Inaktivitaet nun engerer SL und Vol-Filter aktiv.",
                    reason="pytest",
                    source="test",
                    trade_target=12)
                assert res.get("status") == "ok", res

                doc = await settings.find_one({"_id": ai_playbook.STATE_ID})
                scope = (doc.get("classes") or {}).get(asset_class) or {}
                assert sid not in (scope.get("inactive") or {}), "inactive-Flag nicht geloescht"
                rev = (scope.get("revisions") or {}).get(sid) or {}
                assert rev.get("trade_target") == 12
                assert sid in (scope.get("live_blocked") or {})
            finally:
                if original is not None:
                    await settings.replace_one({"_id": ai_playbook.STATE_ID}, original)
                else:
                    await settings.delete_one({"_id": ai_playbook.STATE_ID})
        _run(_do)
