"""Regressionstests: Nacht-Serie / Job-Warteschlange (services/job_series.py).

Abgedeckt (rein, ohne Server):
  * default_label / summarize / next_item / may_start / Telegram-Texte
  * add_item / reorder / remove / clear_finished mit Fake-DB
  * tick(): startet den nächsten Eintrag über den Kind-Starter, wartet auf das
    Job-Ende, speichert Zusammenfassung + job_id, meldet per Telegram, respektiert
    Pause/Startzeit und laufende Fremd-Jobs
  * is_stale_strategy (Strategie-Vergleich: Karteileichen)
"""
import asyncio
import importlib.util
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TESTS_DIR.parent))

from services import job_series as js  # noqa: E402


def _load(name):
    spec = importlib.util.spec_from_file_location(f"_helper_{name}", _TESTS_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_pw = _load("test_position_watchdog")
FakeCollection, FakeDB = _pw.FakeCollection, _pw.FakeDB


class _Coll(FakeCollection):
    def find(self, q=None, *a, **kw):
        q = q or {}
        rows = [d for d in self.docs if self._match(d, q)]
        st = q.get("status")
        if isinstance(st, dict) and "$in" in st:
            rows = [d for d in self.docs if d.get("status") in st["$in"]
                    and all(self._match(d, {k: v}) for k, v in q.items() if k != "status")]
        return _Cur(rows)

    async def count_documents(self, q):
        st = (q or {}).get("status")
        if isinstance(st, dict) and "$in" in st:
            return sum(1 for d in self.docs if d.get("status") in st["$in"])
        return sum(1 for d in self.docs if self._match(d, q or {}))

    async def delete_one(self, q):
        self.docs = [d for d in self.docs if not self._match(d, q)]

    async def delete_many(self, q):
        st = (q or {}).get("status")
        before = len(self.docs)
        if isinstance(st, dict) and "$in" in st:
            self.docs = [d for d in self.docs if d.get("status") not in st["$in"]]
        else:
            self.docs = [d for d in self.docs if not self._match(d, q or {})]
        return type("R", (), {"deleted_count": before - len(self.docs)})()

    async def update_one(self, q, upd, **kw):
        n = 0
        for d in self.docs:
            if self._match(d, q):
                d.update(upd.get("$set", {}))
                n += 1
        if not n and kw.get("upsert"):
            self.docs.append({**q, **upd.get("$set", {})})
        self.updates.append((q, upd))
        return type("R", (), {"modified_count": n})()

    async def find_one(self, q, *a, **kw):
        for d in self.docs:
            if self._match(d, q):
                return dict(d)
        return None


class _Cur:
    def __init__(self, rows):
        self.rows = [dict(r) for r in rows]

    def sort(self, key, direction=1):
        if isinstance(key, list):
            for k, dirn in reversed(key):
                self.rows.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=dirn < 0)
        else:
            self.rows.sort(key=lambda r: (r.get(key) is None, r.get(key)), reverse=direction < 0)
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, n=None):
        return self.rows[:n] if n else self.rows


class _DB(FakeDB):
    def __init__(self):
        super().__init__([])
        self.job_series = _Coll()
        self.settings = _Coll()
        self.backtests = _Coll()
        self.optimizer_runs = _Coll()


BT_BODY = {"strategy_ids": ["rsi_reversal", "ema_cross"], "symbols": ["BTCUSDT", "ETHUSDT"],
           "days": 30, "max_capital": 100}


# ---------------- rein ----------------
def test_default_label_and_summaries():
    assert js.default_label("backtest", BT_BODY) == "Backtest rsi_reversal, ema_cross · BTCUSDT, ETHUSDT · 30d"
    assert js.default_label("optimizer", {"mode": "params", "strategy_id": "rsi", "symbols": ["BTCUSDT"],
                                          "days": 90, "timeframe": "5m"}) == "Optimizer params: rsi · BTCUSDT · 90d · 5m"
    assert js.default_label("regime_analysis", {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 365}).startswith("Regime-Analyse")
    s = js.summarize("backtest", {"config": {"max_capital": 100}, "per_strategy": [
        {"strategy_id": "a", "strategy_name": "A", "pnl": 12.0, "trades": 10, "wins": 6, "losses": 4, "max_drawdown": 3.0},
        {"strategy_id": "b", "strategy_name": "B", "pnl": -2.0, "trades": 5, "wins": 2, "losses": 3, "max_drawdown": 5.0}]})
    assert s == {"pnl": 10.0, "trades": 15, "max_drawdown": 5.0, "best": "A", "win_rate": 53.3,
                 "strategies": 2, "capital": 100}
    o = js.summarize("optimizer", {"mode": "discovery", "top5": [{"metrics": {"pnl": 40, "trades": 20, "win_rate": 55},
                                                                    "wf": 0.8, "passed": True}],
                                   "definition": {"name": "Disco"}, "max_capital": 200})
    assert o["pnl"] == 40 and o["strategy"] == "Disco" and o["has_definition"] and o["passed"]
    assert js.summarize("regime_analysis", {"analysis_id": "abc"})["analysis_id"] == "abc"
    assert js.summarize("backtest", None) == {}


def test_next_item_and_may_start():
    items = [{"id": "b", "status": "queued", "position": 2}, {"id": "a", "status": "queued", "position": 1},
             {"id": "c", "status": "done", "position": 0}]
    assert js.next_item(items)["id"] == "a"
    assert js.next_item([{"status": "done"}]) is None
    assert js.may_start({"paused": True}) == (False, "pausiert")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    ok, why = js.may_start({"start_at": future})
    assert not ok and why.startswith("geplant ab")
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    assert js.may_start({"start_at": past})[0] is True
    assert js.may_start({"start_at": "kaputt"})[0] is True
    assert js.may_start({}) == (True, "bereit")


def test_telegram_texts():
    done = [{"label": "Backtest X", "status": "done", "summary": {"pnl": 12.5, "win_rate": 55, "trades": 9, "max_drawdown": 3}},
            {"label": "Opt Y", "status": "error", "error": "RAM", "summary": {}}]
    txt = js.series_summary_text(done)
    assert "Nacht-Serie fertig" in txt and "2 Job(s)" in txt and "PnL +12.50 USDT" in txt and "❌ Opt Y – RAM" in txt
    one = js.item_done_text(done[0], 3)
    assert "Job fertig" in one and "Noch 3 in der Warteschlange" in one


def test_is_stale_strategy():
    from routers.analytics import is_stale_strategy

    class _Reg:
        def get(self, sid):
            return {"id": sid} if sid == "rsi_reversal" else None
    assert is_stale_strategy("custom_deleted_1", _Reg()) is True
    assert is_stale_strategy("rsi_reversal", _Reg()) is False
    assert is_stale_strategy("ai_trader", _Reg()) is False
    assert is_stale_strategy("manual", _Reg()) is False


class _Reg:
    def get(self, sid):
        return {"id": sid} if sid in ("rsi_reversal", "ema_cross", "rsi") else None


def test_validate_body():
    reg, syms = _Reg(), {"BTCUSDT", "ETHUSDT"}
    assert js.validate_body("backtest", BT_BODY, reg, syms) is None
    assert "Coin" in js.validate_body("backtest", {**BT_BODY, "symbols": []}, reg, syms)
    assert "Unbekannte Coins" in js.validate_body("backtest", {**BT_BODY, "symbols": ["XXXUSDT"]}, reg, syms)
    assert "Unbekannte Strategien" in js.validate_body("backtest", {**BT_BODY, "strategy_ids": ["nope"]}, reg, syms)
    assert js.validate_body("optimizer", {"mode": "params", "strategy_id": "rsi", "symbols": ["BTCUSDT"]}, reg, syms) is None
    assert "strategy_id" in js.validate_body("optimizer", {"mode": "params", "symbols": ["BTCUSDT"]}, reg, syms)
    assert "mode" in js.validate_body("optimizer", {"mode": "foo", "symbols": ["BTCUSDT"]}, reg, syms)
    assert js.validate_body("optimizer", {"mode": "discovery", "symbols": ["BTCUSDT"]}, reg, syms) is None
    assert js.validate_body("regime_analysis", {"symbols": ["XAUUSDT"]}, reg, syms) is None


# ---------------- Persistenz ----------------
def test_add_reorder_remove_clear(monkeypatch):
    monkeypatch.setattr(js, "validate_body", lambda k, b, *a: None)
    db = _DB()
    a = asyncio.run(js.add_item(db, "backtest", BT_BODY))
    b = asyncio.run(js.add_item(db, "optimizer", {"mode": "params", "strategy_id": "rsi", "symbols": ["BTCUSDT"], "days": 30}, "Mein Lauf"))
    assert a["position"] == 1 and b["position"] == 2 and b["label"] == "Mein Lauf"
    with pytest.raises(ValueError):
        asyncio.run(js.add_item(db, "unknown", BT_BODY))
    with pytest.raises(ValueError):
        asyncio.run(js.add_item(db, "backtest", {}))
    assert asyncio.run(js.reorder(db, [b["id"], a["id"]])) == 2
    items = asyncio.run(js.list_items(db))
    assert [i["id"] for i in items] == [b["id"], a["id"]]
    assert js.next_item(items)["id"] == b["id"]
    db.job_series.docs[1]["status"] = "done"  # b
    assert asyncio.run(js.clear_finished(db)) == 1
    assert asyncio.run(js.remove_item(db, a["id"])) is True
    assert asyncio.run(js.remove_item(db, "nope")) is False
    assert asyncio.run(js.list_items(db)) == []


def test_update_state_parses_start_at():
    db = _DB()
    st = asyncio.run(js.update_state(db, {"paused": True, "start_at": "2099-06-10T23:00", "notify_each": False}))
    assert st["paused"] is True and st["start_at"].startswith("2099-06-10T23:00") and st["notify_each"] is False
    st = asyncio.run(js.update_state(db, {"start_at": None, "paused": False}))
    assert st["start_at"] is None and st["paused"] is False


# ---------------- Worker ----------------
def _run_tick_with_fake_job(monkeypatch, db, outcome_status="done", result=None, fail_start=False):
    fake_jobs = {}

    async def _start(kind, body):
        if fail_start:
            raise RuntimeError("Es läuft bereits ein Backtest")
        fake_jobs["j1"] = {"id": "j1", "status": "running", "progress": 0, "phase": "Startet",
                           "result": None, "error": None}

        async def _finish():
            await asyncio.sleep(0.05)
            fake_jobs["j1"].update({"progress": 100, "status": outcome_status, "result": result,
                                    "error": "kaputt" if outcome_status == "error" else None})
        asyncio.get_event_loop().create_task(_finish())
        return "j1"

    sent = []

    async def _notify(db_, tg, text):
        sent.append(text)
    monkeypatch.setattr(js, "_start_job", _start)
    monkeypatch.setattr(js, "_jobs_for", lambda kind: fake_jobs)
    monkeypatch.setattr(js, "_notify", _notify)
    monkeypatch.setattr(js, "POLL_SEC", 0.01)
    return asyncio.run(js.tick(db)), sent


def test_tick_runs_next_item_and_notifies(monkeypatch):
    monkeypatch.setattr(js, "validate_body", lambda k, b, *a: None)
    db = _DB()
    asyncio.run(js.add_item(db, "backtest", BT_BODY))
    result = {"config": {"max_capital": 100}, "per_strategy": [
        {"strategy_id": "a", "strategy_name": "A", "pnl": 12.0, "trades": 10, "wins": 6, "losses": 4, "max_drawdown": 3.0}]}
    done, sent = _run_tick_with_fake_job(monkeypatch, db, result=result)
    assert done["status"] == "done" and done["job_id"] == "j1"
    assert done["summary"]["pnl"] == 12.0
    doc = db.job_series.docs[0]
    assert doc["status"] == "done" and doc["finished_at"] and doc["progress"] == 100
    # je Job + Serie komplett
    assert len(sent) == 2 and "Job fertig" in sent[0] and "Nacht-Serie fertig" in sent[1]
    st = asyncio.run(js.get_state(db))
    assert st.get("last_series_done_at") and st.get("start_at") is None


def test_tick_error_and_start_failure(monkeypatch):
    monkeypatch.setattr(js, "validate_body", lambda k, b, *a: None)
    db = _DB()
    asyncio.run(js.add_item(db, "backtest", BT_BODY))
    done, _ = _run_tick_with_fake_job(monkeypatch, db, outcome_status="error")
    assert done["status"] == "error" and done["error"] == "kaputt"
    db2 = _DB()
    asyncio.run(js.add_item(db2, "backtest", BT_BODY))
    done, sent = _run_tick_with_fake_job(monkeypatch, db2, fail_start=True)
    assert done["status"] == "error" and "bereits" in done["error"]


def test_tick_respects_pause_schedule_and_running_jobs(monkeypatch):
    monkeypatch.setattr(js, "validate_body", lambda k, b, *a: None)
    db = _DB()
    asyncio.run(js.add_item(db, "backtest", BT_BODY))
    asyncio.run(js.update_state(db, {"paused": True}))
    assert asyncio.run(js.tick(db)) is None
    asyncio.run(js.update_state(db, {"paused": False,
                                     "start_at": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}))
    assert asyncio.run(js.tick(db)) is None
    asyncio.run(js.update_state(db, {"start_at": None}))
    monkeypatch.setattr(js, "any_job_running", lambda: True)
    assert asyncio.run(js.tick(db)) is None
    assert db.job_series.docs[0]["status"] == "queued"
