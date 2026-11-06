"""Regressionstests Verbesserungen 06/26:
1) Tiefenanalyst-Token-Optimierung (Severity-Routing + Tages-Limit)
2) Neustart-Resilienz lokaler Jobs (db.local_jobs Restore)
3) RAM-Schutz: export_trades-Pruning in create_job
"""
import asyncio
import time

import pytest


# ---------- 1) Tiefenanalyst ----------
class TestDeepDepthRouting:
    def test_depth_for_severity(self):
        from services.ai_news_watcher import deep_depth_for
        assert deep_depth_for("high") == "full"
        assert deep_depth_for("medium") == "update"
        assert deep_depth_for("low") == "update"

    def test_should_trigger_deep_unchanged(self):
        from services.ai_news_watcher import should_trigger_deep
        now = time.time()
        assert should_trigger_deep("high", True, 0, now)
        assert should_trigger_deep("medium", True, 0, now)
        assert not should_trigger_deep("low", True, 0, now)
        assert not should_trigger_deep("high", True, now - 60, now)  # Cooldown

    def test_daily_cap(self):
        from services.ai_news_watcher import NewsWatcher, DEEP_NEWS_DAILY_CAP
        w = NewsWatcher()
        for _ in range(DEEP_NEWS_DAILY_CAP):
            assert w._deep_daily_ok()
            w._deep_news_count += 1
        assert not w._deep_daily_ok()

    def test_deep_update_system_exists(self):
        from services.ai_engine import DEEP_UPDATE_SYSTEM, DEEP_ANALYSIS_SYSTEM
        assert "KOMPAKTES UPDATE" in DEEP_UPDATE_SYSTEM
        assert len(DEEP_UPDATE_SYSTEM) < len(DEEP_ANALYSIS_SYSTEM) + 500

    def test_run_deep_analysis_accepts_depth(self):
        import inspect
        from services.ai_engine import AIEngine
        sig = inspect.signature(AIEngine.run_deep_analysis)
        assert "depth" in sig.parameters
        assert sig.parameters["depth"].default == "full"


# ---------- 2) Neustart-Resilienz ----------
class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def limit(self, n):
        return self

    def __aiter__(self):
        self._it = iter(self._docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _FakeColl:
    def __init__(self, docs=None):
        self.docs = {d["_id"]: d for d in (docs or [])}

    async def find_one(self, q):
        return self.docs.get(q.get("_id"))

    def find(self, q=None):
        return _FakeCursor(list(self.docs.values()))

    async def update_one(self, q, u, upsert=False):
        return None

    async def delete_one(self, q):
        self.docs.pop(q.get("_id"), None)
        return None


class _FakeDB:
    def __init__(self, docs=None):
        self.local_jobs = _FakeColl(docs)


class TestLocalJobRestore:
    def test_restore_job_recreates_optimizer_job(self):
        from services import local_exec
        from services import optimizer as opt
        db = _FakeDB([{"_id": "rj1", "kind": "optimizer",
                       "params": {"mode": "params"}, "progress": 33,
                       "created_at": "2026-06-01T00:00:00+00:00"}])
        opt.JOBS.pop("rj1", None)
        local_exec.LOCAL_JOBS.pop("rj1", None)
        meta = asyncio.run(
            local_exec.restore_job(db, "rj1"))
        assert meta is not None
        job = opt.JOBS.get("rj1")
        assert job and job["status"] == "running" and job["progress"] == 33
        assert job["execution"] == "local"
        # cleanup
        opt.JOBS.pop("rj1", None)
        local_exec.LOCAL_JOBS.pop("rj1", None)

    def test_restore_unknown_returns_none(self):
        from services import local_exec
        meta = asyncio.run(
            local_exec.restore_job(_FakeDB(), "nope"))
        assert meta is None

    def test_apply_progress_restores_instead_of_cancel(self):
        from services import local_exec
        from services import optimizer as opt
        db = _FakeDB([{"_id": "rj2", "kind": "optimizer", "params": {},
                       "created_at": "2026-06-01T00:00:00+00:00"}])
        opt.JOBS.pop("rj2", None)
        local_exec.LOCAL_JOBS.pop("rj2", None)
        resp = asyncio.run(
            local_exec.apply_progress("rj2", {"progress": 50, "phase": "x"}, db))
        assert resp["cancel"] is False  # vorher: True -> Worker brach ab!
        assert opt.JOBS["rj2"]["progress"] == 50
        opt.JOBS.pop("rj2", None)
        local_exec.LOCAL_JOBS.pop("rj2", None)

    def test_apply_progress_unknown_job_cancels(self):
        from services import local_exec
        resp = asyncio.run(
            local_exec.apply_progress("ghost99", {"progress": 1}, _FakeDB()))
        assert resp["cancel"] is True

    def test_restore_running_jobs_skips_stale(self):
        from services import local_exec
        from services import optimizer as opt
        db = _FakeDB([{"_id": "old1", "kind": "optimizer", "params": {},
                       "created_at": "2020-01-01T00:00:00+00:00",
                       "updated_at": "2020-01-01T00:00:00+00:00"}])
        n = asyncio.run(
            local_exec.restore_running_jobs(db, "optimizer"))
        assert n == 0
        assert "old1" not in opt.JOBS


# ---------- 3) RAM-Schutz ----------
class TestRamPruning:
    def test_optimizer_create_job_prunes_export_trades(self):
        from services import optimizer as opt
        opt.JOBS["oldjob"] = {"id": "oldjob", "status": "done",
                              "created_at": "2026-01-01",
                              "export_trades": [{"pnl": 1}] * 10}
        jid = opt.create_job({"mode": "params"})
        assert "export_trades" not in opt.JOBS["oldjob"]
        opt.JOBS.pop(jid, None)
        opt.JOBS.pop("oldjob", None)

    def test_backtester_create_job_prunes_exports(self):
        from services import backtester as bt
        bt.JOBS["oldbt"] = {"id": "oldbt", "status": "done",
                            "created_at": "2026-01-01",
                            "export_trades": [{"pnl": 1}] * 10,
                            "export_candles": {"BTCUSDT|1m": [{}]}}
        jid = bt.create_job({})
        assert "export_trades" not in bt.JOBS["oldbt"]
        assert "export_candles" not in bt.JOBS["oldbt"]
        bt.JOBS.pop(jid, None)
        bt.JOBS.pop("oldbt", None)


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
