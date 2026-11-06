"""Durchspiel-Test: Detektor-Signal -> KI-Prüfung (Prüfer-Rolle) -> Einstiegs-Signal.

Läuft den ECHTEN AIEngine.run_analysis-Pfad gegen eine lokale Wegwerf-DB;
nur Marktdaten-Snapshot, Prompt-Zusatzblöcke, LLM-Antwort und die Order-
Übergabe (_emit_signal) sind ersetzt. Kein Netz, kein Geld.
"""
import asyncio
import json
import os
from datetime import datetime, timezone

import pytest
from motor.motor_asyncio import AsyncIOMotorClient

from services import ai_engine as eng_mod
from services.signal_broker import signal_broker

SYM = "BTCUSDT"


class _Scanner:
    candle_buffer = {SYM: [{"close": 100.0}] * 80}

    def berlin_now(self):
        return datetime.now(timezone.utc)

    def berlin_date(self):
        return datetime.now(timezone.utc).date().isoformat()

    def is_trading_session(self, _sid):
        return True

    def get_current_session(self):
        return "test"


def _engine(monkeypatch, db, decisions, cfg):
    eng = eng_mod.AIEngine()
    eng.db, eng.scanner, eng.symbols = db, _Scanner(), [SYM]
    eng.config.update(cfg)
    emitted = []

    async def _gen(role, prompt, system, temperature=0.4, json_mode=True):
        return json.dumps({"market_overview": "test", "decisions": decisions}), "openrouter", "openai/gpt-6-luna-pro"

    async def _emit(dec, collection=False):
        emitted.append({"dec": dict(dec), "collection": collection})
        return True

    async def _none(*_a, **_k):
        return None

    async def _empty(*_a, **_k):
        return ""

    async def _same(base, *_a, **_k):
        return base

    monkeypatch.setattr(eng_mod.AIEngine, "key", property(lambda self: "test-key"))
    monkeypatch.setattr(eng, "generate_for_role", _gen)
    monkeypatch.setattr(eng, "_emit_signal", _emit)
    monkeypatch.setattr(eng, "_setup_live_gate", _none)
    monkeypatch.setattr(eng, "_snapshot", lambda s: {"text": f"{s} price 100", "price": 100.0, "rsi": 50})
    monkeypatch.setattr(eng, "_analysis_extra_blocks", _empty)
    monkeypatch.setattr(eng, "_liquidity_block", _empty)
    monkeypatch.setattr(eng, "_user_directives", _empty)
    monkeypatch.setattr(eng, "_open_trades_text", _empty)
    monkeypatch.setattr(eng, "resolve_group_blocks", _same)
    monkeypatch.setattr(eng_mod.market_hours, "is_weekend_closed", lambda s: (False, ""))
    return eng, emitted


TEST_DB = "ki_flow_test"


async def _with_db(fn):
    """Eigene lokale Test-DB; räumt nur die eigenen Test-Dokumente auf."""
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[TEST_DB]
    try:
        return await fn(db)
    finally:
        for col in ("ai_decisions", "ai_signal_windows"):
            await db[col].delete_many({"symbol": SYM})
        await db.strategy_coin_configs.delete_one({"_id": f"ai_trader_{SYM}"})


async def _fresh_broker(db):
    await db.strategy_coin_configs.replace_one(
        {"_id": f"ai_trader_{SYM}"}, {"config": {"mode": "paper"}}, upsert=True)
    signal_broker.setup(db)
    signal_broker.signals, signal_broker._loaded = [], True


def _cfg():
    return {"ai_role": "pruefer", "signal_broker_enabled": True, "min_confidence": 50,
            "collection_enabled": False, "group_analysis": False}


def test_pruefer_flow_signal_review_to_entry(monkeypatch):
    async def body(db):
        await _fresh_broker(db)
        sig = await signal_broker.register(SYM, "crypto", {
            "setup": "pullback", "side": "LONG", "entry": 100.0, "sl": 98.5, "tp1": 101.5,
            "tpf": 103.0, "note": "Test-Detektor", "has_edge": True}, _cfg())
        dec = {"symbol": SYM, "action": "LONG", "confidence": 72, "setup": "pullback",
               "signal_id": sig["id"], "sl_pct": 1.5, "tp1_pct": 1.5, "tpf_pct": 3.0,
               "reasoning": "Struktur bestätigt"}
        eng, emitted = _engine(monkeypatch, db, [dec], _cfg())
        res = await eng.run_analysis(only_symbols=[SYM], trigger=f"SETUP-SIGNAL {SYM} [sig:{sig['id']}]")
        assert res.get("status") == "ok", res
        assert len(emitted) == 1 and not emitted[0]["collection"]
        d = emitted[0]["dec"]
        assert d["signal_source"] == "ki_geprueft" and d["signal_ref"] == sig["id"]
        assert d["model"] == "openai/gpt-6-luna-pro"
        stored = await db.ai_decisions.find_one({"id": d["id"]})
        assert stored and stored["signal_source"] == "ki_geprueft"
    asyncio.run(_with_db(body))


def test_pruefer_vetoes_entry_without_detector_signal(monkeypatch):
    async def body(db):
        await _fresh_broker(db)
        dec = {"symbol": SYM, "action": "SHORT", "confidence": 80, "setup": "breakout",
               "sl_pct": 1.5, "tp1_pct": 1.5, "tpf_pct": 3.0, "reasoning": "frei"}
        eng, emitted = _engine(monkeypatch, db, [dec], _cfg())
        res = await eng.run_analysis(only_symbols=[SYM], trigger="Test ohne Signal")
        assert res.get("status") == "ok", res
        assert emitted == []
        stored = await db.ai_decisions.find_one({"symbol": SYM}, sort=[("_id", -1)])
        assert stored["action"] == "HOLD" and "Prüfer-Modus" in stored["reasoning"]
    asyncio.run(_with_db(body))
