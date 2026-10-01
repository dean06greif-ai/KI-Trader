"""Regressionstests für die Verbesserungen 06/2026:

1. KI-Trader-Hebel: Trade-Einstellungen (preset_lev) statt fixem 15x
2. Datensammel-Trades: feste 100-USDT-Marge, kein Paper-Guthaben-Abzug
3. TP1-Erkennung: Break-Even-Preis-Helfer (extern erkannte Teilschließung)
4. Aktivitäts-Wächter: inaktive Setups früh überarbeiten (inactivity_reason)
"""
from datetime import datetime, timedelta, timezone

from services import position_sizing, setup_lifecycle
from services.bitunix_trade import COLLECTION_MARGIN_USDT, breakeven_price
from services.ai_playbook import _clamp_trade_target, revision_allowed


def _params(**over):
    p = position_sizing.build_params(
        {"sizing_mode": "risk"}, {"capital_pct": 100}, is_swing=False)
    p.update(over)
    return p


class TestPresetLeverage:
    def test_preset_lev_wins_over_risk_max(self):
        """Coin-Einstellungen (z.B. Auto-Lev max 50) dürfen NICHT mehr auf
        risk_max_leverage=15 gedeckelt werden."""
        rs = position_sizing.compute(_params(), {}, entry=100.0, sl=99.0,
                                     equity=1000.0, coin_max_lev=200.0,
                                     preset_lev=42.0, lev_source="Test-Quelle")
        assert rs["leverage"] == 42.0
        assert "Test-Quelle" in rs["note"]
        assert rs["leverage_source"] == "Test-Quelle"

    def test_swing_cap_still_applies(self):
        rs = position_sizing.compute(_params(is_swing=True, swing_cap=8.0), {},
                                     entry=100.0, sl=99.0, equity=1000.0,
                                     coin_max_lev=200.0, preset_lev=42.0)
        assert rs["leverage"] == 8.0
        assert "Swing-Cap" in rs["leverage_source"]

    def test_coin_max_caps_preset(self):
        rs = position_sizing.compute(_params(), {}, entry=100.0, sl=99.0,
                                     equity=1000.0, coin_max_lev=25.0,
                                     preset_lev=42.0)
        assert rs["leverage"] == 25.0

    def test_fallback_without_preset_keeps_old_behaviour(self):
        old = position_sizing.compute(_params(), {}, entry=100.0, sl=99.0,
                                      equity=1000.0, coin_max_lev=200.0)
        assert old["leverage"] <= float(_params()["max_leverage"])

    def test_risk_budget_unchanged_by_preset(self):
        """Notional bleibt Risiko-basiert – nur Marge skaliert mit dem Hebel."""
        a = position_sizing.compute(_params(), {}, 100.0, 99.0, 1000.0,
                                    coin_max_lev=200.0, preset_lev=10.0)
        b = position_sizing.compute(_params(), {}, 100.0, 99.0, 1000.0,
                                    coin_max_lev=200.0, preset_lev=40.0)
        if not (a["capped"] or b["capped"]):
            assert abs(a["notional"] - b["notional"]) < 1e-6
            assert a["margin"] > b["margin"]


class TestCollectionMargin:
    def test_constant(self):
        assert COLLECTION_MARGIN_USDT == 100.0


class TestBreakevenPrice:
    def test_long_covers_fees(self):
        be = breakeven_price(100.0, "LONG", 0.06)
        assert be > 100.0
        fee = 0.06 / 100
        assert abs(be * (1 - fee) - 100.0 * (1 + fee)) < 1e-6

    def test_short_below_entry(self):
        assert breakeven_price(100.0, "SHORT", 0.06) < 100.0


class TestInactivityReason:
    NOW = datetime(2026, 6, 10, tzinfo=timezone.utc)

    def _since(self, days):
        return (self.NOW - timedelta(days=days)).isoformat()

    def test_grace_period(self):
        assert setup_lifecycle.inactivity_reason(self._since(2), 0, now=self.NOW) is None

    def test_zero_trades_flagged_after_grace(self):
        why = setup_lifecycle.inactivity_reason(self._since(5), 0, now=self.NOW)
        assert why and "0 Trades" in why

    def test_active_setup_not_flagged(self):
        assert setup_lifecycle.inactivity_reason(self._since(7), 5, now=self.NOW) is None

    def test_custom_target_honoured(self):
        # Ziel 2/Woche: 1 Trade in 7 Tagen reicht (>= 50 % des Ziels)
        assert setup_lifecycle.inactivity_reason(self._since(7), 1, target=2, now=self.NOW) is None
        # Ziel 20/Woche: 3 Trades in 7 Tagen sind zu wenig
        why = setup_lifecycle.inactivity_reason(self._since(7), 3, target=20, now=self.NOW)
        assert why and "Ziel ~20" in why

    def test_invalid_since(self):
        assert setup_lifecycle.inactivity_reason(None, 0, now=self.NOW) is None


class TestRevisionAllowedInactive:
    LIB = {"breakout": "desc"}

    def test_inactive_setup_may_be_revised(self):
        scope = {"live_blocked": {}, "inactive": {"breakout": {"reason": "nur 0 Trades"}}}
        ok, why = revision_allowed("crypto", "breakout", scope, self.LIB)
        assert ok, why

    def test_healthy_setup_still_protected(self):
        ok, why = revision_allowed("crypto", "breakout",
                                   {"live_blocked": {}, "inactive": {}}, self.LIB)
        assert not ok
        assert "inaktive" in why


class TestClampTradeTarget:
    def test_values(self):
        assert _clamp_trade_target(7) == 7
        assert _clamp_trade_target("12.5") == 12
        assert _clamp_trade_target(0) is None
        assert _clamp_trade_target(999) == 50
        assert _clamp_trade_target("abc") is None
        assert _clamp_trade_target(None) is None
