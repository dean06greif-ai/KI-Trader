"""Regressionstests: Funding-Gebühren im Fee-Wächter + Funding-Projektion.

Verbesserung (22.08.): Der Fee-Wächter bezieht projizierte Funding-Kosten über
die erwartete Haltedauer ein, damit lange gehaltene Trades realistisch bewertet
werden (services/funding_fees.py + fee_guard_check in services/bitunix_trade.py).
"""
from services.bitunix_trade import fee_guard_check, fee_guard_min_sl_pct
from services.funding_fees import (adverse_funding_pct, hold_hours,
                                   parse_funding_payload)


class TestAdverseFundingPct:
    def test_long_pays_positive_rate(self):
        # 0.01%/8h Rate, 24h Haltedauer -> 3 Intervalle -> 0.03% Notional
        pct = adverse_funding_pct(0.0001, "LONG", 24.0, 8.0)
        assert abs(pct - 0.03) < 1e-9

    def test_long_receives_negative_rate(self):
        assert adverse_funding_pct(-0.0001, "LONG", 24.0, 8.0) == 0.0

    def test_short_pays_negative_rate(self):
        pct = adverse_funding_pct(-0.0002, "SHORT", 8.0, 8.0)
        assert abs(pct - 0.02) < 1e-9

    def test_short_receives_positive_rate(self):
        assert adverse_funding_pct(0.0002, "SHORT", 8.0, 8.0) == 0.0

    def test_invalid_inputs_fail_open(self):
        assert adverse_funding_pct(None, "LONG", 8.0) == 0.0
        assert adverse_funding_pct("x", "LONG", 8.0) == 0.0
        assert adverse_funding_pct(0.001, "LONG", 0) == 0.0

    def test_hold_hours_mapping(self):
        assert hold_hours("swing") > hold_hours("scalp")
        assert hold_hours(None) == hold_hours("scalp")
        assert hold_hours("unbekannt") == hold_hours("scalp")


class TestParseFundingPayload:
    def test_dict_payload(self):
        p = {"code": 0, "data": {"fundingRate": "0.0001", "fundingInterval": 8}}
        info = parse_funding_payload(p)
        assert info == {"rate": 0.0001, "interval_h": 8.0}

    def test_list_payload_and_default_interval(self):
        p = {"code": 0, "data": [{"fundingRate": "-0.0003"}]}
        info = parse_funding_payload(p)
        assert info["rate"] == -0.0003
        assert info["interval_h"] == 8.0

    def test_bad_payloads(self):
        assert parse_funding_payload(None) is None
        assert parse_funding_payload({"code": 1, "data": {}}) is None
        assert parse_funding_payload({"code": 0, "data": {}}) is None


class TestFeeGuardWithFunding:
    AI = {"fee_guard_enabled": True, "fee_guard_mult": 4.0,
          "fee_guard_atr_mult": 0.0, "fee_guard_crv_relax": False}
    CFG = {"fee_percent": 0.06}

    def test_min_pct_includes_funding(self):
        base = fee_guard_min_sl_pct(0.06, 4.0)
        with_funding = fee_guard_min_sl_pct(0.06, 4.0, funding_pct=0.05)
        assert abs(base - 0.48) < 1e-9
        assert abs(with_funding - (0.48 + 4.0 * 0.05)) < 1e-9

    def test_trade_blocked_only_with_funding(self):
        # SL-Distanz 0.5% > 0.48% Fee-Minimum -> ohne Funding erlaubt
        entry, sl = 100.0, 99.5
        ok, _ = fee_guard_check(self.AI, self.CFG, entry, sl)
        assert ok
        # Mit 0.05% projizierten Funding-Kosten steigt das Minimum auf 0.68%
        ok, reason = fee_guard_check(self.AI, self.CFG, entry, sl,
                                     funding_pct=0.05)
        assert not ok
        assert "Funding" in reason

    def test_wide_sl_passes_with_funding(self):
        ok, _ = fee_guard_check(self.AI, self.CFG, 100.0, 99.0,
                                funding_pct=0.05)
        assert ok

    def test_guard_disabled_ignores_funding(self):
        ai = dict(self.AI, fee_guard_enabled=False)
        ok, _ = fee_guard_check(ai, self.CFG, 100.0, 99.9, funding_pct=1.0)
        assert ok
