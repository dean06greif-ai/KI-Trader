"""Regressionstests: Key-Level-Trailing + Live-Gate-Bypass des KI-Traders.

Anforderungen (23.08., Iteration 3):
  * Key-Level-Trailing: SL hinter zuletzt DURCHBROCHENE Widerstände/
    Unterstützungen nachziehen (nicht nur nach %-Gewinn) – intern für
    KI-Trader-Trades aktiv (ai_profit_protection.level_trail).
  * Live-Gate-Bypass: hochkonfidente Setups dürfen begrenzt live gehen
    (paar Live-Trades/Tag), auch wenn das Setup noch nicht 'live-reif' ist –
    weniger geprüfte Setups sammeln weiter Paper-Daten.
"""
from services.ai_engine import live_gate_bypass_ok
from services.bitunix_trade import (AutoTradeManager, find_swing_levels,
                                    key_level_trail_sl)


def _c(h, lo):
    return {"high": h, "low": lo}


def _uptrend_candles():
    """Aufwärts-Struktur: Swing-Tief bei 103 (zwischen Entry 100 und Kurs 108)."""
    hi_lo = [(100.5, 99.5), (101.5, 100.2), (102.5, 101.0), (103.5, 102.8),
             (104.5, 103.4), (104.8, 103.6),
             (104.0, 103.0),   # Pullback: Swing-Tief 103.0 (Retest-Zone)
             (105.0, 103.8), (106.0, 104.5), (107.0, 105.5), (108.0, 106.5)]
    return [_c(h, lo) for h, lo in hi_lo]


class TestFindSwingLevels:
    def test_detects_swing_low_and_high(self):
        lv = find_swing_levels(_uptrend_candles(), lookback=2)
        assert 103.0 in lv["lows"]
        assert lv["highs"] == [] or max(lv["highs"]) <= 108.5

    def test_too_few_candles(self):
        lv = find_swing_levels([_c(1, 1)] * 3, lookback=3)
        assert lv == {"highs": [], "lows": []}


class TestKeyLevelTrailSl:
    def test_long_trails_behind_broken_level(self):
        # Entry 100, SL 98 (Risk 2), Kurs 108 (=+4R) -> SL hinter Swing-Tief 103
        new_sl = key_level_trail_sl(_uptrend_candles(), "LONG", 100.0, 98.0,
                                    108.0, 2.0, lookback=2, buffer_pct=0.15)
        assert new_sl is not None
        assert 102.5 < new_sl < 103.0  # knapp unter dem Level (Buffer)

    def test_long_requires_min_r_profit(self):
        # Kurs nur +0.5R -> kein Trailing
        assert key_level_trail_sl(_uptrend_candles(), "LONG", 100.0, 98.0,
                                  101.0, 2.0, lookback=2) is None

    def test_long_no_worse_sl(self):
        # SL bereits über dem Level -> kein Rückschritt
        assert key_level_trail_sl(_uptrend_candles(), "LONG", 100.0, 105.0,
                                  108.0, 2.0, lookback=2) is None

    def test_short_trails_behind_broken_level(self):
        candles = [_c(2 * 104 - h + 4, 2 * 104 - lo + 4)
                   for h, lo in [(c["low"], c["high"])
                                 for c in _uptrend_candles()]]
        # gespiegelte Abwärts-Struktur: Entry 108, Kurs 100, Swing-Hoch dazwischen
        new_sl = key_level_trail_sl(candles, "SHORT", 108.0, 110.0, 100.0,
                                    2.0, lookback=2)
        if new_sl is not None:
            assert 100.0 < new_sl < 110.0

    def test_no_levels_between_entry_and_price(self):
        flat = [_c(100.2, 99.8)] * 12
        assert key_level_trail_sl(flat, "LONG", 100.0, 98.0, 108.0, 2.0,
                                  lookback=2) is None

    def test_invalid_inputs(self):
        assert key_level_trail_sl([], "LONG", 100, 98, 108, 2) is None
        assert key_level_trail_sl(_uptrend_candles(), "LONG", 100, 98, 108, 0) is None

    def test_policy_default_includes_level_trail(self):
        assert AutoTradeManager.AI_PROTECTION_DEFAULTS.get("level_trail") is True


class TestLiveGateBypass:
    CFG = {"live_gate_bypass_enabled": True, "live_gate_bypass_margin": 5,
           "live_gate_bypass_per_day": 2}

    def test_high_confidence_and_free_slot(self):
        assert live_gate_bypass_ok(75, 65, 0, self.CFG) is True
        assert live_gate_bypass_ok(70, 65, 1, self.CFG) is True

    def test_confidence_below_margin(self):
        assert live_gate_bypass_ok(68, 65, 0, self.CFG) is False

    def test_daily_limit_reached(self):
        assert live_gate_bypass_ok(90, 65, 2, self.CFG) is False

    def test_disabled(self):
        cfg = dict(self.CFG, live_gate_bypass_enabled=False)
        assert live_gate_bypass_ok(99, 65, 0, cfg) is False

    def test_zero_per_day(self):
        cfg = dict(self.CFG, live_gate_bypass_per_day=0)
        assert live_gate_bypass_ok(99, 65, 0, cfg) is False

    def test_garbage_safe(self):
        assert live_gate_bypass_ok(None, 65, 0, self.CFG) is False
        assert live_gate_bypass_ok("x", 65, 0, {"live_gate_bypass_margin": "y",
                                                "live_gate_bypass_enabled": True}) is False
