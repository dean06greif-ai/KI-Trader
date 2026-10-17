"""Regression 10/2026: Autopilot-Verlauf (Ampel, Bester je Gruppe, Referenz-Start)
und Retention je Lauf-Art (Autopilot-Läufe wurden von regime_opt verdrängt)."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import regime_autopilot as ap  # noqa: E402
from services import retention  # noqa: E402


def _run(rid, score, tf="1h", syms=("BTCUSDT",), hf1=56.0, phase=8.0, **extra):
    return {"id": rid, "created_at": f"2026-09-{10 + len(rid):02d}T00:00:00+00:00",
            "result": {"kind": "autopilot", "symbols": list(syms), "timeframe": tf, "days": 720,
                       "best": {"score": score, "detector": "kombi",
                                "metrics": {"holdout_reference_f1_pct": hf1, "avg_live_phase_days": phase}},
                       "best_engine_config": {"detector": "kombi", "x": score},
                       "history": [{"engine_config": {"detector": "kombi", "x": score - i}} for i in range(3)],
                       "settings": {"min_phase_days_target": 5, "max_phase_days_target": 15}, **extra}}


def test_rate_result_grades():
    assert ap.rate_result(_run("a", 80)["result"])["grade"] == "top"
    assert ap.rate_result(_run("b", 65, hf1=46)["result"])["grade"] == "good"
    assert ap.rate_result(_run("c", 50, hf1=46, phase=30)["result"])["grade"] == "weak"
    assert ap.rate_result(_run("d", 80, holdout_regressed=True,
                               evidence="insufficient_evidence")["result"])["grade"] == "good"


def test_annotate_marks_best_per_group():
    rows = ap.annotate_runs([_run("a", 70), _run("bb", 75), _run("ccc", 60, tf="4h"),
                             _run("dddd", 40, tf="1d", hf1=30, phase=40)])
    best = {r["id"]: r["best_in_group"] for r in rows}
    # Bester je Gruppe nur, wenn auch gut bewertet (kein ★ für schwache Einzel-Läufe)
    assert best == {"a": False, "bb": True, "ccc": True, "dddd": False}
    assert all("rating" in r for r in rows)


def test_reference_start_uses_best_and_top_variants_cross_timeframe():
    plan = ap.reference_start(_run("a", 70, tf="1h"), {"detector": "ema"})
    assert plan["engine_config"] == {"detector": "kombi", "x": 70}
    assert len(plan["seeds"]) == 4 and plan["seeds"][-1]["source"] == "aktuelle Einstellung"
    assert plan["reference"]["timeframe"] == "1h" and plan["reference"]["run_id"] == "a"


def test_retention_keeps_autopilot_runs_per_kind_and_pinned():
    from motor.motor_asyncio import AsyncIOMotorClient
    url = os.environ.get("MONGO_URL")
    if not url:
        import pytest
        pytest.skip("MONGO_URL fehlt")

    async def go():
        db = AsyncIOMotorClient(url)["test_retention_groups_tmp"]
        await db.regime_lab_runs.delete_many({})
        docs = [{"id": f"opt{i}", "created_at": f"2026-09-20T{i:02d}:00:00", "result": {"kind": "regime_opt"}}
                for i in range(20)]
        docs += [{"id": f"ap{i}", "created_at": f"2026-08-01T{i:02d}:00:00", "result": {"kind": "autopilot"},
                  "pinned": i == 0} for i in range(5)]
        await db.regime_lab_runs.insert_many(docs)
        rule = next(r for r in retention.DEFAULT_POLICY if r["coll"] == "regime_lab_runs")
        rule = {**rule, "keep_by_group": {"autopilot": 3}}
        await retention._sweep_rule(db, rule)
        left = {d["id"] async for d in db.regime_lab_runs.find({}, {"id": 1})}
        await db.client.drop_database("test_retention_groups_tmp")
        return left

    left = asyncio.run(go())
    assert sum(1 for x in left if x.startswith("opt")) == 12
    assert {"ap4", "ap3", "ap2", "ap0"} == {x for x in left if x.startswith("ap")}


def test_retention_pinned_rows_do_not_consume_keep_slots():
    """01.10.2026: importierte/gemerkte Zeilen verdrängen keine echten Autopilot-Läufe."""
    from motor.motor_asyncio import AsyncIOMotorClient
    url = os.environ.get("MONGO_URL")
    if not url:
        import pytest
        pytest.skip("MONGO_URL fehlt")

    async def go():
        db = AsyncIOMotorClient(url)["test_retention_pinned_tmp"]
        await db.regime_lab_runs.delete_many({})
        docs = [{"id": f"ap{i}", "created_at": f"2026-08-01T{i:02d}:00:00", "result": {"kind": "autopilot"},
                 "pinned": i >= 4} for i in range(7)]
        await db.regime_lab_runs.insert_many(docs)
        rule = next(r for r in retention.DEFAULT_POLICY if r["coll"] == "regime_lab_runs")
        await retention._sweep_rule(db, {**rule, "keep_by_group": {"autopilot": 3}})
        left = {d["id"] async for d in db.regime_lab_runs.find({}, {"id": 1})}
        await db.client.drop_database("test_retention_pinned_tmp")
        return left

    assert asyncio.run(go()) == {"ap6", "ap5", "ap4", "ap3", "ap2", "ap1"}
