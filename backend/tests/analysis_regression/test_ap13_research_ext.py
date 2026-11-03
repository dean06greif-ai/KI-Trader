"""AP13-Solltests: Indikator-Ablation, Assetgruppen-Pooling und
Unsicherheitskalibrierung (Zielbild §4/§6B).

Offline: keine DB, kein Netzwerk (fetch_histories wird gefaked).
"""
import asyncio
import inspect
import math

import pytest

from services import regime as rg
from services import regime_engine as eng
from services import regime_lab as lab
from services import research_ablation as ra
from services import research_calibration as rc
from services import research_pooling as rp


def _candles(n=900, tf_min=60, seed_up=450):
    """Synthetischer Verlauf: erst aufwärts, dann abwärts (deterministisch)."""
    out = []
    price = 100.0
    ts = 1_700_000_000_000
    for i in range(n):
        drift = 0.4 if i < seed_up else -0.4
        wiggle = 0.6 * math.sin(i * 0.7)
        o = price
        price = max(price + drift + wiggle, 5.0)
        hi, lo = max(o, price) + 0.3, min(o, price) - 0.3
        out.append({"timestamp": ts + i * tf_min * 60_000, "open": o,
                    "high": hi, "low": lo, "close": price, "volume": 1000.0})
    return out


# ---------------------------------------------------------------- Varianten
class TestAblationVariants:
    def test_reactive_full_first_and_components(self):
        vs = ra.ablation_variants({"detector": "reactive"})
        keys = [v["key"] for v in vs]
        assert keys[0] == "full"
        assert {"no_mtf", "no_volume", "no_ema_confirm", "alt_ema"} <= set(keys)
        no_mtf = next(v for v in vs if v["key"] == "no_mtf")
        assert no_mtf["engine_config"]["mtf_confirm"] is False
        assert no_mtf["removed"] == "mtf_confirm"

    def test_disabled_component_is_skipped(self):
        vs = ra.ablation_variants({"detector": "reactive", "mtf_confirm": False})
        assert "no_mtf" not in [v["key"] for v in vs]

    def test_kombi_and_ema_alternatives(self):
        kk = [v["key"] for v in ra.ablation_variants({"detector": "kombi"})]
        assert {"no_pivot", "no_dominance", "alt_ema"} <= set(kk)
        ee = ra.ablation_variants({"detector": "ema"})
        assert [v["key"] for v in ee] == ["full", "alt_regression"]
        assert ee[1]["engine_config"]["detector"] == "regression"

    def test_base_config_not_mutated(self):
        base = {"detector": "kombi", "kombi_pivot_accel": True}
        ra.ablation_variants(base)
        assert base == {"detector": "kombi", "kombi_pivot_accel": True}


class TestComponentVerdicts:
    ROWS = [{"variant_key": "full", "inner_direction_pct": 70.0},
            {"variant_key": "no_a", "inner_direction_pct": 65.0},
            {"variant_key": "no_b", "inner_direction_pct": 72.5},
            {"variant_key": "no_c", "inner_direction_pct": 70.4},
            {"variant_key": "no_d", "inner_direction_pct": None,
             "direction_pct": None}]

    def test_verdict_classes(self):
        v = ra.component_verdicts(self.ROWS)
        assert v["no_a"]["verdict"] == "traegt_bei" and v["no_a"]["delta_pp"] == 5.0
        assert v["no_b"]["verdict"] == "schadet" and v["no_b"]["delta_pp"] == -2.5
        assert v["no_c"]["verdict"] == "redundant"
        assert v["no_d"]["verdict"] == "unbewertet"
        assert "full" not in v

    def test_fallback_metric_without_inner_window(self):
        rows = [{"variant_key": "full", "inner_direction_pct": None,
                 "direction_pct": 60.0},
                {"variant_key": "no_a", "inner_direction_pct": None,
                 "direction_pct": 55.0}]
        v = ra.component_verdicts(rows)
        assert v["no_a"]["verdict"] == "traegt_bei"


# ---------------------------------------------------------------- Pooling
class TestAssetPooling:
    def test_weighted_pool_and_class_separation(self):
        pool = rp.pool_rows({
            "BTCUSDT": {"direction_pct": 80.0, "bars": 300},
            "ETHUSDT": {"direction_pct": 60.0, "bars": 100},
            "GOLD": {"direction_pct": 50.0, "bars": 200}})
        crypto = pool["classes"]["crypto"]
        assert crypto["direction_pct"] == 75.0  # (80*300+60*100)/400
        assert crypto["n_symbols"] == 2 and crypto["bars"] == 400
        assert pool["classes"]["resources"]["symbols"] == ["GOLD"]
        assert pool["basis"] == "bars_weighted"

    def test_deviation_stays_visible(self):
        pool = rp.pool_rows({
            "BTCUSDT": {"direction_pct": 80.0, "bars": 300},
            "ETHUSDT": {"direction_pct": 60.0, "bars": 100}})
        dev = pool["classes"]["crypto"]["deviation"]
        assert dev["BTCUSDT"] == 5.0 and dev["ETHUSDT"] == -15.0

    def test_missing_weight_falls_back_to_one(self):
        pool = rp.pool_rows({"BTCUSDT": {"direction_pct": 80.0},
                             "ETHUSDT": {"direction_pct": 60.0}})
        assert pool["classes"]["crypto"]["direction_pct"] == 70.0


# ---------------------------------------------------------------- Kalibrierung
class TestUncertaintyCalibration:
    def test_hit_points_compare_direction_not_labels(self):
        m = 9
        up_a = eng.regime_id(2, 0, m)
        up_b = eng.regime_id(2, 2, m)   # gleiche Richtung, andere Vol-Stufe
        down = eng.regime_id(0, 1, m)
        pts = rc.hit_points([up_a, up_a, None], [up_b, down, up_b],
                            [0.8, 0.8, 0.8], m)
        assert pts == [(0.8, 1), (0.8, 0)]  # None-Kerze fällt raus

    def test_bins_merge_and_gap(self):
        rows_a = rc.bin_rows([(0.95, 1), (0.92, 1)])
        rows_b = rc.bin_rows([(0.93, 0)])
        merged = rc.merge_bin_rows([rows_a, rows_b])
        top = merged[-1]
        assert top["n"] == 3 and top["hits"] == 2
        assert top["hit_pct"] == 66.7
        assert rc.weighted_gap(merged) is not None

    def test_report_symbol_basis_only_with_data_strength(self):
        strong = rc.symbol_bins([(0.9, 1)] * 300)
        weak = rc.symbol_bins([(0.9, 0)] * 10)
        rep = rc.calibration_report({"BTCUSDT": strong, "ETHUSDT": weak},
                                    min_points=300)
        assert rep["per_symbol"]["BTCUSDT"]["basis"] == "symbol"
        assert rep["per_symbol"]["ETHUSDT"]["basis"] == "pooled:crypto"
        # Pool = 310 Punkte -> belastbar; eigener Anteil bleibt sichtbar
        assert rep["per_symbol"]["ETHUSDT"]["verdict"] == "ok"
        assert rep["per_symbol"]["ETHUSDT"]["own_n"] == 10
        assert rep["score_is_calibrated_probability"] is False

    def test_report_insufficient_pool_named(self):
        rep = rc.calibration_report(
            {"GOLD": rc.symbol_bins([(0.7, 1)] * 5)}, min_points=300)
        assert rep["per_symbol"]["GOLD"]["verdict"] == "insufficient"

    def test_reactive_payload_exposes_live_conf(self):
        candles = _candles(400)
        cfg = eng.resolve_config({"detector": "reactive"}, "1h", len(candles))
        from services import regime_reactive as rx
        pay = rx.full_payload(eng.compute_matrix(candles, cfg), cfg, candles)
        assert len(pay["live_conf"]) == len(candles)
        assert all(0.0 <= c <= 1.0 for c in pay["live_conf"])

    def test_symbol_payload_stores_uncertainty(self):
        candles = _candles(600)
        model = rg.detect_regimes({"SYM": candles[:450]}, "1h", 5, 3.0, 5.0,
                                  engine="v2", engine_config={})
        assert model
        _labels, entry = lab._symbol_payload(model, candles, "1h", 0.55, 0,
                                             False, candles[449]["timestamp"])
        unc = entry.get("uncertainty")
        assert unc and unc["n"] > 0 and isinstance(unc["bins"], list)
        assert unc["score_is_calibrated_probability"] is False


# ---------------------------------------------------------------- Job/Verdrahtung
class TestAblationJob:
    def test_run_ablation_offline_end_to_end(self, monkeypatch):
        candles = _candles(900)

        async def fake_hist(symbols, days, timeframe, job, **_kw):
            return {"BTCUSDT": candles}

        monkeypatch.setattr(lab, "fetch_histories", fake_hist)
        job_id = lab.create_job("ablation", {})
        body = {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 60,
                "train_pct": 75, "engine_config": {"detector": "reactive"}}
        asyncio.run(lab.run_ablation(job_id, body, None))
        job = lab.JOBS[job_id]
        assert job["status"] == "done", job.get("error")
        res = job["result"]
        assert res["kind"] == "ablation"
        assert res["rows"][0]["variant_key"] == "full"
        assert res["holdout_role"] == "final_test"
        assert res["selection_basis"] in ("inner_validation", "train_only")
        ok_rows = [r for r in res["rows"] if not r.get("error")]
        assert ok_rows, "mindestens eine Variante muss rechnen"
        assert all("crypto" in (r["pooling"]["classes"] or {}) for r in ok_rows)
        assert set(res["verdicts"]) <= {r["variant_key"] for r in res["rows"]}
        assert res["manifest"]["selection_rule"] == "inner_validation"

    def test_selection_never_reads_holdout(self):
        src = inspect.getsource(lab.run_ablation)
        assert "select_best_row(rows)" in src
        assert "key=lambda" not in src, \
            "keine eigene Bestenwahl neben dem AP07-Resolver"
        assert 'max(cand' not in src

    def test_router_and_analysis_wiring(self):
        import routers.regime_lab as router_mod
        rsrc = inspect.getsource(router_mod)
        assert "/api/regime-lab/ablation" in rsrc
        assert "run_ablation" in rsrc
        asrc = inspect.getsource(lab.run_analysis)
        assert "calibration_report" in asrc  # combined-Scope Kalibrierung
