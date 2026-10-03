"""Audit 2.5: Wilson-Intervall, Unsicherheits-Kennzeichnung und Live-Vorrang
der Setup-Gewichtung (Paper nur als Prior mit Abzug). Ohne Netzwerk."""
from services import ai_playbook as pb
from services import setup_lifecycle as lifecycle
from services import setup_weighting as sw


def test_wilson_interval_basics():
    assert lifecycle.wilson_interval(0, 0) == (0.0, 100.0)
    lo, hi = lifecycle.wilson_interval(8, 10)
    assert 44 < lo < 55 and 90 < hi < 97
    lo2, hi2 = lifecycle.wilson_interval(80, 100)
    assert lo2 > lo and (hi2 - lo2) < (hi - lo)  # mehr Daten -> engeres Intervall
    assert 0.0 <= lo2 < hi2 <= 100.0


def test_is_uncertain_threshold():
    assert lifecycle.is_uncertain(0) and lifecycle.is_uncertain(14)
    assert not lifecycle.is_uncertain(15)


def test_stats_row_carries_ci_and_uncertain():
    row = pb._stats_row({"trades": 10, "wins": 6, "pnl": 12.0, "margin": 100.0})
    assert row["uncertain"] is True and len(row["wr_ci"]) == 2
    row2 = pb._stats_row({"trades": 40, "wins": 24, "pnl": 12.0, "margin": 100.0})
    assert row2["uncertain"] is False
    assert row2["wr_ci"][0] > row["wr_ci"][0]  # gleiche WR, mehr Daten -> engere Grenze


def test_judging_stats_prefers_live():
    live = {"trades": 6, "wins": 1, "pnl": -30.0}
    mixed = {"trades": 50, "wins": 30, "pnl": 200.0}
    st, prior = sw.judging_stats(live, mixed)
    assert st is live and prior is False
    st2, prior2 = sw.judging_stats({"trades": 2, "wins": 2, "pnl": 5.0}, mixed)
    assert st2 is mixed and prior2 is True


def test_combined_weight_paper_never_upgrades():
    good_mixed = {"trades": 40, "wins": 30, "pnl": 100.0}
    bad_mixed = {"trades": 40, "wins": 8, "pnl": -80.0}
    # ohne Live-Daten: Paper-Erfolg wertet nie über neutral auf
    assert sw.combined_weight(good_mixed, None) <= 1.0
    # schwaches Paper darf weiter dämpfen
    assert sw.combined_weight(bad_mixed, None) < 1.0
    # genug Live-Daten: Live-Bilanz zählt (auch nach oben)
    live = {"trades": 20, "wins": 15, "pnl": 60.0}
    assert sw.combined_weight(bad_mixed, None, class_live_stats=live) > 1.0
    # Schalter aus = altes Verhalten (gemischte Statistik darf aufwerten)
    assert sw.combined_weight(good_mixed, None, live_pref=False) > 1.0


def test_combined_weight_bad_live_beats_good_paper():
    good_mixed = {"trades": 60, "wins": 40, "pnl": 300.0}
    bad_live = {"trades": 10, "wins": 1, "pnl": -50.0}
    assert sw.combined_weight(good_mixed, None, class_live_stats=bad_live) < 1.0


def test_maturity_rows_carry_uncertainty():
    sid = next(iter(pb.all_setups()))
    stats = {sid: {"trades": 6, "wins": 4, "pnl": 10.0, "margin": 50.0, "verdict": "neutral"}}
    rows = pb.maturity_overview(stats, {})
    row = next(r for r in rows if r["setup"] == sid)
    assert row["uncertain"] is True
    assert len(row["wr_ci"]) == 2 and len(row["live_wr_ci"]) == 2
    assert row["wr_ci"][0] <= row["winrate"] <= row["wr_ci"][1]
