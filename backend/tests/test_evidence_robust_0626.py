"""06/2026 E1/E2: Statistik-Kern, robuste Validierung, zeitliche Validierung, Verdict."""
import math

from services import dynamic_strategy as dyn
from services import evidence_stats as es
from services import regime_evidence


# ---------------- evidence_stats ----------------
def test_credibility_and_shrink():
    assert es.credibility(0) == 0.0
    assert math.isclose(es.credibility(20, 20), 0.5)
    assert math.isclose(es.credibility(80, 20), 0.8)
    assert math.isclose(es.shrink(1.0, 20, 0.0, 20), 0.5)
    assert es.shrink(2.0, 0, 0.3) == 0.3


def test_lower_bound_and_validation():
    assert es.lower_bound(1.0, 2.0, 1) is None
    lb = es.lower_bound_of([1, 1, 1, 1, 5])
    assert lb is not None and lb > 0
    assert es.validation_ok([1.0, 1.2, 0.9, 1.1, 1.0, 0.8], 5)
    assert not es.validation_ok([5.0, -4.0, 3.0, -3.5, 0.2, -0.1], 5)
    assert not es.validation_ok([1, 1, 1], 5)


def test_prob_greater_deterministic():
    a, b = [1.0] * 10 + [0.5] * 10, [-0.5] * 20
    p = es.prob_greater(a, b)
    assert p == 1.0 and es.prob_greater(a, b) == p
    assert es.prob_greater([1.0], []) is None
    noisy = es.prob_greater([3, -3, 2, -2, 0.5, -0.4], [0.1, -0.1, 0.05, -0.05])
    assert 0.2 < noisy < 0.9


def test_size_factor_and_robust_mean():
    assert es.size_factor(10, -0.1) == 0.0
    assert es.size_factor(2, 0.5) == es.MIN_SIZE_FACTOR
    assert es.size_factor(80, 0.5) == 0.8
    assert es.robust_mean([1, 1, 1, 1, 100, 200]) == 1.0


# ---------------- regime_evidence ----------------
def _pr(rid, n, avg, lb=None):
    return {"regime": rid, "label": f"R{rid}", "metrics": {"trades": n, "avg_pnl": avg, "avg_pnl_lb": lb}}


def test_regime_evidence_classic_unchanged():
    r = regime_evidence.evaluate([_pr(0, 12, 0.5), _pr(1, 40, 0.2)])
    assert r["ok"] is False and r["min_trades"] == 30
    assert "mode" not in r


def test_regime_evidence_robust_allows_few_trades_with_weighting():
    r = regime_evidence.evaluate([_pr(0, 12, 0.5), _pr(1, 40, 0.2)], mode="robust")
    assert r["ok"] is True and r["mode"] == "robust"
    row0 = next(x for x in r["regimes"] if x["regime"] == 0)
    assert row0["credibility"] == round(12 / 32, 3)
    assert 0 < row0["size_factor"] < 1
    assert row0["shrunk_avg_pnl"] < 0.5


def test_regime_evidence_robust_blocks_negative_and_thin():
    r = regime_evidence.evaluate([_pr(0, 60, -0.3), _pr(1, 60, 0.1)], mode="robust")
    assert r["ok"] is False and any("Erwartungswert" in x for x in r["reasons"])
    r2 = regime_evidence.evaluate([_pr(0, 5, 0.5)], mode="robust")
    assert r2["ok"] is False


# ---------------- dynamic_strategy ----------------
def _rows(pnls):
    return [{"pnl": p, "closed": f"2026-01-01T00:{i:02d}:00", "fees": 0} for i, p in enumerate(pnls)]


def test_metrics_have_evidence_fields():
    m = dyn.metrics_from_rows(_rows([1, 2, 3, 4]), 100)
    assert m["pnl_std"] > 0 and m["avg_pnl_lb"] is not None
    assert dyn.metrics_from_rows([], 100)["avg_pnl_lb"] is None


def test_validation_passed_modes():
    lucky = dyn.metrics_from_rows(_rows([5, -1, -1]), 100)
    assert dyn.validation_passed(lucky, 3) is True          # Alt-Verhalten
    assert dyn.validation_passed(lucky, 3, "robust") is False
    solid = dyn.metrics_from_rows(_rows([1, 1.2, 0.8, 1.1, 0.9, 1.0]), 100)
    assert dyn.validation_passed(solid, 3, "robust") is True


def test_score_robust_ignores_outliers():
    m = {"pnl": 100.0, "pnl_ex_top2": -5.0, "win_rate": 50.0}
    assert dyn._score(m, "combo") > 0
    assert dyn._score(m, "robust") < 0


def test_build_verdict_classic_and_robust():
    dyn_m = dyn.metrics_from_rows(_rows([3, 2, -0.3, 1.5, 0.2]), 100)
    stat_m = dyn.metrics_from_rows(_rows([0.1, -0.1, 0.1, 0.0, 0.1]), 100)
    classic = dyn.build_verdict(dyn_m, stat_m, 3, 4)
    assert classic["dynamic_better"] is True and "evidence_mode" not in classic
    robust = dyn.build_verdict(dyn_m, stat_m, 3, 4, mode="robust",
                               dyn_rows=_rows([3, 2, -0.3, 1.5, 0.2]), stat_rows=_rows([0.1] * 5))
    assert robust["dynamic_better"] is False and robust["classic_dynamic_better"] is True
    strong = [1.0, 1.2, 0.9, 1.1] * 5
    sm = dyn.metrics_from_rows(_rows(strong), 100)
    r2 = dyn.build_verdict(sm, stat_m, 3, 4, mode="robust", dyn_rows=_rows(strong),
                           stat_rows=_rows([0.1, -0.1, 0.1, 0.0, 0.1]))
    assert r2["dynamic_better"] is True and r2["prob_dynamic_better"] >= 0.8


# ---------------- split_segments ----------------
H = 3_600_000


def _seg(rid, start_bar, n, warm=5):
    c = [{"timestamp": (start_bar - warm + i) * H} for i in range(n + warm)]
    return {"regime": rid, "start_ts": c[warm]["timestamp"], "candles": c, "n_bars": n}


def _segments():
    return {"A": [_seg(0, 100, 100), _seg(1, 200, 100), _seg(0, 300, 100), _seg(1, 400, 100)]}


def test_split_phase_mode_is_default():
    a = dyn.split_segments(_segments(), 75)
    b = dyn.split_segments(_segments(), 75, "phase")
    assert [len(x) for x in a[0].values()] == [len(x) for x in b[0].values()]
    assert len(a[1]["A"]) == 4       # Ende JEDER Phase


def test_split_time_mode_is_strictly_later_with_embargo():
    tr, va = dyn.split_segments(_segments(), 75, "time", embargo_bars=10)
    last_train = max(seg["candles"][-1]["timestamp"] for seg in tr["A"])
    first_val = min(seg["start_ts"] for seg in va["A"])
    assert first_val > last_train
    assert (first_val - last_train) / H >= 10
    assert len(va["A"]) == 1 and va["A"][0]["regime"] == 1   # nur die spätere Phase
    for seg in tr["A"] + va["A"]:
        assert seg["n_bars"] > 20
        warm = len(seg["candles"]) - seg["n_bars"]
        assert seg["candles"][warm]["timestamp"] == seg["start_ts"]


def test_split_time_tiny_input_returns_train_only():
    tr, va = dyn.split_segments({"A": [_seg(0, 0, 30)]}, 75, "time")
    assert va == {} and tr["A"]
