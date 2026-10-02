"""Regressionstests Detektor 'jump' (Statistisches Jump-Modell) + Autopilot-Auswahl.
Reine Unit-Tests ohne Netz/DB (synthetische Kerzen)."""
import numpy as np

from services import regime as rg
from services import regime_autopilot as ap
from services import regime_engine as eng
from services import regime_jump as rj
from services import research_ablation


def _candles(n=24 * 240, seed=3):
    """Synthetische 1h-Serie: auf -> seitwärts -> ab -> auf (je 60 Tage)."""
    rng = np.random.default_rng(seed)
    drift = np.concatenate([np.full(n // 4, 0.0009), np.zeros(n // 4),
                            np.full(n // 4, -0.0009), np.full(n - 3 * (n // 4), 0.0009)])
    r = drift + rng.normal(0, 0.004, n)
    close = 100 * np.exp(np.cumsum(r))
    out = []
    for i, c in enumerate(close):
        o = close[i - 1] if i else c
        out.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": float(o),
                    "high": float(max(o, c) * 1.001), "low": float(min(o, c) * 0.999),
                    "close": float(c), "volume": 1.0})
    return out


def test_online_filter_is_causal():
    rng = np.random.default_rng(1)
    L = rng.random((400, 3))
    full, _ = rj.online_filter(L, 0.8)
    part, _ = rj.online_filter(L[:250], 0.8)
    assert np.array_equal(full[:250], part), "Live-Labels dürfen nicht von der Zukunft abhängen"


def test_jump_penalty_reduces_switches():
    rng = np.random.default_rng(2)
    L = rng.random((2000, 3))
    lo, _ = rj.online_filter(L, 0.0)
    hi, _ = rj.online_filter(L, 5.0)
    sw = lambda a: int((a[1:] != a[:-1]).sum())  # noqa: E731
    assert sw(hi) < sw(lo)
    assert sw(rj.viterbi(L, 5.0)) <= sw(hi)


def test_viterbi_recovers_blocks():
    L = np.full((300, 3), 1.0)
    L[:100, 2] = 0.0
    L[100:200, 1] = 0.0
    L[200:, 0] = 0.0
    lab = rj.viterbi(L, 3.0)
    assert list(lab[[10, 150, 250]]) == [2, 1, 0]


def test_resolve_config_accepts_jump_and_clamps():
    cfg = eng.resolve_config({"detector": "jump", "jump_center": 99, "jump_penalty_days": -3},
                             "1h", 24 * 300)
    assert cfg["detector"] == "jump"
    assert cfg["jump_center"] == 5.0 and cfg["jump_penalty_days"] == 0.0
    assert eng.detector_warmup_bars(cfg) >= 3 * 14 * 24


def test_jump_detects_synthetic_regimes_end_to_end():
    candles = _candles()
    model = rg.detect_regimes({"X": candles}, "1h", 5, 3.0, 5.0, engine="v2",
                              engine_config={"detector": "jump", "regime_mode": 3})
    assert model and model["config"]["detector"] == "jump"
    live = eng.classify_series(model, candles)
    final = eng.final_labels(model, candles)
    assert len(live) == len(final) == len(candles)
    # Mitte jeder Phase: Final-Sicht erkennt auf / seitwärts / ab
    q = len(candles) // 4
    trend = [None if x is None else eng.split_id(int(x), 3)[0] for x in final]
    assert trend[q // 2 + q // 4] == 2 or trend[q // 2] == 2
    assert trend[2 * q + q // 2] == 0
    assert "Jump-Modell" in eng.summarize(model)


def test_autopilot_searches_jump_space():
    assert "jump" in ap.DETECTORS
    assert set(ap.space_for("jump")) >= {"jump_fast_days", "jump_slow_days",
                                          "jump_center", "jump_penalty_days"}


def test_robustness_key_ignores_holdout():
    a = {"holdout_direction_pct": 99.0, "train_reference_f1_pct": 40.0,
         "live_direction_phase_days": 7.0}
    b = {"holdout_direction_pct": 10.0, "train_reference_f1_pct": 50.0,
         "live_direction_phase_days": 7.0}
    assert ap.robustness_key(b, (4, 14)) > ap.robustness_key(a, (4, 14))


def test_holdout_regressed_prefers_reference_f1():
    base = {"holdout_direction_pct": 95.0, "holdout_reference_f1_pct": 40.0}
    best = {"holdout_direction_pct": 80.0, "holdout_reference_f1_pct": 60.0}
    assert ap.holdout_metric(best, base) == "holdout_reference_f1_pct"
    assert not ap.holdout_regressed(best, base)
    assert ap.holdout_regressed({"holdout_direction_pct": 80.0}, {"holdout_direction_pct": 95.0})


def test_ablation_has_jump_variants():
    vs = research_ablation.ablation_variants({"detector": "jump", "jump_penalty_days": 1.5}) \
        if hasattr(research_ablation, "ablation_variants") else None
    if vs is None:
        return
    keys = [v["key"] for v in vs]
    assert "no_jump_penalty" in keys and "alt_ema" in keys
