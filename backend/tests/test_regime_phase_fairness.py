"""Regression: faire Phasendauer je Regime-Anzahl (Fix 10/2026).

Befund (scripts/regime_mode_phase_probe.py): Die Richtung ist bei 3/5/9 Regimen
identisch – trotzdem erreichten 3/5 Regime den Sweet Spot (Ø Phase >= 5 d)
schlechter als 9. Ursachen: Glättungs-Wahl hing an der Regime-Anzahl, die
Unterachse (Stärke/Vola) hatte keine Mindestdauer, die Such-Strafe nutzte für
alle Modi dieselbe Untergrenze und die Referenz-Richtung hing an der Regime-Anzahl.
"""
import random

import numpy as np

from services import regime_autopilot as ap
from services import regime_engine as eng
from services import regime_phase as rp
from services import regime_reactive as rx
from services import regime_reference as rr
from services import regime_truth as rt


def _candles(n=2400, seed=7, tf_ms=3_600_000):
    rng = random.Random(seed)
    px, out, drift = 100.0, [], 0.0
    for i in range(n):
        if i % 180 == 0:
            drift = rng.choice([-0.0012, 0.0, 0.0012])
        r = drift + rng.gauss(0, 0.006)
        o, px = px, px * (1 + r)
        hi, lo = max(o, px) * (1 + abs(rng.gauss(0, 0.002))), min(o, px) * (1 - abs(rng.gauss(0, 0.002)))
        out.append({"timestamp": 1_700_000_000_000 + i * tf_ms, "open": o, "high": hi,
                    "low": lo, "close": px, "volume": 1000 + rng.random() * 200})
    return out


# ---------------- regime_phase (rein) ----------------
def test_debounce_causal_waits_and_keeps_direction():
    d = np.array([2] * 10 + [0] * 4)
    s = np.array([0, 0, 1, 0, 1, 1, 1, 1, 0, 0, 1, 1, 0, 0])
    out = rp.debounce_sub(d, s, 3, causal=True)
    assert out[:6].tolist() == [0, 0, 0, 0, 0, 0]       # kurzer Ausreißer gefiltert
    assert out[6] == 1                                  # nach 3 Kerzen übernommen
    assert out[10] == 1                                 # Richtungswechsel -> sofort
    assert len(out) == len(d)


def test_debounce_final_absorbs_short_runs_within_phase():
    d = np.array([2] * 9)
    s = np.array([0, 0, 0, 1, 0, 0, 1, 1, 1])
    out = rp.debounce_sub(d, s, 3, causal=False)
    assert out.tolist() == [0, 0, 0, 0, 0, 0, 1, 1, 1]


def test_debounce_off_is_identity():
    s = np.array([0, 1, 0, 1])
    assert rp.debounce_sub(np.zeros(4), s, 0).tolist() == s.tolist()
    assert rp.sub_min_bars({"bars_per_day": 24}) == 0          # Alt-Modell ohne Schlüssel
    assert rp.sub_min_bars({"bars_per_day": 24, "sub_min_days": 1.5}) == 36


def test_regime_phase_floor_scales_with_mode():
    assert rp.regime_phase_floor(3, 5.0) == 5.0
    assert rp.regime_phase_floor(5, 5.0) == 3.0
    assert rp.regime_phase_floor(9, 5.0) == 2.5


# ---------------- Such-Strafe ----------------
def test_phase_penalty_legacy_without_mode_unchanged():
    m = {"live_direction_phase_days": 6.0, "avg_live_phase_days": 2.0}
    assert ap.phase_penalty_for(m, 5.0, 15.0) == ap.phase_penalty(2.0, 5.0)


def test_phase_penalty_fair_per_mode():
    m5 = {"live_direction_phase_days": 6.0, "avg_live_phase_days": 3.2, "regime_mode": 5}
    m9 = {**m5, "regime_mode": 9, "avg_live_phase_days": 2.6}
    m3 = {"live_direction_phase_days": 6.0, "avg_live_phase_days": 6.0, "regime_mode": 3}
    assert ap.phase_penalty_for(m3, 5.0, 15.0) == 0.0
    assert ap.phase_penalty_for(m5, 5.0, 15.0) == 0.0
    assert ap.phase_penalty_for(m9, 5.0, 15.0) == 0.0
    short_dir = {"live_direction_phase_days": 4.0, "avg_live_phase_days": 4.0, "regime_mode": 3}
    assert ap.phase_penalty_for(short_dir, 5.0, 15.0) > 0


def test_search_space_mode_extras():
    assert "sub_min_days" not in ap.space_for("reactive")
    assert "sub_min_days" not in ap.space_for("reactive", 3)
    assert "sub_min_days" in ap.space_for("kombi", 9)
    assert "strong_speed_ratio" in ap.space_for("ema", 5)
    rng = random.Random(1)
    for _ in range(30):
        cfg = ap.mutate({"detector": "reactive", "regime_mode": 3}, rng, False, 0)
        assert "sub_min_days" not in cfg and cfg["regime_mode"] == 3


# ---------------- Engine ----------------
def test_new_config_has_sub_min_days_and_clamps():
    cfg = eng.resolve_config({"regime_mode": 5, "sub_min_days": 99}, "1h", 2000)
    assert cfg["sub_min_days"] == 10.0
    assert eng.resolve_config({"regime_mode": 5}, "1h", 2000)["sub_min_days"] == rp.DEFAULT_SUB_MIN_DAYS


def test_direction_identical_across_modes_and_sub_debounced():
    c = _candles()
    dirs, phases = {}, {}
    for mode in (3, 5, 9):
        cfg = eng.resolve_config({"regime_mode": mode, "detector": "reactive"}, "1h", len(c))
        f = eng.compute_matrix(c, cfg)
        ids, _conf, det = rx.classify(f, cfg)
        valid = ids >= 0
        tr = np.array([eng.split_id(int(r), mode)[0] for r in ids[valid]])
        dirs[mode] = tr.tolist()
        phases[mode] = len(ids[valid]) / (int(np.sum(ids[valid][1:] != ids[valid][:-1])) + 1)
    assert dirs[3] == dirs[5] == dirs[9]
    # Unterachse mit Mindestdauer: Regime-Phase nie absurd kurz ggü. Richtung
    for mode in (5, 9):
        assert phases[mode] >= 0.4 * phases[3]


def test_legacy_model_without_sub_min_days_behaves_as_before():
    c = _candles(1600, seed=3)
    cfg = eng.resolve_config({"regime_mode": 5}, "1h", len(c))
    legacy = {k: v for k, v in cfg.items() if k != "sub_min_days"}
    f = eng.compute_matrix(c, legacy)
    ids_legacy, _c, det = rx.classify(f, legacy)
    raw = rx._ids(det["live3"], rx._strength_axis(f, legacy, det["live3"], True), 5)
    raw[:det["warm"]] = -1
    assert ids_legacy.tolist() == raw.tolist()


def test_reference_direction_independent_of_mode():
    c = _candles(2000, seed=11)
    base = eng.resolve_config({"regime_mode": 9}, "1h", len(c))
    trends = {}
    for mode in (3, 5, 9):
        lab = rt.centered_labels(c, rr.reference_cfg({**base, "regime_mode": mode}), mode)
        trends[mode] = rt._trend_arr(lab, mode).tolist()
    assert trends[3] == trends[5] == trends[9]
    assert rr.REFERENCE_REVISION == "2.1"


def test_profile_quality_measures_direction_phase_for_all_modes():
    c = _candles(2200, seed=5)
    q = {}
    for mode in (3, 9):
        cfg = eng.resolve_config({"regime_mode": mode, "adapt_profile": "standard"}, "1h", len(c))
        q[mode] = eng._profile_quality({"X": c}, cfg)
    assert q[3]["target_segment_days"] == q[9]["target_segment_days"]
    assert q[3]["direction_phase_days"] == q[9]["direction_phase_days"]
