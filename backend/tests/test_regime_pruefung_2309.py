"""Regressionstests Regime-Prüfung 23.09.2026 (unit, ohne Netzwerk/DB):

1. Historien-Vertrag der Regime-Brücke: der KI-Trader lädt so viel Historie,
   wie der Detektor zum Einschwingen braucht – sonst Zustand `stale` statt
   eines stillen Live≠Lab-Labels (gemessen: bis 55 % andere Regime-IDs bei
   pauschal 30 Tagen).
2. Live-Label ist mit ausreichender Historie fenster-unabhängig (Lab = Live).
3. Referenz-Qualität (detektor-unabhängig) ergänzt Live=Final; Note wird
   durch die Referenz nach oben begrenzt (EMA-Selbst-Übereinstimmung ~98 %).
4. EMA-Vergleich wählt nach Referenz-Treffer statt nach Live=Final.
"""
import math
import random
from datetime import datetime, timezone

import numpy as np
import pytest

from services import regime_engine as eng
from services import regime_quality, regime_reference, research_validation, structural_regime

pytestmark = pytest.mark.unit


def _candles(n: int, tf_ms: int = 3_600_000, seed: int = 7, start_ts: int = 1_700_000_000_000):
    rnd = random.Random(seed)
    out, p = [], 100.0
    for i in range(n):
        # klare Phasen: 1/3 auf, 1/3 seitwärts, 1/3 ab (+ Rauschen)
        phase = (i * 3) // n
        drift = {0: 0.0009, 1: 0.0, 2: -0.0009}[phase]
        p *= 1 + drift + rnd.gauss(0, 0.004)
        hi, lo = p * (1 + abs(rnd.gauss(0, 0.002))), p * (1 - abs(rnd.gauss(0, 0.002)))
        out.append({"timestamp": start_ts + i * tf_ms, "open": p, "high": hi, "low": lo,
                    "close": p, "volume": 100 + rnd.random() * 50})
    return out


# --------------------------------------------------------------- 1. Historien-Vertrag
def test_required_history_grows_with_detector_needs():
    c = _candles(4000)
    m_ema9 = eng.build_model({"X": c}, "1h", {"detector": "ema", "regime_mode": 9,
                                                "ema_regime_days": 30, "vol_ref_days": 200,
                                                "auto_adapt": False})
    m_ema5 = eng.build_model({"X": c}, "1h", {"detector": "ema", "regime_mode": 5,
                                                "ema_regime_days": 9, "auto_adapt": False,
                                                "use_ema_confirm": False})
    need9 = eng.required_history_bars(m_ema9["config"])
    need5 = eng.required_history_bars(m_ema5["config"])
    assert need9 >= 200 * 24, "9er-Modus braucht die volle Vola-Referenz"
    assert need9 > need5
    assert eng.required_history_days(m_ema9["config"]) >= 200
    assert eng.required_history_days(m_ema5["config"]) >= 30  # nie unter dem alten Standard
    assert eng.required_history_days({}) == 30                   # Legacy-Modell ohne Config
    assert eng.required_history_days(m_ema9["config"], max_days=120) == 120


def test_reactive_warmup_covers_ema_anchor_and_pivots():
    c = _candles(3000)
    m = eng.build_model({"X": c}, "1h", {"detector": "reactive", "regime_mode": 3,
                                          "auto_adapt": False, "ema_slow_days": 50})
    cfg = m["config"]
    assert eng.detector_warmup_bars(cfg) >= 3 * cfg["ema_slow_bars"]
    cfg_no_ema = {**cfg, "use_ema_confirm": False}
    assert eng.detector_warmup_bars(cfg_no_ema) < eng.detector_warmup_bars(cfg)


def test_context_marks_insufficient_history_as_stale():
    c = _candles(6000)
    m = eng.build_model({"X": c}, "1h", {"detector": "ema", "regime_mode": 9,
                                          "ema_regime_days": 30, "vol_ref_days": 150,
                                          "auto_adapt": False})
    now = datetime.fromtimestamp(c[-1]["timestamp"] / 1000 + 60, tz=timezone.utc)
    short = structural_regime.context_from_model(m, c[-720:], "1h", "ra_t", "active", [], "BTCUSDT", now)
    assert short["history_ok"] is False
    assert short["state"] == "stale"
    assert "Historie" in short["reason"]
    assert short["history_required_bars"] > 720
    full = structural_regime.context_from_model(m, c, "1h", "ra_t", "active", [], "BTCUSDT", now)
    assert full["history_ok"] is True
    assert full["state"] == "ok"
    assert full["history_bars"] == len(c)


# --------------------------------------------------------------- 2. Fenster-Unabhängigkeit
@pytest.mark.parametrize("detector,mode", [("ema", 9), ("ema", 5), ("reactive", 3)])
def test_live_label_is_window_independent_with_required_history(detector, mode):
    c = _candles(5200, seed=11)
    m = eng.build_model({"X": c[:3900]}, "1h", {"detector": detector, "regime_mode": mode,
                                                 "auto_adapt": False, "vol_ref_days": 40,
                                                 "ema_slow_days": 30, "ema_regime_days": 9})
    cfg = m["config"]
    need = eng.required_history_bars(cfg)
    assert need < 3000
    full = eng.classify_series(m, c)
    mism_need = mism_30d = pts = 0
    for t in range(len(c) - 600, len(c), 24):
        a = full[t]
        b = eng.classify_series(m, c[t - need:t + 1])[-1]
        d = eng.classify_series(m, c[t - 720:t + 1])[-1]
        if a is None:
            continue
        pts += 1
        mism_need += int(a != b)
        mism_30d += int(a != d)
    assert pts > 10
    # Mit Detektor-Warmup (fast) identisch zum Lab – 30 Tage pauschal nicht.
    assert mism_need / pts <= 0.06, f"{detector}/{mode}: {mism_need}/{pts} Abweichungen trotz Warmup"
    assert mism_30d >= mism_need


# --------------------------------------------------------------- 3. Referenz-Qualität
def test_reference_compare_splits_holdout_and_inner():
    n = 300
    c = _candles(n, tf_ms=60_000)
    truth = [2] * 100 + [1] * 100 + [0] * 100
    live = [2] * 100 + [2] * 20 + [1] * 80 + [0] * 100     # 20 Kerzen Lag am 2. Wechsel
    train_end = c[199]["timestamp"]
    inner_start = c[99]["timestamp"]
    r = regime_reference.compare(c, live, truth, 3, 1440.0, train_end, inner_start)
    assert r["bars"] == 300
    assert r["direction_pct"] == pytest.approx(280 / 300 * 100, abs=0.1)
    assert r["holdout_bars"] == 100 and r["holdout_direction_pct"] == 100.0
    assert r["inner_bars"] == 100 and r["inner_direction_pct"] == pytest.approx(80.0, abs=0.1)
    assert r["missed_pct"] == 0.0
    assert r["switches_live"] == 2 and r["switches_truth"] == 2
    assert regime_reference.grade(r["direction_pct"]) == "gut"
    assert regime_reference.grade(40.0) == "schwach"
    assert regime_reference.grade(None) is None


def test_quality_grade_is_capped_by_reference():
    # EMA-typisch: Live=Final 98 %, Referenz nur 52 %
    g = regime_quality.grade_of(98.0, 98.5, 6000, reference_pct=52.0, reference_basis="holdout")
    assert g["grade"] == "schwach"
    assert g["reference_grade"] == "schwach"
    assert "Referenz" in g["text"]
    # ohne Referenz (Bestandsanalyse) unverändert
    g0 = regime_quality.grade_of(98.0, 98.5, 6000)
    assert g0["grade"] == "gut" and g0["reference_grade"] is None
    # gute Referenz hebt nie über die Live=Final-Note
    g1 = regime_quality.grade_of(55.0, 56.0, 6000, reference_pct=80.0, reference_basis="holdout")
    assert g1["grade"] == "mittel"


def test_quality_summary_uses_reference_from_entry():
    entry = {"live_agreement": {"direction_pct": 98.0, "holdout_direction_pct": 98.0,
                                "holdout_bars": 5000, "trend_hit_pct": 90.0},
             "validation": {"violation_bars_pct": 1.0, "avg_segment_days": 20.0, "passed": True},
             "reference": {"direction_pct": 60.0, "holdout_direction_pct": 50.0,
                           "mean_lag_days": 9.5, "missed_pct": 20.0}}
    q = regime_quality.summarize_scope({"BTCUSDT": entry})
    ov = q["overall"]
    assert ov["reference_holdout_pct"] == 50.0
    assert ov["reference_lag_days"] == 9.5
    assert ov["grade"] == "schwach"
    assert q["thresholds"]["reference_good"] == regime_reference.REF_GOOD
    legacy = regime_quality.summarize_scope({"BTCUSDT": {k: v for k, v in entry.items() if k != "reference"}})
    assert legacy["overall"]["grade"] == "gut"


# --------------------------------------------------------------- 4. Auswahl im EMA-Vergleich
def test_select_best_row_prefers_reference_chain():
    rows = [{"period": 5, "inner_direction_pct": 80.0, "inner_reference_pct": 66.0},
            {"period": 30, "inner_direction_pct": 98.0, "inner_reference_pct": 52.0}]
    chain = [("inner_reference_pct", "inner_validation_reference"),
             ("inner_direction_pct", "inner_validation"),
             ("direction_pct", "train_only")]
    best, basis = research_validation.select_best_row(rows, chain=chain)
    assert best["period"] == 5 and basis == "inner_validation_reference"
    # Altdaten ohne Referenz: bisheriges Verhalten
    for r in rows:
        r.pop("inner_reference_pct")
    best, basis = research_validation.select_best_row(rows, chain=chain)
    assert best["period"] == 30 and basis == "inner_validation"
    best, basis = research_validation.select_best_row(rows)
    assert best["period"] == 30 and basis == "inner_validation"


def test_symbol_payload_contains_reference_for_all_detectors():
    from services import regime_lab as lab
    c = _candles(2600, seed=3)
    for det in ("ema", "reactive", "regression"):
        m = eng.build_model({"X": c[:2000]}, "1h", {"detector": det, "regime_mode": 3,
                                                     "auto_adapt": False})
        assert m, det
        _labels, entry = lab._symbol_payload(m, c, "1h", 0.55, 0.0, True,
                                             c[1999]["timestamp"], c[1799]["timestamp"])
        ref = entry.get("reference")
        assert ref and ref["bars"] > 0, det
        # Referenz ist zentriert -> am Datenende fehlt ein halbes Fenster
        assert 300 <= ref["holdout_bars"] <= 600, det
        assert 150 <= ref["inner_bars"] <= 200, det
        assert ref["source"] == "centered_reference"
        assert math.isfinite(float(ref["direction_pct"]))
        assert entry["ideal"] is not None and entry["ideal"]["lookahead"] is True
        if det != "regression":
            # Live=Final (Selbst-Übereinstimmung) und Referenz sind verschiedene Zahlen
            assert entry["live_agreement"]["bars"] > 0


def test_probe_metric_ema_self_agreement_exceeds_reference_on_noisy_data():
    """Nachweis des Metrik-Problems: bei einem langen EMA-Detektor liegt Live=Final
    deutlich über der Referenz-Übereinstimmung (der Detektor ist mit sich selbst
    einig, nicht mit den echten Phasen)."""
    from services import regime_lab as lab
    rnd = np.random.default_rng(5)
    c, p = [], 100.0
    for i in range(3000):
        p *= 1 + rnd.normal(0, 0.006) + (0.0015 if (i // 150) % 2 == 0 else -0.0015)
        c.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": p, "high": p * 1.002,
                  "low": p * 0.998, "close": p, "volume": 100.0})
    m = eng.build_model({"X": c}, "1h", {"detector": "ema", "regime_mode": 3, "auto_adapt": False,
                                          "ema_regime_days": 30, "ema_regime_smooth_days": 0.5,
                                          "ema_regime_persist_days": 0.25})
    _l, entry = lab._symbol_payload(m, c, "1h", 0.55, 0.0, False, None, None)
    assert entry["live_agreement"]["direction_pct"] > entry["reference"]["direction_pct"] + 10


# --------------------------------------------------------------- 5. Brücken-Health
def test_bridge_health_flags_short_structural_history():
    from services import regime_bridge_health as h
    ctx = {"BTCUSDT": {"history_ok": False, "history_bars": 720, "history_required_bars": 4887},
           "ETHUSDT": {"history_ok": True, "history_bars": 5000, "history_required_bars": 4887}}
    checks = h.evaluate([], set(), 1, 1, [], structural_ctx=ctx)
    c = next(x for x in checks if x["name"] == "structural_short_history")
    assert c["level"] == "warn" and c["count"] == 1
    assert c["items"][0]["symbol"] == "BTCUSDT"
    ok = next(x for x in h.evaluate([], set(), 1, 1, []) if x["name"] == "structural_short_history")
    assert ok["level"] == "ok" and ok["items"] == []
