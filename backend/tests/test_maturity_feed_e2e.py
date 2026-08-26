"""E2E-Test: Reife-Übergang schreibt genau EINE KI-Feed-Meldung.

Setup:
- settings-Doc _id='ai_playbook_state': setze live_ready.breakout=False
- 6 geschlossene Test-Trades in auto_trades (strategy_id=ai_trader, status=closed,
  setup=breakout, opened_at=jetzt-ISO, 4x pnl>0 / 2x pnl<0, Summe positiv,
  ids mit Präfix TEST-MATURITY-)
- GET /api/ai/playbook  -> refresh() erkennt Übergang und postet EINE Nachricht
- Zweiter GET postet KEINE weitere Nachricht
Cleanup:
- Test-Trades löschen (id-Präfix TEST-MATURITY-)
- ai_chat playbook-Testnachricht (setup=breakout, text enthält LIVE-freigeschaltet)
  löschen
- live_ready.breakout wieder auf False setzen bzw. Feld bereinigen
"""
import asyncio
import os
from datetime import datetime, timezone

import pytest
import requests
from motor.motor_asyncio import AsyncIOMotorClient

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
MONGO_URL = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB_NAME = os.environ.get("DB_NAME", "crypto_scanner")

# Try load from backend/.env if not set
if "REACT_APP_BACKEND_URL" not in os.environ:
    pytest.skip("REACT_APP_BACKEND_URL not set", allow_module_level=True)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


TEST_ID_PREFIX = "TEST-MATURITY-"


async def _setup_state_and_trades(db):
    """Set live_ready.breakout=False and insert 6 closed test trades."""
    # First read current state to preserve other fields
    doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
    lr = dict(doc.get("live_ready") or {})
    lr["breakout"] = False
    await db.settings.update_one(
        {"_id": "ai_playbook_state"},
        {"$set": {"live_ready": lr}}, upsert=True)

    # Insert 6 closed breakout trades - 4 winners, 2 losers, sum positive
    pnls = [5.0, 4.0, 3.0, 2.0, -1.0, -2.0]  # sum = 11.0
    now = _now_iso()
    trades = []
    for i, pnl in enumerate(pnls):
        trades.append({
            "id": f"{TEST_ID_PREFIX}{i}",
            "strategy_id": "ai_trader",
            "status": "closed",
            "setup": "breakout",
            "opened_at": now,
            "closed_at": now,
            "realized_pnl": pnl,
            "mode": "paper",
            "symbol": "BTCUSDT",
            "side": "LONG",
        })
    await db.auto_trades.insert_many(trades)
    return len(trades)


async def _cleanup(db):
    """Remove test trades, playbook test message, reset live_ready.breakout."""
    del_trades = await db.auto_trades.delete_many(
        {"id": {"$regex": f"^{TEST_ID_PREFIX}"}})
    del_msgs = await db.ai_chat.delete_many(
        {"role": "playbook", "setup": "breakout",
         "text": {"$regex": "LIVE-freigeschaltet"}})
    # Reset live_ready.breakout back to False (as required by cleanup rules)
    doc = await db.settings.find_one({"_id": "ai_playbook_state"}) or {}
    lr = dict(doc.get("live_ready") or {})
    if "breakout" in lr:
        lr["breakout"] = False
    await db.settings.update_one(
        {"_id": "ai_playbook_state"},
        {"$set": {"live_ready": lr}}, upsert=True)
    return del_trades.deleted_count, del_msgs.deleted_count


async def _count_test_feed_msgs(db):
    return await db.ai_chat.count_documents(
        {"role": "playbook", "setup": "breakout",
         "text": {"$regex": "LIVE-freigeschaltet"}})


async def _run_test():
    client = AsyncIOMotorClient(MONGO_URL)
    db = client[DB_NAME]
    try:
        # Preclean any prior test data
        await _cleanup(db)

        # Setup: state + trades
        n_inserted = await _setup_state_and_trades(db)
        assert n_inserted == 6

        # Baseline count of matching feed messages (should be 0)
        pre = await _count_test_feed_msgs(db)
        assert pre == 0, f"Baseline should be 0, got {pre}"

        # First GET -> triggers refresh() -> should post exactly one message
        r1 = requests.get(f"{BASE_URL}/api/ai/playbook", timeout=30)
        assert r1.status_code == 200, f"GET1 status {r1.status_code}"
        data1 = r1.json()
        assert isinstance(data1.get("maturity"), list)
        assert len(data1["maturity"]) == 10

        # Find breakout row -> should be live_ready True now
        by_setup = {r["setup"]: r for r in data1["maturity"]}
        assert "breakout" in by_setup
        assert by_setup["breakout"]["live_ready"] is True, \
            f"breakout should be live_ready after 6 trades, got {by_setup['breakout']}"
        assert by_setup["breakout"]["trades"] == 6

        # Small wait to ensure insert flushed
        await asyncio.sleep(0.3)

        after_first = await _count_test_feed_msgs(db)
        assert after_first == 1, \
            f"Expected exactly 1 playbook feed message after first GET, got {after_first}"

        # Second GET -> should NOT post another
        r2 = requests.get(f"{BASE_URL}/api/ai/playbook", timeout=30)
        assert r2.status_code == 200
        await asyncio.sleep(0.3)

        after_second = await _count_test_feed_msgs(db)
        assert after_second == 1, \
            f"Expected still 1 playbook feed message after second GET, got {after_second}"

        # Verify message content
        msg = await db.ai_chat.find_one(
            {"role": "playbook", "setup": "breakout",
             "text": {"$regex": "LIVE-freigeschaltet"}})
        assert msg is not None
        assert "LIVE-freigeschaltet" in msg["text"]
        assert msg.get("setup") == "breakout"

    finally:
        # Cleanup
        n_tr, n_msg = await _cleanup(db)
        print(f"Cleanup: {n_tr} trades, {n_msg} messages deleted")
        client.close()


def test_maturity_transition_posts_exactly_one_feed_message():
    asyncio.run(_run_test())
