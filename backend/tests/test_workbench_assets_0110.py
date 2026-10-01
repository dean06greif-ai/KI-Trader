"""Dynamik-Werkbank: Asset-Teilmenge, Kerzen-Wiederverwendung, Plateau-/Fehler-Steuerung."""
import asyncio

from services import dynamic_workbench as wb
from services import regime_lab as lab
from services import regime_opt

ALL = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]


def test_norm_subset_and_keys():
    assert lab.norm_subset(ALL, None) is None
    assert lab.norm_subset(ALL, ALL) is None
    assert lab.norm_subset(ALL, ["ETHUSDT", "BTCUSDT", "XXX"]) == ["BTCUSDT", "ETHUSDT"]
    assert lab.norm_subset(ALL, ["XXX"]) is None
    assert lab.area_key("combined", None, ["ETHUSDT", "BTCUSDT"]) == "combined@BTCUSDT+ETHUSDT"
    assert lab.area_key("combined", None, None) == "combined"
    assert lab.area_key("per_coin", "BTCUSDT", ["ETHUSDT"]) == "per_coin:BTCUSDT"
    assert lab.area_fields(None) == ("assignments", "walkforward")
    assert lab.area_fields(["BTCUSDT"]) == ("subset_assignments", "subset_walkforward")


def test_dataset_for_limits_manifest():
    ds = {"version": 1, "per_symbol": {s: {"bars": 1} for s in ALL}}
    assert set(lab.dataset_for(ds, ["BTCUSDT"])["per_symbol"]) == {"BTCUSDT"}
    assert lab.dataset_for(None, ["BTCUSDT"]) is None


def test_assignment_items_reads_subset_field():
    doc = {"assignments": {"combined:0": {"a": 1}},
           "subset_assignments": {"combined@BTCUSDT:1": {"b": 2}}}
    assert list(regime_opt._assignment_items(doc, "combined", None)) == [0]
    assert list(regime_opt._assignment_items(doc, "combined", None, ["BTCUSDT"])) == [1]


def test_history_slot_reuse_and_subset_manifest(monkeypatch):
    calls = []

    async def fake_fetch(syms, days, tf, job, end_ts=None, start_ts=None, dataset=None, **_k):
        calls.append({"syms": list(syms), "ds": sorted((dataset or {}).get("per_symbol") or {})})
        return {s: [{"timestamp": i} for i in range(5)] for s in syms}

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    regime_opt.clear_history_slot()
    doc = {"id": "ra1", "days": 30, "timeframe": "1h", "symbols": ALL,
           "dataset": {"per_symbol": {s: {"bars": 5} for s in ALL}}, "bounds": {}}
    syms = regime_opt.area_symbols(doc, "combined", None, ["BTCUSDT"])
    job = {}
    h1 = asyncio.run(regime_opt._load_area_histories(doc, syms, "1h", job, "wb_1"))
    h2 = asyncio.run(regime_opt._load_area_histories(doc, syms, "1h", job, "wb_1"))
    assert h1 is h2 and len(calls) == 1
    assert calls[0] == {"syms": ["BTCUSDT"], "ds": ["BTCUSDT"]}
    asyncio.run(regime_opt._load_area_histories(doc, syms, "1h", job, None))
    assert len(calls) == 2  # ohne reuse_key: unverändertes Verhalten
    regime_opt.clear_history_slot("anderer")
    assert regime_opt._HISTORY_SLOT  # fremder Schlüssel räumt nicht
    regime_opt.clear_history_slot("wb_1")
    assert not regime_opt._HISTORY_SLOT


def test_plateau_helpers():
    assert wb.regime_should_run({"stale": 0}, 3)
    assert not wb.regime_should_run({"stale": 2}, 3)
    assert wb.regime_should_run({"stale": 2}, 4)
    assert not wb.regime_should_run({"dead": True}, 4)
    assert wb.all_exhausted({0: {"stale": 2}, 1: {"dead": True}})
    assert not wb.all_exhausted({0: {"stale": 1}})
    assert wb.is_no_data_error("Keine Kerzen-Abschnitte für dieses Regime im Trainingsbereich gefunden")


class _Coll:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, q, proj=None):
        return dict(self.doc)

    async def update_one(self, q, upd):
        self.doc.update(upd["$set"])


class _DB:
    def __init__(self, doc):
        self.regime_analyses = _Coll(doc)


def test_run_subset_plateau_and_no_data_regime(monkeypatch):
    analysis = {"id": "ra1", "symbols": ALL, "days": 30, "timeframe": "1h", "settings": {"train_pct": 100},
                "combined": {"model": {"regimes": [{"id": 0, "label": "Bulle"}, {"id": 1, "label": "Bär"}]}}}
    db = _DB(analysis)
    calls = {0: 0, 1: 0}
    bodies = []

    async def fake_run_lab(job, kind_fn, body, analysis_doc, deps):
        rid = body["regime_id"]
        calls[rid] += 1
        bodies.append(body)
        if rid == 1:
            raise RuntimeError("Keine Kerzen-Abschnitte für dieses Regime im Trainingsbereich gefunden")
        score = 10.0 if calls[0] == 1 else 5.0
        return {"mode": "discovery", "definition": {"rules": []},
                "top5": [{"score": score, "validation_passed": False, "metrics": {"pnl": 1, "trades": 20}}]}

    built = {}

    async def fake_build(aid, body):
        built.update(body)
        return {"id": "dyn_test"}

    monkeypatch.setattr(wb, "_run_lab", fake_run_lab)
    p = {"analysis_id": "ra1", "scope": "combined", "symbols": ["BTCUSDT"], "rounds": 6,
         "walkforward": False, "result_backtest": False,
         "targets": {"0": {"mode": "discovery"}, "1": {"mode": "discovery"}}}
    jid = wb.create_job("discover", p)
    asyncio.run(wb.run(jid, p, {"db": db, "registry": None, "build": fake_build}))
    job = wb.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    assert calls == {0: 3, 1: 1}  # Plateau nach 2 Runden ohne Verbesserung, Regime 1 ohne Daten
    assert all(b["symbols"] == ["BTCUSDT"] and b["reuse_key"] == jid for b in bodies)
    assert "combined@BTCUSDT:0" in analysis["subset_assignments"]
    assert "assignments" not in analysis  # Gesamt-Menge unberührt
    assert built["symbols"] == ["BTCUSDT"]
    assert job["result"]["subset"] is True and job["result"]["symbols"] == ["BTCUSDT"]
    assert job["regimes"]["1"]["note"] == "keine Daten auf den gewählten Assets"
