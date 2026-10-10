"""Regressionstests: Daten-Wiederherstellung (services/data_recovery.py),
Trade-Papierkorb (services/trade_trash.py), MasterPrompt-/Lektions-History."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import data_recovery as dr  # noqa: E402
from services.ai_master_prompt import DEFAULT_TEXT, DEFAULT_LESSON_POLICY  # noqa: E402
from services.ai_master_prompt import MasterPromptStore, HISTORY_KEEP  # noqa: E402


# ---------------------------------------------------------------- Test-Artefakte
def test_test_artifact_detection():
    assert dr.is_test_artifact("UI-TEXT-42", "COMBO-POLICY-42")
    assert dr.is_test_artifact(DEFAULT_TEXT, "TESTPOLICY-XYZ")
    assert dr.is_test_artifact(DEFAULT_TEXT + "\n[QA_TEST marker]", DEFAULT_LESSON_POLICY)
    assert dr.is_test_artifact("kurz", DEFAULT_LESSON_POLICY)
    assert not dr.is_test_artifact(DEFAULT_TEXT, DEFAULT_LESSON_POLICY)


def test_pick_restorable_master_prefers_newest_real_version():
    hist = [
        {"version": 102, "text": DEFAULT_TEXT, "lesson_policy": DEFAULT_LESSON_POLICY,
         "rules": {"max_leverage": 50}},
        {"version": 103, "text": DEFAULT_TEXT, "lesson_policy": "TESTPOLICY-XYZ"},
        {"version": 104, "text": "UI-TEXT-42", "lesson_policy": "COMBO-POLICY-42"},
        {"version": 108, "text": DEFAULT_TEXT + "[QA_TE", "lesson_policy": DEFAULT_LESSON_POLICY},
    ]
    src = dr.pick_restorable_master(hist)
    assert src["version"] == 102 and src["rules"]["max_leverage"] == 50
    assert dr.pick_restorable_master([hist[2]]) is None


def test_test_lesson_detection():
    assert dr.is_test_lesson({"title": "TEST_Lektion_ChatCmd"})
    assert dr.is_test_lesson({"title": "[QA] irgendwas"})
    assert not dr.is_test_lesson({"title": "Stop-Loss-Abstand zu eng – 1.5 ATR nötig"})
    assert not dr.is_test_lesson({"title": "Test der Unterstützung abwarten"})   # 'Test' als Wort ist ok


# ---------------------------------------------------------------- Trade-Rekonstruktion
ACTIONS = [
    {"trade_id": "BTCUSDT-1785345367568", "symbol": "BTCUSDT", "side": "SHORT", "mode": "paper",
     "action": "adjust_sl", "source": "ki", "ts": "2026-07-29T16:30:00+00:00", "result": {"sl": 1.0}},
    {"trade_id": "BTCUSDT-1785345367568", "symbol": "BTCUSDT", "side": "SHORT", "mode": "paper",
     "action": "partial_close", "source": "ki", "ts": "2026-07-29T17:00:00+00:00",
     "result": {"realized_pnl": 0.5}},
    {"trade_id": "BTCUSDT-1785345367568", "symbol": "BTCUSDT", "side": "SHORT", "mode": "paper",
     "action": "close", "source": "ki", "ts": "2026-07-29T17:21:26+00:00",
     "reason": "Fed in 25 Min", "result": {"result": "loss", "realized_pnl": -1.377023}},
]


def test_rebuild_trade_from_actions():
    t = dr.rebuild_trade("BTCUSDT-1785345367568", ACTIONS)
    assert t["status"] == "closed" and t["mode"] == "paper" and t["side"] == "SHORT"
    assert t["result"] == "loss" and abs(t["realized_pnl"] - (-0.877023)) < 1e-6
    assert t["opened_at"].startswith("2026-07-29T") and t["closed_at"].startswith("2026-07-29T17:21")
    assert t["recovered"] is True and t["strategy_id"] == "ai_trader" and t["actions_count"] == 3
    assert "entry" not in t          # kein Signal -> keine erfundenen Levels


def test_rebuild_trade_with_signal_levels_and_manual_source():
    sig = {"id": "s1", "entry_price": 100.0, "stop_loss": 99.0, "take_profit_1": 101.0,
           "take_profit_full": 102.0, "ai_confidence": 70, "ai_setup": "breakout"}
    acts = [dict(a, source="user") for a in ACTIONS]
    t = dr.rebuild_trade("BTCUSDT-1785345367568", acts, sig)
    assert t["entry"] == 100.0 and t["sl"] == 99.0 and t["tp1"] == 101.0 and t["tpf"] == 102.0
    assert t["signal_id"] == "s1" and t["setup"] == "breakout"
    assert t["manual_trade"] is True and t["strategy_id"] == "manual"


def test_rebuild_trade_requires_close():
    assert dr.rebuild_trade("X-1", ACTIONS[:2]) is None
    assert dr.trade_open_ms("BTCUSDT-1785345367568") == 1785345367568
    assert dr.trade_open_ms("kaputt") is None


def test_rebuild_skips_manual_seconds_trades_as_test_artifacts():
    # manuell per API geöffnet und 6 s später geschlossen (Test-Lauf 05.09.) -> kein Trade
    acts = [{"trade_id": "BTCUSDT-1788641797521", "symbol": "BTCUSDT", "side": "LONG", "mode": "paper",
             "action": "close", "source": "manual", "ts": "2026-09-05T20:56:44+00:00",
             "result": {"result": "loss", "realized_pnl": -0.07}}]
    assert dr.rebuild_trade("BTCUSDT-1788641797521", acts) is None
    # dieselbe Dauer per KI ('ki') bleibt erhalten
    assert dr.rebuild_trade("BTCUSDT-1788641797521", [dict(acts[0], source="ki")]) is not None
    assert dr.is_test_like_duration("2026-09-05T20:56:37+00:00", "2026-09-05T20:56:44+00:00")
    assert not dr.is_test_like_duration("2026-09-05T20:56:37+00:00", "2026-09-05T21:56:44+00:00")


# ---------------------------------------------------------------- Fake-DB für async Pfade
class _Cursor:
    def __init__(self, rows):
        self.rows = rows

    def sort(self, *a, **kw):
        return self

    def skip(self, n):
        self.rows = self.rows[n:]
        return self

    def limit(self, n):
        self.rows = self.rows[:n]
        return self

    async def to_list(self, n=None):
        return list(self.rows)

    def __aiter__(self):
        self._it = iter(self.rows)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Coll:
    def __init__(self, rows=None):
        self.rows = [dict(r) for r in (rows or [])]

    def _match(self, q):
        def ok(r):
            for k, v in (q or {}).items():
                if isinstance(v, dict):
                    if "$regex" in v:
                        import re as _re
                        flags = _re.I if "i" in str(v.get("$options", "")) else 0
                        if not _re.search(v["$regex"], str(r.get(k, "")), flags):
                            return False
                    if "$in" in v and r.get(k) not in v["$in"]:
                        return False
                    if "$ne" in v and r.get(k) == v["$ne"]:
                        return False
                    if "$gte" in v and not (str(r.get(k, "")) >= v["$gte"]):
                        return False
                    if "$lte" in v and not (str(r.get(k, "")) <= v["$lte"]):
                        return False
                elif r.get(k) != v:
                    return False
            return True
        return [r for r in self.rows if ok(r)]

    def find(self, q=None, proj=None):
        return _Cursor(self._match(q))

    async def find_one(self, q=None, proj=None):
        m = self._match(q)
        return dict(m[0]) if m else None

    async def insert_one(self, d):
        self.rows.append(dict(d))

    async def insert_many(self, ds):
        self.rows.extend(dict(d) for d in ds)

    async def delete_many(self, q):
        before = len(self.rows)
        self.rows = [r for r in self.rows if r not in self._match(q)]

        class R:
            deleted_count = before - len(self.rows)
        return R()

    async def delete_one(self, q):
        m = self._match(q)
        if m:
            self.rows.remove(m[0])

        class R:
            deleted_count = 1 if m else 0
        return R()

    async def update_one(self, q, upd, upsert=False):
        m = self._match(q)
        if not m and upsert:
            self.rows.append(dict(q))
            m = self._match(q)
        if not m:
            return
        row = m[0]
        for k, v in (upd.get("$set") or {}).items():
            row[k] = v
        for k, spec in (upd.get("$push") or {}).items():
            arr = list(row.get(k) or []) + list(spec.get("$each") or [])
            sl = spec.get("$slice")
            row[k] = arr[sl:] if sl else arr


class _DB:
    def __init__(self):
        self.auto_trades = _Coll()
        self.auto_trades_trash = _Coll()
        self.settings = _Coll()
        self.ai_lessons_history = _Coll()

    def __getitem__(self, name):
        return getattr(self, name)


# ---------------------------------------------------------------- Papierkorb
def test_trade_trash_archive_and_restore():
    from services import trade_trash
    db = _DB()
    asyncio.run(db.auto_trades.insert_many([
        {"id": "a", "mode": "paper", "strategy_id": "ai_trader"},
        {"id": "b", "mode": "live", "strategy_id": "ai_trader"},
        {"id": "c", "mode": "paper", "strategy_id": "x"}]))
    res = asyncio.run(trade_trash.archive(db, {"strategy_id": "ai_trader", "mode": {"$ne": "live"}},
                                          "ai_trader_reset"))
    assert res["count"] == 1 and res["batch_id"]
    assert {r["id"] for r in db.auto_trades.rows} == {"b", "c"}
    batches = asyncio.run(trade_trash.list_batches(db))
    assert batches[0]["count"] == 1 and batches[0]["reason"] == "ai_trader_reset"
    out = asyncio.run(trade_trash.restore(db, res["batch_id"]))
    assert out["restored"] == 1 and {r["id"] for r in db.auto_trades.rows} == {"a", "b", "c"}
    assert asyncio.run(trade_trash.restore(db, res["batch_id"])) is None
    # leerer Filter-Treffer -> kein Batch
    assert asyncio.run(trade_trash.archive(db, {"id": "zzz"}, "x"))["count"] == 0


# ---------------------------------------------------------------- MasterPrompt-History / Restore
def test_master_prompt_history_keep_and_restore():
    db = _DB()
    mp = MasterPromptStore()
    mp.setup(db)
    asyncio.run(mp.load())
    asyncio.run(mp.save(text=DEFAULT_TEXT + " v2", editor="trader"))          # v2
    asyncio.run(mp.save(text="UI-TEXT-42", lesson_policy="COMBO", editor="trader"))  # v3 (Test-Müll)
    assert mp.text == "UI-TEXT-42"
    snap = asyncio.run(mp.restore(2))
    assert snap and mp.text == DEFAULT_TEXT + " v2" and mp.lesson_policy == DEFAULT_LESSON_POLICY
    assert mp.version == 4
    assert asyncio.run(mp.restore(999)) is None
    assert HISTORY_KEEP >= 50


def test_recovery_run_restores_master_and_cleans_lessons_dry_and_real():
    from services import ai_master_prompt, ai_lessons
    db = _DB()
    ai_master_prompt.master_prompt.setup(db)
    ai_lessons.lesson_store.setup(db)
    asyncio.run(db.settings.insert_one({
        "_id": "ai_master_prompt", "text": "UI-TEXT-42", "lesson_policy": "COMBO-POLICY-42",
        "rules": {"max_leverage": 0}, "version": 122, "editor": "trader",
        "history": [{"version": 102, "text": DEFAULT_TEXT, "lesson_policy": DEFAULT_LESSON_POLICY,
                     "rules": {"max_leverage": 50}},
                    {"version": 121, "text": "UI-TEXT-42", "lesson_policy": "X"}]}))
    asyncio.run(db.settings.insert_one({"_id": "ai_lessons", "lessons": [
        {"id": "l1", "title": "TEST_Lektion_ChatCmd", "detail": "x", "locked": True},
        {"id": "l2", "title": "Stop-Loss-Abstand zu eng", "detail": "1.5 ATR"}]}))
    db.ai_trade_actions = _Coll([dict(a) for a in ACTIONS])
    db.signals = _Coll()
    rep = asyncio.run(dr.run(db, dry_run=True))
    assert rep["master_prompt"]["restored"] and rep["master_prompt"]["from_version"] == 102
    assert rep["lessons"]["removed"] == ["TEST_Lektion_ChatCmd"]
    assert rep["paper_trades"]["recovered"] == 1 and not db.auto_trades.rows   # dry-run schreibt nichts
    rep = asyncio.run(dr.run(db, dry_run=False))
    doc = asyncio.run(db.settings.find_one({"_id": "ai_master_prompt"}))
    assert doc["text"] == DEFAULT_TEXT and doc["rules"]["max_leverage"] == 50
    assert [l["title"] for l in asyncio.run(db.settings.find_one({"_id": "ai_lessons"}))["lessons"]] == ["Stop-Loss-Abstand zu eng"]
    assert len(db.auto_trades.rows) == 1 and db.auto_trades.rows[0]["recovered"]
    # idempotent: zweiter Lauf ändert nichts mehr
    rep2 = asyncio.run(dr.run(db, dry_run=False))
    assert rep2["master_prompt"]["restored"] is False and rep2["paper_trades"]["recovered"] == 0
    assert len(db.auto_trades.rows) == 1


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
