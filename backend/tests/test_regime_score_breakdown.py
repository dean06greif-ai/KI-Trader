"""Regime-Lab: Score-Zerlegung + Richtungs-Edge (Analyse 10/2026).

Score bleibt exakt gleich (score_metrics nutzt score_breakdown), die Zerlegung
erklärt die ~60-66-Grenze; direction_edge trennt Phasen-Erkennung von
Vorhersagekraft. Kennzahlen = echter Prod-Lauf 04.10. (Score 63.475)."""
from services import regime_advice
from services import regime_autopilot as ap

PROD_BEST = {"utility_sign_hit_pct": 51.8, "utility_separation_pct": 1.5,
             "utility_train_sign_hit_pct": 47.5, "holdout_skill_pct": 6.5,
             "train_reference_f1_pct": 55.3, "inner_reference_f1_pct": 55.2,
             "train_direction_pct": 93.1, "inner_direction_pct": 93.2,
             "live_direction_phase_days": 5.6, "avg_live_phase_days": 4.53}


def test_breakdown_reproduces_prod_score():
    bd = ap.score_breakdown(PROD_BEST, 4.5, 15.0)
    assert bd["total"] == 63.475 == ap.score_metrics(PROD_BEST, 4.5, 15.0)
    assert bd["live_final_weight"] == 0.25 and bd["reference_weight"] == 0.75
    parts = bd["live_final_points"] + bd["reference_points"] + bd["utility_points"] - bd["phase_penalty"]
    assert abs(parts - bd["total"]) < 0.02
    assert bd["utility_points"] == -1.25 and bd["phase_penalty"] == 0
    # Score 70 bräuchte ~F1 63.9, Score 80 ~F1 77.2
    assert 63 < bd["f1_needed"]["70"] < 65 and 76 < bd["f1_needed"]["80"] < 78


def test_score_metrics_unchanged_for_legacy_and_edge_cases():
    assert ap.score_metrics(None, 5) is None and ap.score_breakdown({}, 5) is None
    # nur Live=Final (Alt-Lauf ohne Referenz): Score = Live=Final - Strafe
    m = {"direction_pct": 96.0, "avg_live_phase_days": 2.5}
    exp = round(96.0 - ap.phase_penalty(2.5, 5.0, 0.0), 3)
    assert ap.score_metrics(m, 5.0) == exp
    bd = ap.score_breakdown(m, 5.0)
    assert bd["reference_pct"] is None and bd["f1_needed"]["70"] is None
    # Referenz v1 (Gewicht 0.5)
    m1 = {"train_direction_pct": 90.0, "train_reference_pct": 60.0}
    assert ap.score_metrics(m1, 0) == 75.0
    assert ap.score_breakdown(m1, 0)["reference_weight"] == 0.5


def test_direction_edge_verdicts():
    assert regime_advice.direction_edge(PROD_BEST)["verdict"] == "schwach"
    assert regime_advice.direction_edge({**PROD_BEST, "utility_sign_hit_pct": 48.9})["verdict"] == "kein"
    assert regime_advice.direction_edge({**PROD_BEST, "utility_separation_pct": -0.2})["verdict"] == "kein"
    good = {**PROD_BEST, "utility_sign_hit_pct": 58.0}
    assert regime_advice.direction_edge(good)["verdict"] == "vorhanden"
    assert regime_advice.direction_edge({**good, "holdout_skill_pct": -1})["verdict"] == "schwach"
    assert regime_advice.direction_edge({}) is None
    # ohne Holdout-Wert: Training als Fallback
    assert regime_advice.direction_edge({"utility_train_sign_hit_pct": 47.0})["verdict"] == "kein"


def test_result_warnings_contains_edge_hint_only_when_weak():
    w = regime_advice.result_warnings({**PROD_BEST, "reference_pct": 50}, 5, 15)
    assert any("Schwacher Richtungs-Edge" in x for x in w)
    w2 = regime_advice.result_warnings({**PROD_BEST, "reference_pct": 50,
                                        "utility_sign_hit_pct": 60.0}, 5, 15)
    assert not any("Edge" in x for x in w2)
    # alte Ergebnisse ohne Nutzen-Kennzahl: keine neue Warnung
    assert regime_advice.result_warnings({"reference_pct": 50}, 5, 15) == []
