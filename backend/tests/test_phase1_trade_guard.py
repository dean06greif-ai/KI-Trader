"""Phase 1 (Audit F2/F5/E3): Kill-Switch modus-rein, Lernpflicht erzwungen,
Mindest-Pause. Ohne Netzwerk/DB (Fake-DB)."""
import asyncio
from datetime import datetime, timezone, timedelta

import pytest

from services import trade_guard


class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *a, **k):
        return self

    async def to_list(self, n):
        return self.rows


def _match(doc, q):
    for k, v in q.items():
        if isinstance(v, dict):
            if "$ne" in v and doc.get(k) == v["$ne"]:
                return False
            if "$gte" in v and not (doc.get(k) or "") >= v["$gte"]:
                return False
            if "$gt" in v and not (doc.get(k) or "") > v["$gt"]:
                return False
        elif doc.get(k) != v:
            return False
    return True


class _Coll:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def find(self, q, proj=None):
        return _Cursor([r for r in self.rows if _match(r, q)])

    async def find_one(self, q, *a, **k):
        for r in self.rows:
            if _match(r, q):
                return dict(r)
        return None

    async def update_one(self, q, upd, upsert=False):
        for r in self.rows:
            if _match(r, q):
                r.update(upd.get("$set", {}))
                return
        if upsert:
            self.rows.append({**q, **upd.get("$set", {})})


class _DB:
    def __init__(self, trades=None):
        self.auto_trades = _Coll(trades)
        self.settings = _Coll()
        self.dynamic_transition_locks = _Coll()


TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%dT10:00:00")


def _closed(mode, pnl, result, dc=False, cap=1000.0):
    return {"status": "closed", "closed_at": TODAY, "realized_pnl": pnl, "result": result,
            "max_capital": cap, "mode": mode, "data_collection": dc}


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    trade_guard._cfg_cache = None
    triggered = []

    async def fake_trigger(db, tg, reason, mode="live"):
        triggered.append((mode, reason))
        await db.settings.update_one({"_id": trade_guard.state_id_for(mode)}, {"$set": {
            "paused_until": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            "reason": reason, "learning_required": True}}, upsert=True)

    async def no_ref(db, mode, rows):
        return 1000.0

    monkeypatch.setattr(trade_guard, "_trigger", fake_trigger)
    monkeypatch.setattr(trade_guard, "_reference_capital", no_ref)
    monkeypatch.setattr(trade_guard, "_current_mode", lambda: "paper")
    yield triggered
    trade_guard._cfg_cache = None


def _run(coro):
    return asyncio.run(coro)


def test_paper_gain_does_not_mask_live_loss(_isolate):
    live = _closed("live", -100, "loss")
    db = _DB([live, _closed("paper", 200, "win"), _closed("paper", 500, "win", dc=True)])
    _run(trade_guard.on_trade_closed(db, None, live))
    assert _isolate == [("live", "Tagesverlust 10.0% (Limit 5.0%, PnL -100.0 USDT)")]


def test_paper_losses_pause_only_paper(_isolate):
    rows = [_closed("paper", -10, "loss") for _ in range(3)]
    db = _DB(rows)
    _run(trade_guard.on_trade_closed(db, None, rows[-1]))
    assert _isolate and _isolate[0][0] == "paper"
    assert _run(trade_guard.get_state(db, "paper"))["paused"] is True
    assert _run(trade_guard.get_state(db, "live"))["paused"] is False


def test_collection_trade_never_counts(_isolate):
    rows = [_closed("paper", -50, "loss", dc=True) for _ in range(5)]
    db = _DB(rows)
    _run(trade_guard.on_trade_closed(db, None, rows[-1]))
    assert _isolate == []


def test_consecutive_losses_counted_per_mode(_isolate):
    live = [_closed("live", -1, "loss"), _closed("live", -1, "loss")]
    paper = [_closed("paper", -1, "loss")]
    db = _DB(live + paper)
    _run(trade_guard.on_trade_closed(db, None, live[-1]))
    assert _isolate == []  # nur 2 Live-Verluste, Paper zählt nicht mit


def test_learning_required_blocks_entry(_isolate, monkeypatch):
    db = _DB()
    _run(db.settings.update_one({"_id": trade_guard.STATE_ID},
                                {"$set": {"learning_required": True}}, upsert=True))
    kicked = []

    async def fake_kick(db_, mode="live"):
        kicked.append(mode)
    monkeypatch.setattr(trade_guard, "_kick_forced_learning", fake_kick)
    ok, why = _run(trade_guard.check_open_allowed(
        db, {"symbol": "BTCUSDT", "type": "LONG"}, "15m", mode="live"))
    assert not ok and "Zwangs-Lernphase" in why and kicked == ["live"]
    # Paper ist davon unberührt
    ok_p, _ = _run(trade_guard.check_open_allowed(
        db, {"symbol": "BTCUSDT", "type": "LONG"}, "15m", mode="paper"))
    assert ok_p
    # Resume hebt die Lernpflicht auf
    _run(trade_guard.resume(db, "live"))
    ok2, _ = _run(trade_guard.check_open_allowed(
        db, {"symbol": "BTCUSDT", "type": "LONG"}, "15m", mode="live"))
    assert ok2


def test_learning_required_ignored_when_forced_learning_disabled(_isolate):
    db = _DB()
    _run(db.settings.update_one({"_id": trade_guard.CONFIG_ID},
                                {"$set": {"forced_learning_enabled": False}}, upsert=True))
    _run(db.settings.update_one({"_id": trade_guard.STATE_ID},
                                {"$set": {"learning_required": True}}, upsert=True))
    ok, _ = _run(trade_guard.check_open_allowed(
        db, {"symbol": "BTCUSDT", "type": "LONG"}, "15m", mode="live"))
    assert ok


def test_anti_stacking_is_mode_specific(_isolate):
    now = datetime.now(timezone.utc).isoformat()
    db = _DB([{"status": "open", "symbol": "ETHUSDT", "side": "LONG", "timeframe": "5m",
               "mode": "paper", "opened_at": now}])
    sig = {"symbol": "ETHUSDT", "type": "LONG"}
    ok_live, _ = _run(trade_guard.check_open_allowed(db, sig, "5m", mode="live"))
    ok_paper, why = _run(trade_guard.check_open_allowed(db, sig, "5m", mode="paper"))
    assert ok_live and not ok_paper and "Anti-Stacking" in why


def test_pause_until_min_hours():
    now = datetime(2026, 9, 12, 23, 50, tzinfo=timezone.utc)
    assert trade_guard.pause_until(now, 0) == "2026-09-13T00:00:00+00:00"
    assert trade_guard.pause_until(now, 6) == "2026-09-13T05:50:00+00:00"
    early = datetime(2026, 9, 12, 8, 0, tzinfo=timezone.utc)
    assert trade_guard.pause_until(early, 6) == "2026-09-13T00:00:00+00:00"


def test_state_ids_and_mode_normalisation():
    assert trade_guard.state_id_for("live") == trade_guard.STATE_ID
    assert trade_guard.state_id_for("paper") == trade_guard.STATE_ID_PAPER
    assert trade_guard.normalize_mode(None) == "paper"
    assert trade_guard.normalize_mode("LIVE") == "live"
    assert "min_pause_hours" in trade_guard.DEFAULT_CONFIG
