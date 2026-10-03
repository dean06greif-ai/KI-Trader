"""Audit 2.4: Sicherheitsstatus + Ampel (services/safety_status.py). Ohne Netzwerk."""
import asyncio
import inspect

from services import entry_guard, safety_status as ss


def setup_function(_fn):
    ss.reset_cache()


# ---------------- reine Bausteine ----------------
def test_stale_level_and_overall():
    assert ss.stale_level(None, 15) == "ok"
    assert ss.stale_level(10, 15, 60) == "ok"
    assert ss.stale_level(20, 15, 60) == "warn"
    assert ss.stale_level(90, 15, 60) == "critical"
    assert ss.stale_level(90, 15) == "warn"  # ohne crit nie critical
    assert ss.overall_level([]) == "ok"
    assert ss.overall_level([{"level": "ok"}, {"level": "warn"}]) == "warn"
    assert ss.overall_level([{"level": "warn"}, {"level": "critical"}]) == "critical"


def test_age_minutes():
    assert ss.age_minutes(None) is None
    assert ss.age_minutes("kaputt") is None
    from datetime import datetime, timedelta, timezone
    t = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    assert 29 <= ss.age_minutes(t) <= 31


# ---------------- status() mit Fake-DB ----------------
class _Trades:
    def __init__(self, sl_missing=0, close_failed=0, open_live=0):
        self.sl_missing, self.close_failed, self.open_live = sl_missing, close_failed, open_live

    async def count_documents(self, flt):
        if flt.get("sl_exchange_missing"):
            return self.sl_missing
        if flt.get("live_close_failed"):
            return self.close_failed
        return self.open_live


class _Settings:
    def __init__(self, docs=None):
        self.docs = docs or {}

    async def find_one(self, flt):
        return self.docs.get(flt.get("_id"))

    async def update_one(self, flt, upd, upsert=False):
        d = self.docs.setdefault(flt.get("_id"), {})
        d.update(upd.get("$set", {}))


class _Db:
    def __init__(self, trades, settings_docs=None):
        self.auto_trades = trades
        self.settings = _Settings(settings_docs)


def test_status_ok_without_issues():
    db = _Db(_Trades())
    st = asyncio.run(ss.status(db, force=True))
    assert st["level"] == "ok"
    names = [c["name"] for c in st["checks"]]
    assert {"sl_missing", "close_failed", "sync_stale", "watchdog_stale"} <= set(names)


def test_status_critical_on_sl_missing_and_blocks_entry():
    db = _Db(_Trades(sl_missing=2))
    st = asyncio.run(ss.status(db, force=True))
    assert st["level"] == "critical"
    ok, why = asyncio.run(ss.entry_allowed(db))
    assert not ok and "KRITISCH" in why and "Börsen-SL" in why


def test_status_sync_stale_only_with_open_live_trades():
    from datetime import datetime, timedelta, timezone
    old = (datetime.now(timezone.utc) - timedelta(minutes=90)).isoformat()
    docs = {"bitunix_sync_status": {"last_sync_at": old}}
    # ohne offene Live-Trades: ok
    st = asyncio.run(ss.status(_Db(_Trades(open_live=0), dict(docs)), force=True))
    assert [c for c in st["checks"] if c["name"] == "sync_stale"][0]["level"] == "ok"
    ss.reset_cache()
    # mit offenen Live-Trades: 90 min > 60 min -> critical
    st = asyncio.run(ss.status(_Db(_Trades(open_live=1), dict(docs)), force=True))
    assert [c for c in st["checks"] if c["name"] == "sync_stale"][0]["level"] == "critical"


def test_entry_allowed_respects_disable_and_fail_open():
    db = _Db(_Trades(sl_missing=1),
             {ss.CONFIG_ID: {"block_on_critical": False}})
    ok, why = asyncio.run(ss.entry_allowed(db))
    assert ok and why == ""  # Block abgeschaltet

    class Boom:
        def __getattr__(self, _):
            raise RuntimeError("db weg")
    ss.reset_cache()
    ok, _ = asyncio.run(ss.entry_allowed(Boom()))
    assert ok  # fail-open


def test_entry_guard_wires_safety_status_for_live_only():
    src = inspect.getsource(entry_guard.check_entry)
    assert "safety_status.entry_allowed" in src
    assert 'if mode == "live"' in src
