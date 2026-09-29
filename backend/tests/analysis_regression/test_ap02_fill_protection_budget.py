"""AP02-Solltests (Befunde T02/T03/T06): Fillmenge, dreistufiger Schutzstatus,
Risikobudget fail-closed + Unknown-SL-Risiko."""
import asyncio

import pytest

from services import risk_budget
from services.bitunix_trade import recovered_fill_qty, sl_exchange_status_of

from _fakes import FakeDB

pytestmark = pytest.mark.unit


class TestRecoveredFillQty:
    """T02: 60% Fill darf nie als 100% geplante Menge verbucht werden."""

    def test_partial_fill_books_actual_qty(self):
        assert recovered_fill_qty(10.0, 6.0) == 6.0

    def test_full_fill_books_planned_qty(self):
        assert recovered_fill_qty(10.0, 10.0) == 10.0

    def test_more_than_planned_caps_at_planned(self):
        assert recovered_fill_qty(10.0, 15.0) == 10.0

    def test_below_half_is_no_recovery(self):
        assert recovered_fill_qty(10.0, 4.9) is None

    def test_unknown_untracked_is_no_recovery(self):
        assert recovered_fill_qty(10.0, None) is None

    def test_zero_planned_is_no_recovery(self):
        assert recovered_fill_qty(0.0, 5.0) is None

    def test_garbage_input_is_no_recovery(self):
        assert recovered_fill_qty("x", "y") is None


class TestSlExchangeStatus:
    """T03: None (API unsicher) ist weder bestätigt noch sicher fehlend."""

    def test_verified_true_is_confirmed(self):
        assert sl_exchange_status_of("pos1", True) == "confirmed"

    def test_verified_false_is_missing(self):
        assert sl_exchange_status_of("pos1", False) == "missing"

    def test_none_is_unknown_not_false(self):
        assert sl_exchange_status_of("pos1", None) == "unknown"

    def test_no_position_id_is_unknown(self):
        assert sl_exchange_status_of(None, True) == "unknown"


CFG = dict(risk_budget.DEFAULT_CONFIG)


class TestRiskBudgetFailClosed:
    """T06: fehlende Equity/SL-Daten gelten nicht als bestandene Prüfung."""

    def test_missing_equity_blocks_new_trade(self):
        ok, why = risk_budget.check(50.0, "BTC", 0.0, {}, None, CFG)
        assert ok is False and "Equity unbekannt" in why

    def test_zero_equity_blocks_new_trade(self):
        ok, _ = risk_budget.check(50.0, "BTC", 0.0, {}, 0.0, CFG)
        assert ok is False

    def test_fail_open_only_by_explicit_config(self):
        cfg = {**CFG, "fail_open_no_equity": True}
        ok, _ = risk_budget.check(50.0, "BTC", 0.0, {}, None, cfg)
        assert ok is True

    def test_disabled_budget_still_allows(self):
        cfg = {**CFG, "enabled": False}
        ok, _ = risk_budget.check(50.0, "BTC", 0.0, {}, None, cfg)
        assert ok is True

    def test_normal_budget_block_still_works(self):
        # 6% von 1000 = 60; offen 40 + neu 30 = 70 > 60 -> Block
        ok, why = risk_budget.check(30.0, "BTC", 40.0, {}, 1000.0, CFG)
        assert ok is False and "Risikobudget" in why

    def test_normal_budget_pass_still_works(self):
        ok, _ = risk_budget.check(10.0, "BTC", 40.0, {}, 1000.0, CFG)
        assert ok is True


class TestUnknownSlRisk:
    def test_missing_sl_counts_conservative_risk_not_zero(self):
        t = {"status": "open", "mode": "live", "entry": 100.0, "sl": 0,
             "qty": 2.0, "side": "LONG", "symbol": "BTC"}
        assert risk_budget.trade_risk_usdt(t) == 0.0  # Legacy-Verhalten (Default)
        r = risk_budget.trade_risk_usdt(t, unknown_sl_risk_pct=2.0)
        assert r == pytest.approx(100.0 * 2.0 * 0.02)

    def test_open_risk_includes_unknown_sl_trades(self):
        trades = [
            {"status": "open", "mode": "live", "entry": 100.0, "sl": 95.0,
             "qty": 1.0, "side": "LONG", "symbol": "BTC"},
            {"status": "open", "mode": "live", "entry": 50.0, "sl": None,
             "qty": 2.0, "side": "LONG", "symbol": "ETH"},
        ]
        total, _ = risk_budget.open_risk(trades, "live", unknown_sl_risk_pct=2.0)
        assert total == pytest.approx(5.0 + 50.0 * 2.0 * 0.02)

    def test_sl_in_profit_still_zero_risk(self):
        t = {"status": "open", "mode": "live", "entry": 100.0, "sl": 110.0,
             "qty": 1.0, "side": "LONG", "symbol": "BTC"}
        assert risk_budget.trade_risk_usdt(t, unknown_sl_risk_pct=2.0) == 0.0


class TestCheckNewTradeEndToEnd:
    def test_live_without_equity_is_blocked_fail_closed(self):
        db = FakeDB()
        risk_budget._cfg_cache = None
        loop = asyncio.new_event_loop()
        try:
            ok, why = loop.run_until_complete(
                risk_budget.check_new_trade(db, "live", "BTC", 25.0, None))
            assert ok is False and "fail-closed" in why
        finally:
            risk_budget._cfg_cache = None
            loop.close()

    def test_with_equity_and_room_is_allowed(self):
        db = FakeDB()
        risk_budget._cfg_cache = None
        loop = asyncio.new_event_loop()
        try:
            ok, _ = loop.run_until_complete(
                risk_budget.check_new_trade(db, "live", "BTC", 25.0, 5000.0))
            assert ok is True
        finally:
            risk_budget._cfg_cache = None
            loop.close()
