"""Iteration 38 – E2E des PnL-Abgleichs gegen die ECHTE Bitunix-Positions-Historie.

READ-ONLY gegenüber der Börse: es wird ausschließlich get_history_positions
(GET) gelesen. Geschrieben wird nur ein temporärer TEST-Trade in der lokalen
DB, der am Ende wieder gelöscht wird. Keine Orders/Closes.
"""
import asyncio
import os
import sys
from datetime import datetime, timezone

import pytest
import requests
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

sys.path.insert(0, "/app/kitrader/backend")

BASE_URL = os.environ.get("KITRADER_BASE_URL", "http://localhost:8055").rstrip("/")
ENV = dotenv_values("/app/kitrader/backend/.env")
MONGO_URL = os.environ.get("MONGO_URL") or ENV.get("MONGO_URL")
DB_NAME = os.environ.get("DB_NAME") or ENV.get("DB_NAME")
TEST_TRADE_ID = "TEST_iter38_pnl_reconcile"


def admin_session():
    user = os.environ.get("ADMIN_USER") or ENV.get("ADMIN_USER") or "Admin"
    pw = os.environ.get("ADMIN_PASSWORD") or ENV.get("ADMIN_PASSWORD")
    if not pw:
        pytest.skip("Keine Admin-Credentials (ADMIN_PASSWORD) gesetzt")
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": user, "password": pw}, timeout=30)
    assert r.status_code == 200, r.text[:300]
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}",
                      "Content-Type": "application/json"})
    return s


async def _fetch_history_position():
    """Eine echte, bereits geschlossene Bitunix-Position lesen (GET only)."""
    from services.bitunix_trade import BitunixTradeClient, parse_closed_position
    client = BitunixTradeClient()
    if not client.configured():
        pytest.skip("Bitunix nicht konfiguriert")
    res = await client.get_history_positions(limit=20)
    if not isinstance(res, dict) or res.get("code") != 0:
        pytest.skip(f"Positions-Historie nicht lesbar: {str(res)[:200]}")
    data = res.get("data") or {}
    items = data.get("positionList") if isinstance(data, dict) else data
    for p in items or []:
        pid = str(p.get("positionId") or "")
        exact = parse_closed_position(res, pid) if pid else None
        if exact and exact.get("net_pnl") is not None:
            return pid, exact, p
    pytest.skip("Keine geschlossene Position in der Bitunix-Historie gefunden")


async def _seed(db, pid, symbol, side, qty):
    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "id": TEST_TRADE_ID, "symbol": symbol, "side": side, "mode": "live",
        "entry": 1.0, "sl": 0.9, "tp1": None, "tpf": None,
        "qty": qty, "qty_remaining": 0.0, "status": "closed",
        "realized_pnl": -999.0, "fees_paid": 0.0, "fee_percent": 0.06,
        "strategy_id": "external", "strategy_name": "TEST_iter38",
        "manual_trade": True, "external_adopted": True,
        "bitunix_position_id": pid, "opened_at": now, "closed_at": now,
        "trade_date": now[:10], "events": [], "result": "loss",
    }
    await db.auto_trades.delete_one({"id": TEST_TRADE_ID})
    await db.auto_trades.insert_one(dict(doc))
    return doc


def test_pnl_reconcile_uses_real_bitunix_pnl():
    async def run():
        pid, exact, raw = await _fetch_history_position()
        db = AsyncIOMotorClient(MONGO_URL)[DB_NAME]
        symbol = str(raw.get("symbol") or "BTCUSDT")
        side = "LONG" if str(raw.get("side", "")).upper() in ("BUY", "LONG") else "SHORT"
        qty = float(raw.get("maxQty") or 1)
        await _seed(db, pid, symbol, side, qty)
        try:
            s = admin_session()
            r = s.post(f"{BASE_URL}/api/autotrade/pnl-reconcile/run", timeout=180)
            assert r.status_code == 200, r.text[:300]
            body = r.json()
            assert body["status"] == "success", body
            assert body["checked"] >= 1, body
            assert body["reconciled"] >= 1, body
            t = await db.auto_trades.find_one({"id": TEST_TRADE_ID}, {"_id": 0})
            assert t is not None
            assert t.get("pnl_exchange_exact") is True, t
            assert abs(float(t["realized_pnl"]) - float(exact["net_pnl"])) < 1e-4, \
                (t["realized_pnl"], exact["net_pnl"])
            assert abs(float(t["fees_paid"]) - abs(float(exact["fee"]))) < 1e-4, t
            assert float(t["pnl_local_estimate"]) == -999.0, t
            assert t.get("pnl_reconciled_at"), t
            assert any("PNL-ABGLEICH" in e for e in t.get("events") or []), t.get("events")
            assert t["result"] in ("win", "loss", "breakeven")
            print(f"OK: pid={pid} lokal -999 -> echt {t['realized_pnl']} "
                  f"(fee {t['fees_paid']}, funding {t.get('funding_paid')}, "
                  f"result {t['result']})")
            # Idempotenz: zweiter Lauf darf denselben Trade nicht erneut anfassen
            r2 = s.post(f"{BASE_URL}/api/autotrade/pnl-reconcile/run", timeout=180)
            assert r2.status_code == 200
            t2 = await db.auto_trades.find_one({"id": TEST_TRADE_ID}, {"_id": 0})
            assert t2["realized_pnl"] == t["realized_pnl"]
            assert len(t2.get("events") or []) == len(t.get("events") or [])
        finally:
            await db.auto_trades.delete_one({"id": TEST_TRADE_ID})

    asyncio.run(run())
