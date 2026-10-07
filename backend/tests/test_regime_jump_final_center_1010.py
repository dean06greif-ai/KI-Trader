"""Jump-Detektor: Rückblick-Zentrum (jump_final_center_ratio) + Drift-Regel.
Problem 10/2026: BTC 1h 540d mit 3 Regime -> 89 % Seitwärts, ein -50 %-Abverkauf
über Monate wurde rückblickend "seitwärts". Reine Unit-Tests (synthetisch)."""
import numpy as np
import pytest

from services import regime_engine as eng
from services import regime_jump as rj
from services import regime_reactive as rx
from services import regime_reference
from services import regime_truth as rt

pytestmark = pytest.mark.unit


def _candles(drift_per_bar, n, seed=5, vol=0.006):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(drift_per_bar + rng.normal(0, vol, n)))
    out = []
    for i, c in enumerate(close):
        o = close[i - 1] if i else c
        out.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": float(o),
                    "high": float(max(o, c) * 1.002), "low": float(min(o, c) * 0.998),
                    "close": float(c), "volume": 1.0})
    return out


def _run(candles, **extra):
    cfg = eng.resolve_config({"regime_mode": 3, "detector": "jump", **extra}, "1h", len(candles))
    f = eng.compute_matrix(candles, cfg)
    ids, _conf, det = rx.classify(f, cfg)
    return np.asarray(ids), np.asarray(rx.final_ids_from(det, f, cfg))


def _bear():
    # 60 Tage seitwärts, 100 Tage Abverkauf (~ -0,8 %/Tag, Tagesvola ~2 %), 60 Tage seitwärts
    d = 24
    drift = np.concatenate([np.zeros(60 * d), np.full(100 * d, -0.008 / d), np.zeros(60 * d)])
    return _candles(drift, len(drift), vol=0.004)


def _ou_range(n=24 * 200, seed=9):
    rng = np.random.default_rng(seed)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = 0.99 * x[i - 1] + rng.normal(0, 0.004)
    return _candles(np.diff(x, prepend=0.0), n, vol=0.0)


def _ref_f1(candles, fin):
    cfg = eng.resolve_config({"regime_mode": 3, "detector": "jump"}, "1h", len(candles))
    ref = np.asarray([-1 if v is None else v for v in
                      rt.centered_labels(candles, regime_reference.reference_cfg(cfg), 3)])
    m = (fin >= 0) & (ref >= 0)
    a, b = fin[m], ref[m]
    return np.mean([2 * np.sum((a == k) & (b == k)) / max(np.sum(a == k) + np.sum(b == k), 1)
                    for k in (0, 1, 2)])


def test_default_ratio_matches_engine_default():
    assert eng.DEFAULT_CONFIG["jump_final_center_ratio"] == rj.FINAL_CENTER_RATIO
    p = rj.params({"bars_per_day": 24, "jump_center": 0.6})
    assert p["c_final"] == pytest.approx(0.6 * rj.FINAL_CENTER_RATIO)
    assert rj.params({"jump_center": 0.6, "jump_final_center_ratio": 1.0})["c_final"] == 0.6
    assert rj.params({"jump_center": 0.6, "jump_final_center_ratio": 9})["c_final"] == 0.6
    assert eng.resolve_config({"jump_final_center_ratio": 0.05}, "1h")["jump_final_center_ratio"] == 0.2


def test_bear_is_down_not_sideways():
    c = _bear()
    _l, fin_old = _run(c, jump_final_center_ratio=1.0)
    _l, fin_new = _run(c)
    bear = slice(65 * 24, 155 * 24)
    assert np.mean(fin_new[bear] == 0) >= 0.5
    assert _ref_f1(c, fin_new) > _ref_f1(c, fin_old)
    assert np.mean(fin_new[bear] == 0) > np.mean(fin_old[bear] == 0)


@pytest.mark.parametrize("seed", [1, 9])
def test_final_closer_to_reference_on_random_walk(seed):
    c = _candles(np.zeros(24 * 200), 24 * 200, seed=seed)
    _l, fin_old = _run(c, jump_final_center_ratio=1.0)
    _l, fin_new = _run(c)
    assert _ref_f1(c, fin_new) > _ref_f1(c, fin_old)


def test_live_view_unchanged_by_final_ratio():
    c = _bear()
    live_a, _ = _run(c, jump_final_center_ratio=1.0)
    live_b, _ = _run(c, jump_final_center_ratio=0.5)
    assert np.array_equal(live_a, live_b), "Live/Handel darf sich nicht ändern"


def test_mean_reverting_range_stays_sideways():
    _l, fin = _run(_ou_range())
    assert np.mean(fin[fin >= 0] == 1) >= 0.55


def test_drift_reclassify_helper():
    bpd = 24.0
    n = int(20 * bpd)
    lab = np.ones(n, dtype=int)
    cfg = {"validate_side_max_pct_per_day": 0.35, "validate_vol_tol_mult": 1.5}
    up = 100 * np.exp(np.linspace(0, np.log(1.25), n))          # +25 % in 20 Tagen
    flat = 100 * (1 + 0.01 * np.sin(np.linspace(0, 20, n)))     # Range
    dv = np.full(n, 2.0)
    assert (rx.drift_reclassify(lab, up, dv, cfg, bpd, 10) == 2).all()
    assert (rx.drift_reclassify(lab, flat, dv, cfg, bpd, 10) == 1).all()
    assert (lab == 1).all(), "Eingabe darf nicht verändert werden"
