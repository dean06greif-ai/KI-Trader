"""Bestehende Regime (gespeicherte Analysen) -> Autopilot-Verlauf (services/regime_history_import.py)."""
import asyncio

from services import regime_autopilot as ap
from services import regime_history_import as imp


def _entry(direction=90.0, holdout=88.0, inner=91.0, f1=55.0, bars=1000, hbars=250):
    return {"live_agreement": {"direction_pct": direction, "holdout_direction_pct": holdout,
                               "inner_direction_pct": inner, "trend_hit_pct": 60.0,
                               "bars": bars, "holdout_bars": hbars},
            "reference": {"holdout_f1_pct": f1, "train_f1_pct": f1 + 2, "inner_f1_pct": f1 + 1},
            "live_segments": [{"bars": 24 * 6}, {"bars": 24 * 8}]}


def _analysis(aid="a1", engine="v2", detector="ema", per_symbol=None, **kw):
    return {"id": aid, "name": f"Analyse {aid}", "symbols": ["BTCUSDT", "ETHUSDT"],
            "timeframe": "1h", "days": 360, "created_at": "2026-09-20T10:00:00+00:00",
            "settings": {"engine": engine, "train_pct": 75,
                         "engine_config": {"version": "v2", "detector": detector, "kombi_thr": 0.1}},
            "combined": {"per_symbol": per_symbol if per_symbol is not None
                         else {"BTCUSDT": _entry(), "ETHUSDT": _entry(80.0)}}, **kw}


def test_analysis_to_run_has_autopilot_shape_and_same_metrics():
    run = imp.analysis_to_run(_analysis())
    assert run["id"] == "imp_a1" and run["pinned"] is True
    res = run["result"]
    assert res["kind"] == "autopilot" and res["source"] == "import"
    assert {k: res["imported_from"][k] for k in ("type", "id", "name")} == \
        {"type": "analysis", "id": "a1", "name": "Analyse a1"}
    assert "grade" in res["imported_from"] and "regimes" in res["imported_from"]
    assert res["best_engine_config"]["detector"] == "ema"
    assert res["timeframe"] == "1h" and res["symbols"] == ["BTCUSDT", "ETHUSDT"]
    # Kennzahlen exakt wie der Autopilot sie rechnet
    acc = ap.MetricsAccumulator("1h")
    acc.add(_entry())
    acc.add(_entry(80.0))
    assert res["best"]["metrics"] == acc.result()
    assert res["best"]["score"] == ap.score_metrics(acc.result(), 5.0, 15.0)
    assert res["improved"] is False            # nie automatisch übernehmen
    assert res["best"]["metrics"]["avg_live_phase_days"] == 7.0
    # Verlauf-Ampel funktioniert mit der importierten Zeile
    assert ap.rate_result(res)["grade"] in ("top", "good", "mid", "weak")
    # Referenz-Start (Weitersuchen) funktioniert
    assert ap.reference_start(run, None)["engine_config"]["detector"] == "ema"


def test_analysis_to_run_skips_without_live_view():
    assert imp.analysis_to_run(_analysis(engine="kmeans")) is None
    assert imp.analysis_to_run(_analysis(detector="regression")) is None
    assert imp.analysis_to_run(_analysis(per_symbol={})) is None
    assert imp.analysis_to_run(_analysis(per_symbol={"BTCUSDT": {"segments": []}})) is None


class _Cursor:
    def __init__(self, docs):
        self.docs = list(docs)

    def sort(self, *a, **k):
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    async def to_list(self, n=None):
        return list(self.docs)

    def __aiter__(self):
        self._it = iter(self.docs)
        return self

    async def __anext__(self):
        try:
            return next(self._it)
        except StopIteration:
            raise StopAsyncIteration


class _Runs:
    def __init__(self):
        self.docs = {}

    def find(self, q, proj=None):
        return _Cursor([{"id": k, "result": v.get("result", {}), "pinned": v.get("pinned")}
                        for k, v in self.docs.items()])

    async def update_one(self, flt, upd, upsert=False):
        self.docs.setdefault(flt["id"], upd["$setOnInsert"])


class _Analyses:
    def __init__(self, docs):
        self.docs = docs

    def find(self, q, proj=None):
        return _Cursor(self.docs)


class _Db:
    def __init__(self, analyses):
        self.regime_lab_runs = _Runs()
        self.regime_analyses = _Analyses(analyses)


def test_import_existing_is_idempotent_and_reports_counts():
    db = _Db([_analysis("a1"), _analysis("a2", detector="kombi"), _analysis("a3", engine="kmeans")])
    first = asyncio.run(imp.import_existing(db))
    assert first["imported"] == 2 and first["skipped"] == 1 and first["already"] == 0
    assert set(db.regime_lab_runs.docs) == {"imp_a1", "imp_a2"}
    second = asyncio.run(imp.import_existing(db))
    assert second["imported"] == 0 and second["already"] == 2


def test_calibration_history_ignores_imported_rows():
    import inspect
    from services import regime_calibration_history as h
    assert '"result.source": {"$ne": "import"}' in inspect.getsource(h.list_history)


def test_router_exposes_import_endpoint():
    from routers import regime_lab as rl
    paths = {r.path for r in rl.router.routes}
    assert "/api/regime-lab/autopilot/runs/import" in paths


def test_import_skips_followup_analysis_of_pinned_autopilot_run():
    db = _Db([_analysis("a1")])
    res = imp.analysis_to_run(_analysis("a1"))["result"]
    db.regime_lab_runs.docs["run1"] = {"result": {**res, "source": None}, "pinned": True}
    out = asyncio.run(imp.import_existing(db))
    assert out["imported"] == 0 and out["already"] == 1
    assert "imp_a1" not in db.regime_lab_runs.docs


def test_import_keeps_analysis_when_matching_run_is_not_pinned():
    # 10/2026: ungemerkte Autopilot-Läufe löscht die Bereinigung -> Analyse trotzdem sichern
    db = _Db([_analysis("a1")])
    res = imp.analysis_to_run(_analysis("a1"))["result"]
    db.regime_lab_runs.docs["run1"] = {"result": {**res, "source": None}}
    out = asyncio.run(imp.import_existing(db))
    assert out["imported"] == 1 and "imp_a1" in db.regime_lab_runs.docs


def test_import_default_engine_config_and_per_coin_scope():
    doc = _analysis("old")
    doc["settings"]["engine_config"] = {}
    run = imp.analysis_to_run(doc)
    assert run and run["result"]["best_engine_config"]["detector"] == ap.eng.DEFAULT_CONFIG.get("detector")
    pc = _analysis("pc", per_symbol={})
    pc["per_coin"] = {"BTCUSDT": _entry(), "ETHUSDT": {"error": "x"}}
    assert imp.analysis_to_run(pc)["result"]["best"]["metrics"]
