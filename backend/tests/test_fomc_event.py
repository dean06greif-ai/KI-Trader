"""Regressionstests: FOMC-Event-Setup, Backtest-Regeln & Guthaben-Wächter."""
from datetime import datetime, timedelta, timezone

import pytest

from services import fomc_backtest, fomc_event, key_credits


DEC = fomc_event.decision_dt_utc("2025-12-10")


def _mk_candles(t0, spec):
    """spec: Liste (offset_min, open, high, low, close) -> 5m-Kerzen."""
    return [{"time": t0 + m * 60, "open": o, "high": h, "low": l, "close": c}
            for m, o, h, l, c in spec]


class TestCalendar:
    def test_decision_time_winter_is_1900_utc(self):
        assert DEC.hour == 19  # 14:00 ET im Winter (EST) = 19:00 UTC

    def test_decision_time_summer_is_1800_utc(self):
        d = fomc_event.decision_dt_utc("2025-06-18")
        assert d.hour == 18    # 14:00 ET im Sommer (EDT) = 18:00 UTC

    def test_past_decisions_two_years(self):
        now = datetime(2026, 6, 1, tzinfo=timezone.utc)
        past = fomc_event.past_decisions(now, years=2.0)
        assert len(past) >= 14
        assert all(dt < now for dt in past)

    def test_next_decision(self):
        now = DEC - timedelta(days=3)
        assert fomc_event.next_decision(now) == DEC


class TestPhases:
    @pytest.mark.parametrize("offset_min,expected", [
        (-120, "none"), (-89, "pre"), (-1, "pre"),
        (0, "lock"), (9, "lock"),
        (10, "post"), (149, "post"), (151, "none"),
    ])
    def test_phase(self, offset_min, expected):
        ph, _ = fomc_event.phase(DEC + timedelta(minutes=offset_min))
        assert ph == expected

    def test_entry_block_outside_window(self):
        reason = fomc_event.entry_block_reason("fomc_event", DEC - timedelta(days=1))
        assert reason and "nur im FOMC-Event-Fenster" in reason

    def test_entry_block_lock_for_all_setups(self):
        now = DEC + timedelta(minutes=2)
        assert fomc_event.entry_block_reason("trend_follow", now)
        assert fomc_event.entry_block_reason("fomc_event", now)

    def test_entry_allowed_post(self):
        now = DEC + timedelta(minutes=30)
        assert fomc_event.entry_block_reason("fomc_event", now) is None
        assert fomc_event.entry_block_reason("trend_follow", now) is None

    def test_other_setups_unaffected_outside_window(self):
        assert fomc_event.entry_block_reason("trend_follow",
                                             DEC - timedelta(days=1)) is None

    def test_prompt_block_phases(self):
        assert "PRE" in fomc_event.prompt_block(DEC - timedelta(minutes=30))
        assert "LOCK" in fomc_event.prompt_block(DEC + timedelta(minutes=5))
        assert "POST" in fomc_event.prompt_block(DEC + timedelta(minutes=60))
        assert fomc_event.prompt_block(DEC - timedelta(days=5)) == ""
        assert "Zinsentscheid in" in fomc_event.prompt_block(DEC - timedelta(hours=10))


class TestLiveOverride:
    def setup_method(self):
        fomc_event._state = {"live_enabled": False, "validation": {}}

    def test_disabled_without_optin(self):
        fomc_event._state["validation"] = {"crypto": {"validated": True, "summary": "x"}}
        assert fomc_event.live_override("crypto") is None

    def test_enabled_with_validation(self):
        fomc_event._state = {"live_enabled": True,
                             "validation": {"crypto": {"validated": True, "summary": "x"}}}
        ok, why = fomc_event.live_override("crypto")
        assert ok and "Backtest-validiert" in why

    def test_no_override_without_validation(self):
        fomc_event._state["live_enabled"] = True
        assert fomc_event.live_override("crypto") is None
        assert fomc_event.live_override("indices") is None


class TestBacktestRules:
    T0 = int(DEC.timestamp())

    def _pre_range(self):
        # 3h Pre-Range: 100..102 (Höhe 2.0, mid 101)
        return [(m, 101.0, 102.0, 100.0, 101.0) for m in range(-180, 0, 5)]

    def test_fade_short_triggers_and_hits_tp(self):
        spec = self._pre_range() + [
            (0, 101.0, 103.5, 101.0, 101.5),   # Spike > rh+0.7, Close zurück in Range
            (5, 101.5, 101.6, 100.9, 101.0),   # fällt auf mid=101 -> TP
        ]
        trades = fomc_backtest.simulate_event(_mk_candles(self.T0, spec), self.T0)
        fades = [t for t in trades if t["rule"] == "fade"]
        assert len(fades) == 1
        assert fades[0]["side"] == "SHORT" and fades[0]["result"] == "tp"
        assert fades[0]["pnl"] > 0

    def test_drift_long_triggers(self):
        spec = self._pre_range() + [
            (35, 102.0, 103.0, 102.0, 102.8),  # Close > rh+0.5 -> LONG
            (40, 102.8, 105.2, 102.8, 105.0),  # TP = 102.8+2.0=104.8 getroffen
        ]
        trades = fomc_backtest.simulate_event(_mk_candles(self.T0, spec), self.T0)
        drifts = [t for t in trades if t["rule"] == "drift"]
        assert len(drifts) == 1
        assert drifts[0]["side"] == "LONG" and drifts[0]["result"] == "tp"

    def test_no_trade_without_signal(self):
        spec = self._pre_range() + [(m, 101.0, 101.5, 100.5, 101.0)
                                    for m in range(0, 150, 5)]
        assert fomc_backtest.simulate_event(_mk_candles(self.T0, spec), self.T0) == []

    def test_too_narrow_range_skipped(self):
        spec = [(m, 100.0, 100.01, 100.0, 100.0) for m in range(-180, 0, 5)]
        spec += [(0, 100.0, 100.5, 100.0, 100.0)]
        assert fomc_backtest.simulate_event(_mk_candles(self.T0, spec), self.T0) == []

    def test_sl_counted_before_tp_same_candle(self):
        spec = self._pre_range() + [
            (0, 101.0, 103.5, 101.0, 101.5),
            (5, 101.5, 104.0, 100.5, 101.0),   # SL (103.7) UND TP (101) in einer Kerze
        ]
        trades = fomc_backtest.simulate_event(_mk_candles(self.T0, spec), self.T0)
        assert trades[0]["result"] == "sl"


class TestValidation:
    def _trades(self, events, pnl):
        return [{"event": e, "pnl": pnl, "entry_ts": 0} for e in events]

    def test_needs_min_trades(self):
        agg = fomc_backtest.aggregate(self._trades(["2025-01-29"] * 3, 5.0),
                                      ["2025-01-29"])
        ok, why = fomc_backtest.validated(agg)
        assert not ok and "Backtest-Trades" in why

    def test_oos_must_be_positive(self):
        events = [f"2025-0{i}-01" for i in range(1, 7)]
        trades = self._trades(events[:4] * 3, 10.0) + self._trades(events[4:] * 3, -5.0)
        agg = fomc_backtest.aggregate(trades, events)
        ok, why = fomc_backtest.validated(agg)
        assert not ok and "Overfitting" in why

    def test_valid_when_total_and_oos_positive(self):
        events = [f"2025-0{i}-01" for i in range(1, 7)]
        agg = fomc_backtest.aggregate(self._trades(events * 3, 4.0), events)
        ok, why = fomc_backtest.validated(agg)
        assert ok and "OOS" in why


class TestKeyCredits:
    @pytest.mark.parametrize("remaining,has_credits,expected", [
        (10.0, True, "ok"), (1.99, True, "warning"), (0.49, True, "critical"),
        (0.0, False, "free"), (None, True, "unknown"), (2.0, True, "ok"),
    ])
    def test_levels(self, remaining, has_credits, expected):
        assert key_credits.level_for(remaining, has_credits) == expected


class TestNewsWatcherFomcBoost:
    def test_normal_interval_unchanged(self):
        from services.ai_news_watcher import effective_interval_sec
        assert effective_interval_sec(15, False) == 15 * 60
        assert effective_interval_sec(2, False) == 5 * 60   # Untergrenze 5 min

    def test_fomc_window_boost(self):
        from services.ai_news_watcher import FOMC_INTERVAL_MIN, effective_interval_sec
        assert effective_interval_sec(15, True) == FOMC_INTERVAL_MIN * 60
        assert effective_interval_sec(2, True) == FOMC_INTERVAL_MIN * 60

    def test_invalid_config_falls_back(self):
        from services.ai_news_watcher import effective_interval_sec
        assert effective_interval_sec("kaputt", False) == 15 * 60


class TestPlaybookIntegration:
    def test_setup_registered(self):
        from services import ai_playbook
        assert "fomc_event" in ai_playbook.SETUPS
        assert ai_playbook.normalize_setup("fomc") == "fomc_event"

    def test_excluded_classes(self):
        from services import setup_asset_class as ac
        # Seit 06/2026 sind Event-Setups in ALLEN Klassen erlaubt (Zinsentscheide
        # bewegen Gold/Öl/Forex genauso) – siehe setup_asset_class.EXCLUDED.
        for cls in ("crypto", "indices", "resources", "forex"):
            assert ac.setup_allowed(cls, "fomc_event")
