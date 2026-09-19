"""Regressionstests: dynamische Setup-Gewichtung (services/setup_weighting.py).

Setups wirken als gelernter, gewichteter Faktor auf die Konfidenz der
KI-Entscheidung – nie als harte Fessel: enge Grenzen (±10 Punkte),
Bayes-Shrinkage bei wenigen Trades, neutral ohne Daten.
"""
import pytest

from services import setup_weighting as sw


class TestWeightFor:
    def test_no_stats_is_neutral(self):
        assert sw.weight_for(None) == 1.0
        assert sw.weight_for({}) == 1.0
        assert sw.weight_for({"trades": 0, "wins": 0, "pnl": 0}) == 1.0

    def test_proven_setup_gets_boost(self):
        w = sw.weight_for({"trades": 20, "wins": 14, "pnl": 55.0})
        assert w > 1.0
        assert w <= sw.W_MAX

    def test_weak_setup_gets_damped(self):
        w = sw.weight_for({"trades": 20, "wins": 5, "pnl": -40.0})
        assert w < 1.0
        assert w >= sw.W_MIN

    def test_shrinkage_few_trades_stays_near_neutral(self):
        # 2/2 Gewinne dürfen ein Setup nicht 'bewährt' machen
        w = sw.weight_for({"trades": 2, "wins": 2, "pnl": 8.0})
        assert 1.0 < w < 1.12
        # 0/2 dürfen es nicht 'schwach' machen
        w2 = sw.weight_for({"trades": 2, "wins": 0, "pnl": -8.0})
        assert 0.88 < w2 < 1.0

    def test_bounds_enforced(self):
        assert sw.weight_for({"trades": 500, "wins": 500, "pnl": 999}) == sw.W_MAX
        assert sw.weight_for({"trades": 500, "wins": 0, "pnl": -999}) == sw.W_MIN


class TestCombinedWeight:
    def test_asset_stats_refine_only_with_enough_trades(self):
        cls = {"trades": 30, "wins": 20, "pnl": 50.0}
        base = sw.combined_weight(cls, None)
        # Asset-Bilanz mit zu wenigen Trades ändert nichts
        assert sw.combined_weight(cls, {"trades": 2, "wins": 0, "pnl": -5}) == base
        # Asset-Bilanz mit genug Trades zieht das Gewicht
        refined = sw.combined_weight(cls, {"trades": 10, "wins": 1, "pnl": -20})
        assert refined < base

    def test_all_none_is_neutral(self):
        assert sw.combined_weight(None, None) == 1.0


class TestAdjustConfidence:
    def test_neutral_weight_no_change(self):
        conf, note = sw.adjust_confidence(75, 1.0)
        assert conf == 75 and note is None

    def test_boost_and_damp_bounded(self):
        up, note = sw.adjust_confidence(70, sw.W_MAX)
        assert up == 80 and "70→80" in note
        down, note2 = sw.adjust_confidence(70, sw.W_MIN)
        assert down == 60 and note2

    def test_zero_confidence_untouched(self):
        conf, note = sw.adjust_confidence(0, 1.25)
        assert conf == 0 and note is None

    def test_clamped_to_0_100(self):
        hi, _ = sw.adjust_confidence(98, 1.25)
        assert hi == 100
        lo, _ = sw.adjust_confidence(4, 0.75)
        assert lo == 0


class TestContextLine:
    def test_none_without_significant_data(self):
        assert sw.context_line("Krypto", {}) is None
        assert sw.context_line("Krypto", {"breakout": {"trades": 2, "wins": 2, "pnl": 5}}) is None

    def test_line_lists_deviating_setups(self):
        stats = {
            "breakout": {"trades": 20, "wins": 14, "pnl": 40.0},
            "range_fade": {"trades": 15, "wins": 4, "pnl": -22.0},
        }
        line = sw.context_line("Krypto", stats)
        assert line and "breakout" in line and "range_fade" in line
        assert "Krypto" in line
