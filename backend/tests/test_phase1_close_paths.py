"""Phase 1 (Audit F1/F12): Close-Fehlpfad bleibt OFFEN (kein Phantom-'closed'),
atomarer Close (Compare-and-Set) verhindert doppelte Hooks. Ohne Netzwerk."""
import asyncio
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

import pytest

from services import bitunix_trade as bt
from services.bitunix_trade import AutoTradeManager


class _Res:
    def __init__(self, matched):
        self.matched_count = matched
        self.modified_count = matched


class _Coll:
    def __init__(self, rows=None):
        self.rows = {r["id"]: dict(r) for r in (rows or [])}
        self.calls = []

    def _match(self, r, q):
        return all(r.get(k) == v for k, v in q.items())

    async def find_one(self, q, *a, **k):
        for r in self.rows.values():
            if self._match(r, q):
                return dict(r)
        return None

    async def update_one(self, q, upd, upsert=False):
        self.calls.append((dict(q), dict(upd.get("$set", {}))))
        for r in self.rows.values():
            if self._match(r, q):
                r.update(upd.get("$set", {}))
                return _Res(1)
        return _Res(0)


class _DB:
    def __init__(self, trades):
        self.auto_trades = _Coll(trades)
        self.settings = _Coll()
        self.signals = _Coll()


def _trade(**kw):
    base = {"id": "T1", "symbol": "BTCUSDT", "side": "LONG", "mode": "live",
            "status": "open", "entry": 100.0, "sl": 99.0, "initial_sl": 99.0,
            "tp1": 102.0, "tpf": 104.0, "tp_full": 104.0, "tp1_close_percent": 50,
            "tp1_hit": True, "breakeven_moved": True, "qty": 1.0, "qty_remaining": 0.5,
            "realized_pnl": 1.0, "fees_paid": 0.1, "fee_percent": 0.06,
            "leverage": 10, "max_capital": 10.0, "strategy_id": "ai_trader",
            "trail_after_tp1": False, "be_mode": "off", "events": [],
            "opened_at": (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}
    base.update(kw)
    return base


def _manager(db, flash_ok=False):
    m = AutoTradeManager(SimpleNamespace(configured=lambda: True))
    m.db = db
    m.after = []
    m.notes = []

    async def flash(t, qty):
        return {"ok": flash_ok, "detail": "ok" if flash_ok else "code 30001 exchange error"}

    async def reconcile(t, detail):
        return None

    async def notify(sym, side, msg):
        m.notes.append(msg)

    async def after(t):
        m.after.append(t)

    async def mark(symbol):
        return 98.0

    m._live_flash_close = flash
    m._reconcile_close_error = reconcile
    m._notify_reject = notify
    m._after_close = after
    m._current_mark = mark
    return m


def test_failed_close_keeps_trade_open_after_5_attempts():
    t = _trade(live_close_attempts=4, live_close_last_try=None)
    db = _DB([t])
    m = _manager(db, flash_ok=False)
    asyncio.run(m._manage_trade(dict(t), price=98.0))  # SL 99 -> Exit-Auslöser
    row = db.auto_trades.rows["T1"]
    assert row["status"] == "open", "Trade darf NICHT lokal geschlossen werden"
    assert row["live_close_attempts"] == 5 and row["live_close_failed"] is True
    assert row["qty_remaining"] == 0.5 and row["realized_pnl"] == 1.0  # PnL unverändert
    assert row.get("close_escalated_at") and "result" not in row
    assert m.after == [], "kein _after_close (Kill-Switch/Reward/Telegram) ohne Börsen-Bestätigung"
    assert any("KRITISCH" in n for n in m.notes)


def test_escalated_close_is_throttled_then_retried():
    now = datetime.now(timezone.utc)
    t = _trade(live_close_attempts=7, live_close_last_try=now.isoformat())
    db = _DB([t])
    m = _manager(db, flash_ok=False)
    asyncio.run(m._manage_trade(dict(t), price=98.0))
    assert db.auto_trades.rows["T1"]["live_close_attempts"] == 7  # gedrosselt, kein Versuch
    t2 = _trade(live_close_attempts=7,
                live_close_last_try=(now - timedelta(seconds=120)).isoformat())
    db2 = _DB([t2])
    m2 = _manager(db2, flash_ok=True)
    asyncio.run(m2._manage_trade(dict(t2), price=98.0))
    row = db2.auto_trades.rows["T1"]
    assert row["status"] == "closed" and row["live_close_failed"] is False
    assert len(m2.after) == 1


def test_successful_close_still_books_and_hooks_once():
    t = _trade()
    db = _DB([t])
    m = _manager(db, flash_ok=True)
    asyncio.run(m._manage_trade(dict(t), price=98.0))
    row = db.auto_trades.rows["T1"]
    assert row["status"] == "closed" and row["result"] in ("win", "loss", "breakeven")
    assert len(m.after) == 1


def test_finalize_close_is_compare_and_set():
    t = _trade()
    db = _DB([t])
    m = _manager(db)
    upd = {"status": "closed", "result": "loss"}
    first = asyncio.run(m._finalize_close("T1", upd))
    second = asyncio.run(m._finalize_close("T1", upd))
    assert first is True and second is False
    assert db.auto_trades.calls[-1][0] == {"id": "T1", "status": "open"}


def test_concurrent_manual_close_runs_hooks_once():
    t = _trade(mode="paper")
    db = _DB([t])
    m = _manager(db)

    async def both():
        return await asyncio.gather(m.manual_close("T1", 98.0), m.manual_close("T1", 98.0))

    r1, r2 = asyncio.run(both())
    assert len(m.after) == 1
    assert sum(1 for r in (r1, r2) if r and r.get("already_closed")) <= 1
    assert db.auto_trades.rows["T1"]["status"] == "closed"


def test_close_retry_due_pure():
    now = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
    assert bt.close_retry_due(None) is True
    assert bt.close_retry_due((now - timedelta(seconds=30)).isoformat(), 60, now) is False
    assert bt.close_retry_due((now - timedelta(seconds=61)).isoformat(), 60, now) is True
    assert bt.CLOSE_FAIL_ESCALATE_AT == 5
