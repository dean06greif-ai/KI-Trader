"""Audit 2.6: Setup-Gewichtung nach geschrumpftem Erwartungswert je riskiertem
USDT (Netto-R-Mittel) statt Trefferquote; WR nur Tiebreaker. Ohne Netzwerk."""
from services import setup_weighting as sw


def _st(trades, wins, pnl, risk_usdt=0.0):
    return {"trades": trades, "wins": wins, "pnl": pnl, "risk_usdt": risk_usdt}


def test_plan_case_90_of_100_winners_negative_pnl_weighs_below_1():
    # Plan-Test 2.6: 90/100 Gewinner mit Gesamt-PnL −500 -> Gewicht < 1
    w = sw.weight_for(_st(100, 90, -500.0, risk_usdt=1000.0))
    assert w < 1.0


def test_positive_expectancy_weighs_above_1_despite_low_winrate():
    # 30 % Winrate, aber klar positiver Erwartungswert (+0.5R im Mittel)
    w = sw.weight_for(_st(40, 12, 200.0, risk_usdt=400.0))
    assert w > 1.0


def test_shrink_small_samples_stay_near_neutral():
    # riesiger R-Wert bei nur 2 Trades wird stark Richtung neutral gezogen
    w = sw.weight_for(_st(2, 2, 100.0, risk_usdt=20.0))
    assert 1.0 < w <= 1.12


def test_fallback_without_risk_data_keeps_old_behaviour():
    old_style = {"trades": 20, "wins": 14, "pnl": 55.0}
    assert sw.weight_for(old_style) == sw.weight_for(old_style, ev_mode=False)
    assert sw.weight_for(old_style) > 1.0


def test_ev_mode_switch_off_uses_winrate():
    st = _st(100, 90, -500.0, risk_usdt=1000.0)
    assert sw.weight_for(st, ev_mode=False) > 1.0  # alte WR-Logik (Schalter aus)
    assert sw.weight_for(st) < 1.0                 # neue EV-Logik


def test_bounds_hold_in_ev_mode():
    assert sw.weight_for(_st(500, 500, 99999.0, risk_usdt=100.0)) == sw.W_MAX
    assert sw.weight_for(_st(500, 0, -99999.0, risk_usdt=100.0)) == sw.W_MIN


def test_combined_weight_threads_ev_mode():
    bad_ev = _st(100, 90, -500.0, risk_usdt=1000.0)
    live_bad_ev = _st(20, 18, -100.0, risk_usdt=200.0)
    assert sw.combined_weight(bad_ev, None, class_live_stats=live_bad_ev) < 1.0
    assert sw.combined_weight(bad_ev, None, class_live_stats=live_bad_ev,
                              ev_mode=False) > 1.0
