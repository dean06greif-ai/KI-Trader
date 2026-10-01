"""Regime-Lab Klarheit (Kalibrierung + Erkennungs-Qualität) – Regressionstests.

Hintergrund: „Wissenschaftlich kalibrieren“ durchlief bisher IMMER den
Suchraum des Regressions-Detektors. Mit den Standard-Detektoren reactive/ema/
kombi änderte sich dadurch nichts an den Labels (wirkungslos), und die
übernommene best_config verlor den gewählten Detektor. Diese Tests sichern:

- Suchraum je Detektor enthält nur Parameter, die der Detektor nutzt
  (eng.CONFIG_GROUPS), plus die gemeinsamen Wechsel-/Phasen-Parameter
- Regressions-Detektor: Suchraum unverändert (Rückwärtskompatibilität)
- best_config enthält den Detektor; Bericht enthält tuned_keys/changes/improved
- Kalibrierung verbessert (oder hält) den Score bei reactive/ema/kombi
- regime_quality: Pooling je Anlageklasse, Noten-Schwellen, Holdout-Basis
- status-Fallback nach Neustart liefert Kalibrierungen/Läufe statt 404
- GET /api/regime-lab/calibrations existiert und liegt VOR der {aid}-Route
"""
import asyncio

import numpy as np
import pytest

from services import regime_engine as eng
from services import regime_quality as rq
from services import regime_truth as rt


# ------------------------------ Helpers ---------------------------------------
def make_candles(closes, ts0=1_700_000_000_000, step_ms=3_600_000, noise=0.004, seed=7):
    rng = np.random.default_rng(seed)
    out = []
    for i, c in enumerate(closes):
        c = float(c) * (1.0 + rng.normal(0, noise))
        out.append({"timestamp": ts0 + i * step_ms, "open": c * 0.999,
                    "high": c * 1.004, "low": c * 0.996, "close": c, "volume": 100.0})
    return out


def synth_closes(segments, start=100.0, bars_per_day=24):
    closes = [start]
    for days, drift in segments:
        per_bar = (1.0 + drift / 100.0) ** (1.0 / bars_per_day)
        for _ in range(int(days * bars_per_day)):
            closes.append(closes[-1] * per_bar)
    return closes


SEGMENTS = [(20, 1.2), (15, 0.0), (20, -1.1), (10, 0.0), (25, 0.9),
            (15, -0.8), (15, 0.0), (20, 1.0)]


@pytest.fixture(scope="module")
def histories():
    return {"BTCUSDT": make_candles(synth_closes(SEGMENTS))}


def _base(detector, n=3400):
    return eng.resolve_config({"detector": detector, "regime_mode": 3}, "1h", n)


COMMON = {"min_hold_days", "min_phase_days"}


# ------------------------------ Suchraum je Detektor ---------------------------
class TestCalibrationGrid:
    @pytest.mark.parametrize("detector", ["reactive", "ema", "kombi", "regression"])
    def test_grid_keys_belong_to_detector(self, detector):
        grid = rt.calibration_grid(detector, _base(detector), 140.0)
        assert grid, "Suchraum darf nicht leer sein"
        for key in grid:
            if key in COMMON:
                continue
            group = eng.CONFIG_GROUPS.get(key)
            assert group is not None, f"{key} ist keine bekannte Engine-Einstellung"
            assert group["detectors"] is None or detector in group["detectors"], \
                f"{key} wirkt nicht im Detektor {detector}"

    def test_regression_grid_unchanged(self):
        grid = rt.calibration_grid("regression", _base("regression"), 140.0)
        assert set(grid) == {"smooth_days", "trend_t", "hysteresis", "confirm_days",
                             "min_hold_days", "adx_min", "confidence_min"}

    def test_reactive_grid_has_no_regression_only_keys(self):
        grid = rt.calibration_grid("reactive", _base("reactive"), 140.0)
        assert not ({"trend_t", "adx_min", "hysteresis", "confirm_days",
                     "confidence_min", "smooth_days"} & set(grid))
        assert {"rev_atr_mult", "persist_candles"} <= set(grid)

    def test_grid_values_sorted_unique_and_contain_base(self):
        base = _base("ema")
        grid = rt.calibration_grid("ema", base, 140.0)
        for key, vals in grid.items():
            assert vals == sorted(set(vals))
            assert any(abs(v - float(base[key])) < 1e-6 or key == "min_hold_days" for v in vals)


# ------------------------------ Kalibrierung End-to-End ------------------------
class TestCalibrateDetectorAware:
    @pytest.mark.parametrize("detector", ["reactive", "ema", "kombi"])
    def test_calibrate_tunes_detector_and_keeps_it(self, histories, detector):
        rep = rt.calibrate(histories, "1h", {"detector": detector, "regime_mode": 3}, "centered")
        assert rep is not None
        assert rep["detector"] == detector
        assert rep["best_config"]["detector"] == detector, "Detektor darf beim Übernehmen nicht verloren gehen"
        n = len(histories["BTCUSDT"])
        assert set(rep["tuned_keys"]) == set(rt.calibration_grid(detector, _base(detector, n), n / 24.0))
        assert isinstance(rep["changes"], list) and "improved" in rep
        assert rep["best"]["score"] >= rep["baseline"]["score"] - 1e-6
        for c in rep["changes"]:
            assert c["key"] in rep["tuned_keys"]
            assert rep["best_config"][c["key"]] == c["to"]

    def test_calibrate_improves_reactive_on_clear_phases(self, histories):
        # Vorher wirkungslos (identischer Score) – jetzt muss sich der Score
        # mit klaren synthetischen Phasen spürbar verbessern.
        rep = rt.calibrate(histories, "1h", {"detector": "reactive", "regime_mode": 3}, "centered")
        assert rep["improved"] is True
        assert rep["best"]["balanced_direction_pct"] > rep["baseline"]["balanced_direction_pct"]

    def test_calibrate_regression_backward_compatible(self, histories):
        rep = rt.calibrate(histories, "1h", {"detector": "regression", "regime_mode": 3}, "centered")
        assert rep["detector"] == "regression"
        assert {"trend_t", "adx_min", "hysteresis"} <= set(rep["best_config"])
        n = len(histories["BTCUSDT"])
        grid = rt.calibration_grid("regression", _base("regression", n), n / 24.0)
        assert rep["evals"] == 2 * sum(len(v) for v in grid.values())

    def test_best_config_resolves_cleanly(self, histories):
        rep = rt.calibrate(histories, "1h", {"detector": "kombi", "regime_mode": 3}, "centered")
        cfg = eng.resolve_config(rep["best_config"], "1h", 3400)
        assert cfg["detector"] == "kombi"
        assert cfg["auto_adapt"] is False

    def test_cancel_returns_none(self, histories):
        assert rt.calibrate(histories, "1h", {"detector": "ema"}, "centered", stop=lambda: True) is None


class TestConfigChanges:
    def test_only_changed_keys(self):
        ch = rt.config_changes({"a": 1.0, "b": 2.0}, {"a": 1.0, "b": 3.0, "c": 4.0})
        assert [c["key"] for c in ch] == ["b", "c"]
        assert ch[0] == {"key": "b", "from": 2.0, "to": 3.0}


# ------------------------------ Erkennungs-Qualität ----------------------------
def _entry(hold, overall, trend=70.0, hbars=500, viol=3.0, seg=9.0, delay=1.2, passed=True):
    return {"live_agreement": {"holdout_direction_pct": hold, "direction_pct": overall,
                               "trend_hit_pct": trend, "holdout_bars": hbars},
            "validation": {"violation_bars_pct": viol, "avg_segment_days": seg, "passed": passed},
            "corrections": {"avg_delay_days": delay}}


class TestRegimeQuality:
    def test_grade_thresholds(self):
        assert rq.grade_of(70.0, 60.0, 500)["grade"] == "gut"
        assert rq.grade_of(55.0, 60.0, 500)["grade"] == "mittel"
        assert rq.grade_of(40.0, 60.0, 500)["grade"] == "schwach"
        assert rq.grade_of(None, None, 0)["grade"] == "unbewertet"

    def test_without_holdout_falls_back_to_overall_with_warning(self):
        g = rq.grade_of(None, 70.0, 0)
        assert g["basis"] == "overall" and g["grade"] == "gut"
        assert "kein belastbarer Holdout" in g["text"]

    def test_thin_holdout_uses_overall(self):
        g = rq.grade_of(90.0, 40.0, 50)
        assert g["basis"] == "overall" and g["grade"] == "schwach"

    def test_pooling_by_asset_class(self):
        q = rq.summarize_scope({"BTCUSDT": _entry(70.0, 65.0), "ETHUSDT": _entry(60.0, 55.0),
                                "EURUSD": _entry(40.0, 45.0)})
        assert set(q["classes"]) == {"crypto", "forex"}
        crypto = q["classes"]["crypto"]
        assert crypto["label"] == "Krypto" and crypto["n_symbols"] == 2
        assert crypto["holdout_direction_pct"] == 65.0 and crypto["grade"] == "gut"
        assert q["classes"]["forex"]["grade"] == "schwach"
        assert q["overall"]["n_symbols"] == 3
        assert q["thresholds"]["good"] == rq.GRADE_GOOD

    def test_summarize_doc_scopes(self):
        doc = {"combined": {"per_symbol": {"BTCUSDT": _entry(66.0, 60.0)}},
               "per_coin": {"BTCUSDT": {**_entry(50.0, 52.0), "model": {}},
                            "SOLUSDT": {"error": "zu wenig Daten"}}}
        q = rq.summarize(doc)
        assert set(q) == {"combined", "per_coin:BTCUSDT"}
        assert q["combined"]["overall"]["grade"] == "gut"
        assert q["per_coin:BTCUSDT"]["overall"]["grade"] == "mittel"

    def test_legacy_doc_without_metrics(self):
        assert rq.summarize({"combined": {"per_symbol": {"BTCUSDT": {"segments": []}}}}) == {}
        assert rq.summarize({}) == {}


# ------------------------------ Router: Status-Fallback + Route-Reihenfolge -----
class _Coll:
    def __init__(self, doc=None):
        self.doc = doc

    async def find_one(self, *_a, **_k):
        return self.doc


class _DB:
    def __init__(self, **colls):
        self.regime_analyses = colls.get("regime_analyses", _Coll())
        self.regime_calibrations = colls.get("regime_calibrations", _Coll())
        self.regime_lab_runs = colls.get("regime_lab_runs", _Coll())


class TestStatusFallback:
    def _status(self, monkeypatch, db, job_id="j1"):
        from core import state
        from routers import regime_lab as router
        monkeypatch.setattr(state, "db", db)
        monkeypatch.setattr(router.lab, "JOBS", {})
        return asyncio.run(router.job_status(job_id))

    def test_calibration_found_after_restart(self, monkeypatch):
        rep = {"best_config": {"detector": "ema"}}
        out = self._status(monkeypatch, _DB(regime_calibrations=_Coll({"id": "j1", "report": rep})))
        assert out["status"] == "done" and out["kind"] == "calibration"
        assert out["result"]["report"] == rep

    def test_lab_run_found_after_restart(self, monkeypatch):
        res = {"kind": "ema_compare", "rows": []}
        out = self._status(monkeypatch, _DB(regime_lab_runs=_Coll({"id": "j1", "result": res})))
        assert out["status"] == "done" and out["result"] == res

    def test_unknown_job_404(self, monkeypatch):
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as ei:
            self._status(monkeypatch, _DB())
        assert ei.value.status_code == 404

    def test_calibrations_route_before_aid_route(self):
        from routers import regime_lab as router
        paths = [r.path for r in router.router.routes]
        assert "/api/regime-lab/calibrations" in paths
        assert paths.index("/api/regime-lab/calibrations") < paths.index("/api/regime-lab/{aid}")
