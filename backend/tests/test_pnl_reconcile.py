"""Regressionstests: PnL-Abgleich geschlossener Live-Trades mit Bitunix.

Bug-Report: SL auf Break-Even, Kerze schoss durch -> Bitunix −3 USDT, Website 0.
Abgedeckt:
  * build_updates (rein): echter PnL/Fees/Result, lokaler Wert bleibt erhalten
  * reconcile_trade: Retry, Zähler bei fehlender Historie, kein Doppel-Abgleich
  * _after_close übernimmt den Börsen-PnL VOR Telegram/Rewards
  * reconcile_pending: nur Live-Trades mit Position-ID, Statusdokument
  * Paper-Trades / Trades ohne Position-ID werden nie angefasst
"""
import asyncio
import importlib.util
import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR.parent))


def _load(name):
    """Nachbar-Testmodul laden, ohne vom Paket-Namen 'tests' abzuhängen
    (im Repo-Root existiert ein zweites 'tests'-Paket)."""
    spec = importlib.util.spec_from_file_location(f"_helper_{name}", _TESTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

from services import pnl_reconcile
from services.bitunix_trade import AutoTradeManager
_pw = _load("test_position_watchdog")
FakeClient, FakeCollection, FakeDB = _pw.FakeClient, _pw.FakeCollection, _pw.FakeDB


def _hist_row(pid="p1", **kw):
    row = {"positionId": pid, "symbol": "ETHUSDT", "maxQty": "0.628", "qty": "0.628",
           "entryPrice": "2404.07", "closePrice": "2409.87", "side": "SELL",
           "fee": "0.75578858", "funding": "0", "realizedPNL": "-4.39818858"}
    row.update(kw)
    return row


class _Client(FakeClient):
    def __init__(self, rows=None, fail=False):
        super().__init__()
        self.rows = rows if rows is not None else []
        self.fail = fail
        self.hist_calls = 0

    async def get_history_positions(self, symbol=None, position_id=None, limit=20):
        self.hist_calls += 1
        if self.fail:
            raise RuntimeError("api down")
        return {"code": 0, "data": {"positionList": list(self.rows)}}


class _Coll(FakeCollection):
    def __init__(self, docs=None, find_rows=None):
        super().__init__(docs)
        self.find_rows = find_rows

    def find(self, q=None, *a, **kw):
        if self.find_rows is not None:
            return super().find({}) if False else _Rows(self.find_rows)
        return super().find(q)


class _Rows:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def sort(self, *a, **kw):
        return self

    async def to_list(self, n=None):
        return self.rows


def _trade(**kw):
    t = {"id": "ETHUSDT-1", "symbol": "ETHUSDT", "side": "SHORT", "mode": "live",
         "status": "closed", "qty": 0.628, "qty_remaining": 0,
         "entry": 2404.07, "sl": 2404.07, "realized_pnl": 0.0, "fees_paid": 0.9,
         "result": "breakeven", "bitunix_position_id": "p1", "events": ["BREAK-EVEN hit"]}
    t.update(kw)
    return t


def _mgr(client, trades):
    db = FakeDB(trades)
    at = AutoTradeManager(client)
    at.set_db(db)
    return at, db


# ---------------- rein ----------------
def test_build_updates_takes_exchange_truth():
    exact = {"exit_price": 2409.87, "net_pnl": -4.398189, "fee": 0.755789,
             "funding": 0.0, "fee_included_in_pnl": True, "max_qty": 0.628}
    upd = pnl_reconcile.build_updates(_trade(), exact)
    assert upd["realized_pnl"] == -4.398189 and upd["result"] == "loss"
    assert upd["fees_paid"] == 0.755789 and upd["exit_price"] == 2409.87
    assert upd["pnl_exchange_exact"] is True
    assert upd["pnl_local_estimate"] == 0.0 and upd["pnl_reconcile_diff"] == -4.398189
    assert any("PNL-ABGLEICH" in e for e in upd["events"])
    assert upd["events"][0] == "BREAK-EVEN hit"  # Historie bleibt


def test_build_updates_no_event_when_local_already_correct():
    upd = pnl_reconcile.build_updates(_trade(realized_pnl=-4.398),
                                      {"net_pnl": -4.398, "fee": 0.75, "funding": 0})
    assert upd["pnl_exchange_exact"] is True
    assert "events" not in upd and "pnl_local_estimate" not in upd
    assert pnl_reconcile.build_updates(_trade(), None) is None


def test_result_for_thresholds():
    assert pnl_reconcile.result_for(0.5) == "win"
    assert pnl_reconcile.result_for(-0.5) == "loss"
    assert pnl_reconcile.result_for(0.0) == "breakeven"


# ---------------- reconcile_trade ----------------
def test_reconcile_trade_overwrites_breakeven_with_real_loss():
    client = _Client(rows=[_hist_row()])
    at, db = _mgr(client, [_trade()])
    t = dict(db.auto_trades.docs[0])
    upd = asyncio.run(pnl_reconcile.reconcile_trade(at, t, retries=0))
    assert upd and round(upd["realized_pnl"], 4) == -4.3982
    doc = db.auto_trades.docs[0]
    assert doc["result"] == "loss" and doc["pnl_exchange_exact"] is True
    assert round(doc["realized_pnl"], 4) == -4.3982 and doc["pnl_local_estimate"] == 0.0
    assert t["realized_pnl"] == doc["realized_pnl"]  # in-place für Folge-Hooks


def test_reconcile_trade_counts_attempt_when_history_missing():
    client = _Client(rows=[])  # Historie läuft nach
    at, db = _mgr(client, [_trade()])
    t = dict(db.auto_trades.docs[0])
    upd = asyncio.run(pnl_reconcile.reconcile_trade(at, t, retries=1, delay=0))
    assert upd is None
    assert client.hist_calls == 2  # 1 Versuch + 1 Retry
    assert db.auto_trades.docs[0]["pnl_reconcile_attempts"] == 1
    assert db.auto_trades.docs[0]["realized_pnl"] == 0.0  # unverändert


def test_reconcile_trade_skips_paper_exact_and_missing_pid():
    client = _Client(rows=[_hist_row()])
    at, db = _mgr(client, [])
    assert asyncio.run(pnl_reconcile.reconcile_trade(at, _trade(mode="paper"))) is None
    assert asyncio.run(pnl_reconcile.reconcile_trade(at, _trade(bitunix_position_id=None))) is None
    assert asyncio.run(pnl_reconcile.reconcile_trade(at, _trade(pnl_exchange_exact=True))) is None
    assert client.hist_calls == 0


def test_reconcile_trade_respects_mixed_position_guard():
    """Manuell aufgestockte Position (maxQty weicht >5 % ab): kein Überschreiben."""
    client = _Client(rows=[_hist_row(maxQty="1.5")])
    at, db = _mgr(client, [_trade()])
    assert asyncio.run(pnl_reconcile.reconcile_trade(at, dict(db.auto_trades.docs[0]),
                                                     retries=0)) is None
    assert db.auto_trades.docs[0]["realized_pnl"] == 0.0


def test_reconcile_trade_survives_api_error():
    client = _Client(fail=True)
    at, db = _mgr(client, [_trade()])
    assert asyncio.run(pnl_reconcile.reconcile_trade(at, dict(db.auto_trades.docs[0]),
                                                     retries=0)) is None


# ---------------- _after_close-Integration ----------------
def test_after_close_uses_exchange_pnl_for_hooks():
    client = _Client(rows=[_hist_row()])
    at, db = _mgr(client, [_trade()])
    seen = {}

    class _TG:
        async def send_message(self, *a, **kw):
            pass

    from services import notifications

    async def _fake_notify(db_, tg, kind, text):
        seen[kind] = text
    orig = notifications.telegram_notify
    notifications.telegram_notify = _fake_notify
    try:
        asyncio.run(at._after_close(dict(db.auto_trades.docs[0])))
    finally:
        notifications.telegram_notify = orig
    doc = db.auto_trades.docs[0]
    assert doc["result"] == "loss" and round(doc["realized_pnl"], 2) == -4.4
    assert "trade_closed" in seen and "-4.398" in seen["trade_closed"]
    assert "loss" in seen["trade_closed"]


# ---------------- reconcile_pending ----------------
def test_reconcile_pending_processes_only_candidates():
    client = _Client(rows=[_hist_row("p1"), _hist_row("p2", realizedPNL="3.0",
                                                      side="BUY", fee="0.1")])
    at, db = _mgr(client, [_trade(id="a", bitunix_position_id="p1"),
                           _trade(id="b", bitunix_position_id="p2", realized_pnl=2.9)])
    db.auto_trades = _Coll(db.auto_trades.docs, find_rows=db.auto_trades.docs)
    out = asyncio.run(pnl_reconcile.reconcile_pending(at))
    assert out == {"checked": 2, "reconciled": 2, "changed": 2}
    by = {d["id"]: d for d in db.auto_trades.docs}
    assert round(by["a"]["realized_pnl"], 4) == -4.3982
    assert by["b"]["pnl_exchange_exact"] is True
    status = next(u for q, u in db.settings.updates if q.get("_id") == "pnl_reconcile_status")
    assert status["$set"]["reconciled"] == 2 and status["$set"]["last_run_at"]


def test_reconcile_pending_noop_without_client():
    class _Off(FakeClient):
        def configured(self):
            return False
    at, db = _mgr(_Off(), [_trade()])
    assert asyncio.run(pnl_reconcile.reconcile_pending(at)) == {
        "checked": 0, "reconciled": 0, "changed": 0}
