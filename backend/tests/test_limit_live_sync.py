"""Regressionstests: Live-Sync der KI-Limit-Orders (reine Entscheidungslogik)."""
import pytest

from services.limit_live_sync import (DEFAULT_CONFIG, plan_size, should_arm,
                                      should_park)

CFG = dict(DEFAULT_CONFIG)   # arm 1.5 / park 3.5 / reserve 25


class TestShouldArm:
    def test_plenty_of_capital_arms_immediately(self):
        assert should_arm(free=500.0, margin=50.0, dist_pct=5.0, cfg=CFG) is True

    def test_scarce_far_from_level_stays_local(self):
        assert should_arm(free=60.0, margin=50.0, dist_pct=5.0, cfg=CFG) is False

    def test_scarce_but_near_level_arms(self):
        assert should_arm(free=60.0, margin=50.0, dist_pct=1.0, cfg=CFG) is True

    def test_scarce_near_but_margin_does_not_fit(self):
        assert should_arm(free=40.0, margin=50.0, dist_pct=1.0, cfg=CFG) is False

    def test_unknown_balance_is_safe(self):
        assert should_arm(free=None, margin=50.0, dist_pct=0.5, cfg=CFG) is False

    def test_exact_reserve_boundary(self):
        # frei 75, Marge 50 -> Rest 25 = Reserve -> nicht knapp? (25 < 25 ist False) -> arm
        assert should_arm(free=75.0, margin=50.0, dist_pct=9.0, cfg=CFG) is True
        assert should_arm(free=74.9, margin=50.0, dist_pct=9.0, cfg=CFG) is False


class TestShouldPark:
    def test_scarce_and_far_parks(self):
        assert should_park(free=10.0, dist_pct=4.0, cfg=CFG) is True

    def test_scarce_but_near_stays_live(self):
        assert should_park(free=10.0, dist_pct=2.0, cfg=CFG) is False

    def test_plenty_capital_never_parks(self):
        assert should_park(free=500.0, dist_pct=9.0, cfg=CFG) is False

    def test_no_price_no_park(self):
        assert should_park(free=1.0, dist_pct=None, cfg=CFG) is False


class _StubTrader:
    def __init__(self, cfg):
        self._cfg = cfg

    def effective_cfg(self, symbol, strategy_id):
        return dict(self._cfg)

    def _coin_max_lev(self, symbol):
        return 100.0


class TestPlanSize:
    def _row(self, **sig):
        base = {"symbol": "BTC", "side": "LONG", "limit_price": 100.0,
                "signal": {"stop_loss": 98.0, "take_profit_full": 106.0, **sig}}
        return base

    def test_legacy_chain(self):
        at = _StubTrader({"max_capital": 100.0, "leverage": 10,
                          "auto_leverage_enabled": False})
        p = plan_size(at, self._row(ai_capital_pct=50))
        assert p["margin"] == 50.0
        assert p["leverage"] == 10.0
        assert p["qty"] == pytest.approx(5.0)
        assert p["sl"] == 98.0 and p["tp"] == 106.0

    def test_invalid_levels_dropped(self):
        at = _StubTrader({"max_capital": 100.0, "leverage": 5,
                          "auto_leverage_enabled": False})
        p = plan_size(at, {"symbol": "BTC", "side": "LONG", "limit_price": 100.0,
                           "signal": {"stop_loss": 101.0, "take_profit_full": 99.0}})
        assert p["sl"] is None and p["tp"] is None

    def test_zero_capital_returns_none(self):
        at = _StubTrader({"max_capital": 0, "leverage": 5,
                          "auto_leverage_enabled": False})
        assert plan_size(at, self._row()) is None

    def test_bad_limit_price_returns_none(self):
        at = _StubTrader({"max_capital": 100, "leverage": 5,
                          "auto_leverage_enabled": False})
        assert plan_size(at, {"symbol": "BTC", "side": "LONG",
                              "limit_price": 0, "signal": {}}) is None


class TestPrefillContract:
    """Sicherstellen, dass die Pipeline den Prefill-Vertrag kennt."""

    def test_on_signal_impl_handles_prefill(self):
        import inspect
        from services import bitunix_trade
        src = inspect.getsource(bitunix_trade.AutoTradeManager._on_signal_impl)
        assert "_live_prefill" in src
        assert "limit_live" in src

    def test_check_fills_skips_live_rows(self):
        import inspect
        from services import key_level_limits
        src = inspect.getsource(key_level_limits.check_fills)
        assert "live_order_id" in src
