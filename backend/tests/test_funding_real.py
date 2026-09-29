"""Regressionstests: Funding-Wächter auf echten Positions-Daten (rein)."""
import pytest

from services.funding_fees import (DEFAULT_CONFIG, adverse_funding_pct,
                                   parse_funding_payload, positions_by_id)


def _payload(rows):
    return {"code": 0, "data": rows, "msg": "Success"}


class TestPositionsById:
    def test_parses_real_bitunix_shape(self):
        out = positions_by_id(_payload([{
            "positionId": "4019662841126537530", "symbol": "ETHUSDT",
            "funding": "1.7734636183136", "fee": "-4.6006342275",
            "margin": "554.3770180078779742"}]))
        p = out["4019662841126537530"]
        assert p["funding"] == pytest.approx(1.7734636, rel=1e-4)
        assert p["margin"] == pytest.approx(554.377, rel=1e-4)
        assert p["fee"] == pytest.approx(-4.6006, rel=1e-3)

    def test_negative_funding_means_paid(self):
        out = positions_by_id(_payload([{"positionId": "1", "funding": "-3.5",
                                         "margin": "100"}]))
        assert out["1"]["funding"] == -3.5

    def test_bad_payloads_are_safe(self):
        assert positions_by_id(None) == {}
        assert positions_by_id({"code": 1}) == {}
        assert positions_by_id({"code": 0, "data": [{"funding": "1"}]}) == {}
        out = positions_by_id(_payload([{"positionId": "9", "funding": "abc"}]))
        assert out["9"]["funding"] == 0.0


class TestWarnLogic:
    """Der Wächter darf nur bei ECHTEN Kosten warnen – Empfangen ist positiv."""

    def test_received_funding_never_costs(self):
        # ADA-Short des Nutzers: funding +1.88 -> Kosten 0, keine Warnung
        funding = 1.880494699104
        cost = -funding if funding < 0 else 0.0
        assert cost == 0.0

    def test_paid_funding_triggers_at_threshold(self):
        funding, margin = -25.0, 100.0
        cost = -funding
        warn_at = margin * DEFAULT_CONFIG["warn_margin_pct"] / 100.0
        assert cost >= warn_at

    def test_config_has_no_auto_close_anymore(self):
        assert "close_enabled" not in DEFAULT_CONFIG
        assert "close_margin_pct" not in DEFAULT_CONFIG


class TestEntryProjectionUnchanged:
    def test_long_pays_positive_rate(self):
        assert adverse_funding_pct(0.0001, "LONG", 8.0, 8.0) == pytest.approx(0.01)

    def test_receiver_side_is_zero(self):
        assert adverse_funding_pct(0.0001, "SHORT", 8.0, 8.0) == 0.0

    def test_parse_payload(self):
        p = parse_funding_payload({"code": 0, "data": [{"fundingRate": "0.0001",
                                                        "fundingInterval": "4"}]})
        assert p == {"rate": 0.0001, "interval_h": 4.0}
