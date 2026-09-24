"""Regressionstests Prüfung 24.09.2026 (unit, ohne Netzwerk/DB):

1. POL-Liquidation: echter Fill weicht vom Signal ab -> Börsen-Liq lag VOR dem
   SL. Der Fill/Liq-Guard schießt Marge nach, zieht sonst den SL vor die Liq
   bzw. schließt, wenn der Kurs schon jenseits liegt.
2. Liquidation aus der Positions-Historie (liqQty) wird erkannt.
3. Shadow-Trades gelöschter Analysen zählen für keine aktuelle Analyse.
4. Referenz v2: festes Fenster (unabhängig von den Modell-Horizonten),
   Mehrheits-Baseline/Skill, klassen-balancierte Note.
5. Ablation/Freigabe/Autopilot bevorzugen Referenz v2 statt Live=Final.
"""
import math
import random

import pytest

from services import fill_liq_guard as flg
from services import regime_autopilot as ap
from services import regime_quality, regime_reference as rr, regime_release, regime_truth as rt
from services.bitunix_trade import parse_closed_position
from services.position_watchdog import parse_positions

# Echte Zahlen POLUSDT-1790173213520 (Bitunix-Historie): Fill 0.10144, 364 Stk, 23x
POL = dict(side="SHORT", sl=0.106694, ex_entry=0.10144, ex_liq=0.1052, qty=364.0,
           margin=364 * 0.10144 / 23)


def test_pol_case_sl_behind_real_liq_is_detected():
    assert not flg.sl_safe("SHORT", POL["sl"], POL["ex_liq"], POL["ex_entry"], 0.3)
    # Mit dem geplanten Entry 0.10318 und Liq 0.107209 war der SL noch sicher
    assert flg.sl_safe("SHORT", 0.106694, 0.107209, 0.10318, 0.3)


def test_pol_case_tops_up_margin_when_capital_is_free():
    p = flg.plan(**POL, mark=0.1030, usable_free=30.0)
    assert p["action"] == "add_margin"
    assert 0.3 < p["amount"] < 1.5
    assert p["target_liq"] > POL["sl"]


def test_pol_case_moves_sl_before_liq_without_free_capital():
    p = flg.plan(**POL, mark=0.1030, usable_free=0.1)
    assert p["action"] == "move_sl"
    assert p["new_sl"] < POL["ex_liq"]


def test_close_when_price_already_beyond_safe_sl():
    p = flg.plan(**POL, mark=0.1051, usable_free=0.0)
    assert p["action"] == "close"


def test_long_side_and_safe_case():
    safe = flg.plan("LONG", sl=95.0, ex_entry=100.0, ex_liq=90.0, qty=1, margin=10, usable_free=5)
    assert safe["action"] == "ok"
    bad = flg.plan("LONG", sl=95.0, ex_entry=100.0, ex_liq=96.0, qty=1, margin=4.5, usable_free=5)
    assert bad["action"] == "add_margin"


def test_implied_mmr_from_exchange_numbers():
    mmr = flg.implied_mmr("SHORT", POL["ex_entry"], POL["ex_liq"], POL["margin"], POL["qty"])
    assert 0.0 <= mmr <= 0.02


def test_entry_deviation():
    assert flg.entry_deviation_pct(0.10318, 0.10144) == pytest.approx(-1.6864, abs=1e-3)
    assert flg.entry_deviation_pct(0, 1) is None


def test_liquidation_flag_from_history():
    res = {"code": 0, "data": {"positionList": [{
        "positionId": "1", "maxQty": "364", "entryPrice": "0.10144", "closePrice": "0.1052",
        "liqQty": "-1.59068", "side": "SELL", "fee": "0.03234", "funding": "-0.0135",
        "realizedPNL": "-1.6365"}]}}
    out = parse_closed_position(res, "1")
    assert out["liquidated"] is True and out["entry_price"] == 0.10144
    res["data"]["positionList"][0]["liqQty"] = None
    assert parse_closed_position(res, "1")["liquidated"] is False


def test_watchdog_parses_exchange_liq_price():
    rows = parse_positions({"code": 0, "data": [{"positionId": "9", "symbol": "POLUSDT", "qty": "379",
                                                 "side": "SELL", "avgOpenPrice": "0.10423",
                                                 "liqPrice": "0.10621", "margin": "1.01",
                                                 "leverage": 40}]})
    assert rows[0]["liq_price"] == 0.10621 and rows[0]["entry"] == 0.10423


def test_shadow_counts_of_deleted_analyses_are_orphaned():
    by_aid, orphan = regime_release.split_shadow_counts({"ra_2e8723cf": 122, "ra_new": 3}, ["ra_new"])
    assert by_aid == {"ra_new": 3}
    assert orphan == {"ra_2e8723cf": 122}


def _walk(n=24 * 300, seed=3):
    rnd = random.Random(seed)
    p, out = 100.0, []
    for i in range(n):
        drift = 0.0006 * math.sin(i / 400.0)
        p *= math.exp(drift + rnd.gauss(0, 0.004))
        out.append({"timestamp": i * 3600000, "open": p, "high": p * 1.002,
                    "low": p * 0.998, "close": p, "volume": 1.0})
    return out


def test_reference_v2_window_is_independent_of_model_horizons():
    c = _walk()
    a = rt.centered_labels(c, rr.reference_cfg({"bars_per_day": 24, "horizons_days": [5, 10, 40]}), 3)
    b = rt.centered_labels(c, rr.reference_cfg({"bars_per_day": 24, "horizons_days": [2, 6, 90]}), 3)
    assert a == b
    # v1 (ohne Referenz-Schlüssel) hing dagegen vom Modell ab
    v1a = rt.centered_labels(c, {"bars_per_day": 24, "horizons_days": [5, 10, 40]}, 3)
    v1b = rt.centered_labels(c, {"bars_per_day": 24, "horizons_days": [2, 6, 90]}, 3)
    assert v1a != v1b


def test_always_sideways_has_no_skill_and_low_balanced():
    c = _walk()
    truth = rt.centered_labels(c, rr.reference_cfg({"bars_per_day": 24}), 3)
    live = [1] * len(c)   # immer "seitwärts" (3er-Modus: Richtung 1)
    ref = rr.compare(c, live, truth, 3, 24.0, train_end_ts=c[int(len(c) * .75)]["timestamp"])
    assert ref["holdout_balanced_pct"] <= 34.0
    assert ref["holdout_skill_pct"] <= 0.0
    assert ref["train_balanced_pct"] is not None
    assert rr.balanced_grade(ref["holdout_balanced_pct"]) == "schwach"


def test_quality_grade_capped_by_balanced_reference():
    g = regime_quality.grade_of(95.0, 95.0, 5000, reference_pct=74.0, reference_basis="holdout",
                                reference_balanced=44.0)
    assert g["grade"] == "schwach"
    g_old = regime_quality.grade_of(95.0, 95.0, 5000, reference_pct=74.0, reference_basis="holdout")
    assert g_old["grade"] == "gut"


def test_ablation_delta_prefers_reference_v2():
    run = {"rows": [
        {"variant_key": "full", "holdout_direction_pct": 92.6, "holdout_reference_bal_pct": 48.0},
        {"variant_key": "alt_ema", "holdout_direction_pct": 72.6, "holdout_reference_bal_pct": 55.0}]}
    assert regime_release.ablation_delta_pct(run) == -7.0
    legacy = {"rows": [{"variant_key": "full", "holdout_direction_pct": 92.6},
                       {"variant_key": "alt_ema", "holdout_direction_pct": 72.6}]}
    assert regime_release.ablation_delta_pct(legacy) == 20.0


def test_autopilot_score_prefers_balanced_reference_and_direction_phase():
    m = {"inner_direction_pct": 95, "train_direction_pct": 95, "inner_reference_bal_pct": 50,
         "train_reference_bal_pct": 50, "inner_reference_pct": 80, "train_reference_pct": 80,
         "avg_live_phase_days": 10, "live_direction_phase_days": 30}
    s = ap.score_metrics(m, 4, 14)
    expected = 0.25 * 95 + 0.75 * 50 - ap.phase_penalty(30, 4, 14)
    assert s == pytest.approx(expected, abs=1e-3)
