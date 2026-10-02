"""Regression 10/2026: Werkbank-Pause/Entkopplung, Phasen-Anpassung + Verlauf
dynamischer Strategien, lokaler Backtest dynamischer Strategien, Phasen-Hinweis."""
import asyncio
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from services import dynamic_versions as dv  # noqa: E402
from services import dynamic_workbench as wb  # noqa: E402
from services import job_control  # noqa: E402
from services import local_exec as lx  # noqa: E402
from services import regime_advice  # noqa: E402
from services import regime_lab as lab  # noqa: E402
from services import strategy_release  # noqa: E402


class _Strat:
    def __init__(self, sid, name, dynamic=False):
        self.STRATEGY_ID, self.STRATEGY_NAME, self.IS_DYNAMIC = sid, name, dynamic


class _Reg:
    def __init__(self):
        self.items = {"ema": _Strat("ema", "EMA Cross"), "nnfx": _Strat("nnfx", "NNFX"),
                      "custom_base": _Strat("custom_base", "Basis"), "dyn_x": _Strat("dyn_x", "D", True)}

    def get(self, sid):
        return self.items.get(sid)


def _doc():
    d = {"id": "dyn_test", "name": "Test", "strategy_id": "custom_base", "timeframe": "1h",
         "symbols": ["BTCUSDT"],
         "model": {"regimes": [{"id": 0, "label": "Aufwärts"}, {"id": 1, "label": "Seitwärts"},
                               {"id": 2, "label": "Abwärts"}]},
         "configs": {"0": {"tp1_crv": 2.0}, "1": {"tp1_crv": 1.5}},
         "regime_strategies": {"0": "ema", "1": "custom_base"},
         "regime_params": {"0": {"fast": 9}},
         "sub_strategies": {"1": {"rules": ["rsi<30"], "definition": {"long_rules": [{"a": 1}]}}},
         "fallback_config": {}, "rule_variants": {},
         "settings": {"analysis_id": "ra_1", "skipped_regimes": [2], "skipped_reason": "Walk-Forward negativ"},
         "verdict": {"dynamic_better": True}}
    d["release"] = strategy_release.initial_release(d, d["verdict"])
    return d


# ---------------- Phasen-Anpassung (rein) ----------------
def test_phase_summary_matches_live_resolution():
    rows = {p["regime"]: p for p in dv.phase_summary(_doc(), _Reg())}
    assert rows[0]["traded"] and rows[0]["strategy_name"] == "EMA Cross"
    assert rows[1]["own_rules"] and rows[1]["strategy_name"] == dv.OWN_RULES
    assert rows[2]["traded"] is False


def test_skip_unmaps_only_that_regime():
    f = dv.apply_phase_change(_doc(), 0, "skip", _Reg())
    assert f["regime_strategies"]["0"] is None and f["regime_strategies"]["1"] == "custom_base"
    rows = {p["regime"]: p for p in dv.phase_summary({**_doc(), **f}, _Reg())}
    assert rows[0]["traded"] is False and rows[1]["traded"] is True


def test_strategy_swap_drops_old_rules_and_params():
    f = dv.apply_phase_change(_doc(), 1, "strategy", _Reg(), strategy_id="nnfx")
    assert f["regime_strategies"]["1"] == "nnfx" and "1" not in f["sub_strategies"]
    assert f["configs"]["1"] == {"tp1_crv": 1.5}  # Trade-Parameter bleiben standardmäßig
    f2 = dv.apply_phase_change(_doc(), 0, "strategy", _Reg(), strategy_id="nnfx", reset_trade_params=True)
    assert f2["configs"]["0"] == {} and "0" not in f2["regime_params"]


def test_strategy_swap_rejects_dynamic_or_unknown():
    for sid in ("dyn_x", "nope", "ai_trader"):
        with pytest.raises(ValueError):
            dv.apply_phase_change(_doc(), 0, "strategy", _Reg(), strategy_id=sid)
    with pytest.raises(ValueError):
        dv.apply_phase_change(_doc(), 9, "skip", _Reg())


def test_optimized_reactivates_skipped_regime_with_own_rules():
    opt = {"definition": {"long_rules": [{"b": 2}]}, "rules": ["x"], "trade_params": {"tp1_crv": 3},
           "strategy_params": {}}
    f = dv.apply_phase_change(_doc(), 2, "optimized", _Reg(), optimized=opt)
    assert f["regime_strategies"]["2"] == "custom_base" and f["sub_strategies"]["2"]["rules"] == ["x"]
    assert f["configs"]["2"] == {"tp1_crv": 3}
    with pytest.raises(ValueError):
        dv.apply_phase_change(_doc(), 2, "optimized", _Reg(), optimized=None)


def test_release_becomes_new_revision_unless_kept():
    doc = _doc()
    new = {**doc, **dv.apply_phase_change(doc, 0, "skip", _Reg())}
    rel = dv.next_release(doc, new, keep_release=False, note="x")
    assert strategy_release.effective_status({**new, "release": rel}) == "draft"
    kept = dv.next_release(doc, new, keep_release=True, note="x")
    assert kept["status"] == "approved" and kept["approved_without_evidence"] is True
    assert strategy_release.effective_status({**new, "release": kept}) == "approved"


# ---------------- Verlauf (lokale Test-DB) ----------------
@pytest.fixture
def tmp_db():
    from motor.motor_asyncio import AsyncIOMotorClient
    url = os.environ.get("MONGO_URL") or "mongodb://localhost:27017"
    if "localhost" not in url and "127.0.0.1" not in url:
        pytest.skip("nur gegen lokale Test-DB")
    name = f"test_dynver_{uuid.uuid4().hex[:6]}"
    yield name, url
    asyncio.run(_drop(url, name))


async def _drop(url, name):
    from motor.motor_asyncio import AsyncIOMotorClient
    AsyncIOMotorClient(url).drop_database(name)


def test_change_and_restore_versions(tmp_db):
    name, url = tmp_db

    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(url)[name]
        doc = _doc()
        await db.dynamic_strategies.insert_one(dict(doc))
        r1 = await dv.change_phase(db, doc, _Reg(), 0, "skip")
        assert r1["version"]["version"] == 2  # v1 = Ausgangsstand
        stored = await db.dynamic_strategies.find_one({"id": doc["id"]}, {"_id": 0})
        assert stored["regime_strategies"]["0"] is None
        assert stored["settings"]["phase_overrides"]["0"]["action"] == "skip"
        assert stored["settings"]["analysis_id"] == "ra_1"  # übrige Einstellungen bleiben
        r2 = await dv.restore(db, stored, _Reg(), 1)
        assert r2["version"]["version"] == 3
        back = await db.dynamic_strategies.find_one({"id": doc["id"]}, {"_id": 0})
        assert back["regime_strategies"] == doc["regime_strategies"]
        # alte Freigabe gilt wieder, weil die Definition exakt zurückkommt
        assert strategy_release.effective_status(back) == "validated"
        rows = await dv.list_versions(db, doc["id"])
        assert [r["version"] for r in rows] == [3, 2, 1]
        with pytest.raises(ValueError):
            await dv.restore(db, back, _Reg(), 99)
    asyncio.run(go())


# ---------------- Werkbank: Pause & Entkopplung ----------------
def _wb_job():
    jid = wb.create_job("refine", {"execution": "cloud"})
    return wb.JOBS[jid]


def test_workbench_pause_mirrors_to_sub_job():
    job = _wb_job()
    sub = {"status": "running", "progress": 40, "phase": "Suche", "paused": False}
    assert wb.request_pause(job, True)
    wb._mirror_sub(job, sub)
    assert sub["pause"] is True and job["sub_progress"] == 40
    sub["paused"] = True
    wb._mirror_sub(job, sub)
    assert job["paused"] is True
    assert wb.request_pause(job, False)
    wb._mirror_sub(job, sub)
    assert sub["pause"] is False and job["paused"] is False
    job["status"] = "done"
    assert not wb.request_pause(job, True)


def test_workbench_stop_clears_pause_and_reset_frees_sub_jobs():
    job = _wb_job()
    wb.request_pause(job, True)
    assert wb.request_stop(job) and job["pause"] is False and job["stop"] is True
    ljid = lab.create_job("regime_opt", {"workbench_job": job["id"]})
    assert wb.reset_running() >= 1
    assert job["status"] == "cancelled" and lab.JOBS[ljid]["status"] == "cancelled"


def test_workbench_wait_if_paused_checkpoint():
    job = _wb_job()
    job["pause"] = True

    async def go():
        t = asyncio.create_task(job_control.wait_if_paused(job))
        await asyncio.sleep(0.1)
        assert job["paused"] is True and not t.done()
        wb.request_pause(job, False)
        await asyncio.wait_for(t, 2)
    asyncio.run(go())
    assert job["paused"] is False


def test_regime_lab_active_ignores_workbench_sub_jobs():
    from routers import regime_lab as rl
    for j in lab.JOBS.values():
        j["status"] = "done"
    lab.create_job("regime_opt", {"workbench_job": "wb_x"})
    assert asyncio.run(rl.active_job())["active"] is None
    own = lab.create_job("autopilot", {})
    assert asyncio.run(rl.active_job())["active"]["id"] == own
    for j in lab.JOBS.values():
        j["status"] = "done"


# ---------------- Lokaler Backtest dynamischer Strategien ----------------
@pytest.fixture
def _lx_clean():
    for d in (lx.WORKERS, lx.WAITING_WORKERS, lx.LOCAL_JOBS):
        d.clear()
    lx.COMPUTE_QUEUE.clear()
    yield
    for d in (lx.WORKERS, lx.WAITING_WORKERS, lx.LOCAL_JOBS):
        d.clear()
    lx.COMPUTE_QUEUE.clear()


def test_dynamic_backtest_only_claimed_by_new_worker(_lx_clean):
    from services import backtester as bt
    lx.heartbeat("old", {"name": "old", "version": "1.19.0", "running_jobs": []})
    lx.heartbeat("new", {"name": "new", "version": "1.20.0", "running_jobs": []})
    jid = bt.create_job({"execution": "local"})
    lx.enqueue_compute("backtest", jid, {"kind": "backtest", "args": {"dynamic_docs": {"dyn_a": {}}}})
    assert lx.claim("old") is None
    assert lx.claim("new")["job_id"] == jid
    assert lx.worker_supports((1, 20), "new") and not lx.worker_supports((1, 20), "old")


def test_run_backtest_uses_shipped_dynamic_docs(monkeypatch):
    from services import backtester as bt
    from services import dynamic_backtest
    seen = {}

    async def fake_sim(doc, symbols, days, cfg, settings, registry, job, cancelled, **kw):
        seen["doc"] = doc["id"]
        return {"per_pair": [], "timeframe": "1h", "export_trades": [],
                "breakdown": {"dynamic_id": doc["id"], "regimes": []}}
    monkeypatch.setattr(dynamic_backtest, "simulate_dynamic", fake_sim)

    class Reg:
        def is_dynamic(self, sid):
            return False  # Worker-Registry kennt keine dynamischen Strategien
    jid = bt.create_job({})
    asyncio.run(bt.run_backtest(jid, ["dyn_a"], ["BTCUSDT"], 3, {"max_capital": 100}, Reg(), {}, None,
                                {}, None, None, None, dynamic_docs={"dyn_a": {"id": "dyn_a"}}))
    assert seen["doc"] == "dyn_a"
    assert bt.JOBS[jid]["result"]["dynamic_breakdown"]["dyn_a"]["dynamic_id"] == "dyn_a"


# ---------------- Hinweis „5.0 liegt unter 5“ ----------------
def test_phase_warning_shows_hidden_difference():
    w = regime_advice.result_warnings({"reference_pct": 50, "avg_live_phase_days": 4.96}, 5, 15)
    assert any("4.96" in x for x in w)
    assert regime_advice.fmt_days(3.2, 5) == "3.2"


# ---------------- Varianten je Phase, Linie über Optimierungs-Läufe ----------------
def test_phase_variant_and_variant_action_roundtrip():
    doc = _doc()
    v = dv.phase_variant(dv.snapshot_of(doc), 1)
    assert v["traded"] and v["sub_strategy"]["rules"] == ["rsi<30"] and v["trade_params"] == {"tp1_crv": 1.5}
    skipped = {**doc, **dv.apply_phase_change(doc, 1, "skip", _Reg())}
    back = dv.apply_phase_change(skipped, 1, "variant", _Reg(), variant=v)
    assert back["regime_strategies"]["1"] == "custom_base" and back["sub_strategies"]["1"]["rules"] == ["rsi<30"]
    off = dv.apply_phase_change(doc, 0, "variant", _Reg(), variant={"traded": False})
    assert off["regime_strategies"]["0"] is None


def test_carry_overrides_keeps_user_changes_in_unimproved_phases():
    src = {**_doc(), **dv.apply_phase_change(_doc(), 0, "skip", _Reg())}
    src["settings"] = {**src["settings"], "phase_overrides": {"0": {"action": "skip"}, "1": {"action": "strategy"}}}
    new = _doc()  # frischer Build aus den Analyse-Zuordnungen
    res = dv.carry_overrides(src, new, improved_rids=[1])
    assert res["carried"] == [0]  # Regime 1 wurde verbessert -> neues Ergebnis gilt
    assert res["fields"]["regime_strategies"]["0"] is None and res["swapped"] is False
    assert res["fields"]["settings"]["phase_overrides"] == {"0": {"action": "skip"}}
    assert dv.carry_overrides(_doc(), new, [0])["carried"] == []


def test_phase_history_over_lineage_and_variant_restore(tmp_db):
    name, url = tmp_db

    async def go():
        from motor.motor_asyncio import AsyncIOMotorClient
        db = AsyncIOMotorClient(url)[name]
        parent = _doc()
        await db.dynamic_strategies.insert_one(dict(parent))
        await dv.change_phase(db, parent, _Reg(), 0, "strategy", strategy_id="nnfx")
        parent = await db.dynamic_strategies.find_one({"id": parent["id"]}, {"_id": 0})
        child = {**_doc(), "id": "dyn_child", "name": "Kind"}
        await db.dynamic_strategies.insert_one(dict(child))
        out = await dv.after_refine(db, _Reg(), parent["id"], "dyn_child", improved_rids=[1])
        assert out["carried"] == [0]
        child = await db.dynamic_strategies.find_one({"id": "dyn_child"}, {"_id": 0})
        assert child["settings"]["refined_from"] == parent["id"]
        assert child["regime_strategies"]["0"] == "nnfx"  # Anpassung übernommen
        assert strategy_release.effective_status(child) == "draft"  # Tausch nicht WF-geprüft
        hist = await dv.phase_history(db, child, 0, _Reg())
        names = [h["strategy_name"] for h in hist]
        assert hist[0]["current"] and names[0] == "NNFX" and "EMA Cross" in names
        old = next(h for h in hist if h["strategy_name"] == "EMA Cross")
        var = await dv.variant_from(db, child, old["dynamic_id"], old["version"], 0)
        res = await dv.change_phase(db, child, _Reg(), 0, "variant", variant=var)
        assert res["doc"]["regime_strategies"]["0"] == "ema"
        with pytest.raises(ValueError):
            await dv.variant_from(db, child, "fremd", None, 0)
    asyncio.run(go())
