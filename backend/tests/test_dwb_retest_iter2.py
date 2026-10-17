# Retest iteration_2: match compare-stage fixes (edge_note, test metrics, no_improvement) - mocked lab runs
import asyncio
from services import dynamic_workbench as wb
from tests.test_workbench_dynamic_1206 import _DB, _analysis, _Reg


def _run_match(monkeypatch, pnls, rounds=1, val=None, stage2_worse=True):
    seen = {"n": 0}

    async def fake_run_lab(job, kind_fn, body, analysis_doc, deps):
        sid = body.get("strategy_id")
        seen["n"] += 1
        stage2 = body.get("optimize") != {"tpsl": False}
        pnl = pnls[sid] - (500 if (stage2 and stage2_worse) else 0)
        v = val(sid, pnl) if val else {"pnl": pnl / 2, "trades": 8}
        top = {"score": float(pnl), "validation_passed": v["pnl"] > 0, "trade_sufficient": True,
               "metrics": {"pnl": pnl, "trades": 25}, "validation": v}
        return {"mode": "params", "strategy_id": sid, "strategy_name": sid.upper(), "top5": [top]}

    built = []

    async def fake_build(aid, body):
        built.append(body)
        return {"id": "dyn_m"}

    monkeypatch.setattr(wb, "_run_lab", fake_run_lab)
    p = {"analysis_id": "ra1", "scope": "combined", "rounds": rounds, "iterations": 20, "walkforward": False,
         "result_backtest": False, "targets": {"0": {"mode": "params", "candidates": ["s1", "s2", "s3"]}}}
    jid = wb.create_job("match", p)
    asyncio.run(wb.run(jid, p, {"db": _DB(_analysis()), "registry": _Reg(), "build": fake_build}))
    return wb.JOBS[jid], built


# (d) edge_note only when ALL candidates lose
def test_edge_note_absent_when_one_candidate_profitable(monkeypatch):
    job, _ = _run_match(monkeypatch, {"s1": -5, "s2": 40, "s3": -10})
    assert "edge_note" not in job["regimes"]["0"], job["regimes"]["0"]


def test_edge_note_present_when_all_lose(monkeypatch):
    job, _ = _run_match(monkeypatch, {"s1": -5, "s2": -40, "s3": -10})
    assert "edge_note" in job["regimes"]["0"], job["regimes"]["0"]


# (e) adopted compare winner shows test trades / pnl; comparison preserved after stage 2
def test_adopted_winner_has_test_metrics(monkeypatch):
    job, _ = _run_match(monkeypatch, {"s1": 5, "s2": 50, "s3": -10})
    e = job["regimes"]["0"]
    assert e.get("test_trades") == 8 and e.get("test_pnl") == 25, e
    assert e.get("comparison") and e.get("winner") == "S2"


# (e) rejected (not robust) winner: comparison must survive stage 2, test metrics shown
def test_rejected_winner_keeps_comparison_and_test_metrics(monkeypatch):
    job, built = _run_match(monkeypatch, {"s1": 5, "s2": 50, "s3": -10},
                            val=lambda sid, pnl: {"pnl": -abs(pnl), "trades": 8})
    e = job["regimes"]["0"]
    print("ENTRY", e, "STATUS", job["status"], "RESULT", job.get("result"))
    assert e.get("comparison"), f"comparison lost: {e}"
    assert e.get("test_trades") is not None, f"no test metrics: {e}"


# (c) match where stage 2 improves nothing -> what happens? (report behaviour)
def test_match_stage2_no_improvement_behaviour(monkeypatch):
    job, built = _run_match(monkeypatch, {"s1": 5, "s2": 50, "s3": -10}, stage2_worse=True)
    res = job.get("result") or {}
    log = [x["msg"] for x in job["log"]]
    assert job["status"] == "done", job.get("error")
    assert any("0 Regime verbessert" in m for m in log), log
    # documents current behaviour: compare winner counts as improvement -> strategy built
    print("BUILT", len(built), "NO_IMPR", res.get("no_improvement"))
    assert res.get("no_improvement") or built


# (c) match where all compare winners are non-robust losers and stage2 worse
def test_match_all_losers_no_improvement(monkeypatch):
    job, built = _run_match(monkeypatch, {"s1": -5, "s2": -50, "s3": -10},
                            val=lambda sid, pnl: {"pnl": -abs(pnl), "trades": 8})
    res = job.get("result") or {}
    assert job["status"] == "done", job.get("error")
    assert res.get("no_improvement") is True and not built, f"built={len(built)} res={res.get('dynamic_id')}"
