"""Regressionstests für die Verbesserungen vom 20.06.2026 (Teil 2):

1. Fee-Wächter V3: knappe Setups mit hohem CRV fair bewerten (Relax 15%/25%)
2. Manuelle Lektions-Validierung: POST /api/ai/lessons/candidates/approve
3. KI-Trader darf fremde Strategie-Trades nur mit ai_manage-Häkchen anpassen
4. MasterPrompt-Bugfix: 0-Limits werden als 'UNBEGRENZT' kommuniziert
   (KI interpretierte max_trades_per_day=0 als Deaktivierung)
"""
from services.bitunix_trade import fee_guard_check
from services.ai_master_prompt import rules_text, check_day_rules

AI_CFG = {"fee_guard_mult": 4.0, "fee_guard_atr_mult": 0}
CFG = {"fee_percent": 0.06}   # Roundtrip 0.12% -> Standard-Minimum 0.48%


class TestFeeGuardV3:
    def test_high_crv_allows_tight_stop(self):
        # SL 0.40% < 0.48%, aber CRV 3.5 -> gelockertes Minimum 0.36%
        ok, why = fee_guard_check(AI_CFG, CFG, 100.0, 99.6, 0, tp=101.4)
        assert ok, why

    def test_medium_crv_allows_15pct_undershoot(self):
        # SL 0.42% >= 0.48*0.85 = 0.408%, CRV ~2.1
        ok, why = fee_guard_check(AI_CFG, CFG, 100.0, 99.58, 0, tp=100.9)
        assert ok, why

    def test_low_crv_still_blocked(self):
        ok, why = fee_guard_check(AI_CFG, CFG, 100.0, 99.7, 0, tp=100.4)
        assert not ok and "Fee-Wächter" in why

    def test_too_tight_even_with_high_crv(self):
        # SL 0.30% < 0.36% (Relax-Untergrenze bei CRV>=3) -> Block bleibt
        ok, _ = fee_guard_check(AI_CFG, CFG, 100.0, 99.7, 0, tp=102.0)
        assert not ok

    def test_relax_can_be_disabled(self):
        cfg = {**AI_CFG, "fee_guard_crv_relax": False}
        ok, _ = fee_guard_check(cfg, CFG, 100.0, 99.6, 0, tp=101.4)
        assert not ok

    def test_regression_without_tp_unchanged(self):
        # Ohne TP (alte Aufrufe) gilt exakt das bisherige Verhalten
        ok, _ = fee_guard_check(AI_CFG, CFG, 100.0, 99.6, 0)
        assert not ok
        ok2, _ = fee_guard_check(AI_CFG, CFG, 100.0, 99.5, 0)
        assert ok2


class TestMasterPromptZeroSemantics:
    def test_zero_limits_rendered_as_unlimited(self):
        t = rules_text({"max_leverage": 25, "max_trades_per_day": 0,
                        "max_open_trades": 0, "max_daily_loss_usdt": 0})
        assert "Max. Trades pro Tag: UNBEGRENZT" in t
        assert "KEINE Deaktivierung" in t
        assert "Max. offene KI-Trades: UNBEGRENZT" in t
        assert "Tages-Verlustlimit: KEINES" in t

    def test_set_limits_still_rendered(self):
        t = rules_text({"max_trades_per_day": 5, "max_open_trades": 3,
                        "max_daily_loss_usdt": 50})
        assert "Max. Trades pro Tag: 5" in t
        assert "Max. offene KI-Trades: 3" in t
        assert "Tages-Verlustlimit: 50" in t

    def test_zero_means_no_block_in_enforcement(self):
        # 0 = kein Limit: auch 100 Trades am Tag werden NICHT geblockt
        ok, _ = check_day_rules({"max_trades_per_day": 0}, day_pnl=0, day_trades=100)
        assert ok
        ok2, why = check_day_rules({"max_trades_per_day": 5}, day_pnl=0, day_trades=5)
        assert not ok2 and "Tages-Limit" in why


class TestAiManageAllowed:
    def _autotrader(self, overrides=None, scc=None):
        from services.bitunix_trade import AutoTradeManager
        t = AutoTradeManager.__new__(AutoTradeManager)
        t.config = {"strategy_overrides": overrides or {},
                    "strategy_coin_configs": scc or {}}
        return t

    def test_own_ai_trades_always_allowed(self):
        t = self._autotrader()
        assert t.ai_manage_allowed("ai_trader", "BTCUSDT")
        assert t.ai_manage_allowed(None)

    def test_foreign_strategy_blocked_by_default(self):
        t = self._autotrader()
        assert not t.ai_manage_allowed("scalping", "BTCUSDT")

    def test_checkbox_in_strategy_override_allows(self):
        t = self._autotrader(overrides={"scalping": {"ai_manage": True}})
        assert t.ai_manage_allowed("scalping", "BTCUSDT")

    def test_coin_level_config_wins(self):
        t = self._autotrader(
            overrides={"scalping": {"ai_manage": True}},
            scc={"scalping_BTCUSDT": {"ai_manage": False}})
        assert not t.ai_manage_allowed("scalping", "BTCUSDT")
        t2 = self._autotrader(scc={"scalping_ETHUSDT": {"ai_manage": True}})
        assert t2.ai_manage_allowed("scalping", "ETHUSDT")

    def test_defaults_contain_flag(self):
        from core.defaults import DEFAULT_STRATEGY_OVERRIDE, DEFAULT_STRATEGY_COIN_CFG
        assert DEFAULT_STRATEGY_OVERRIDE.get("ai_manage") is False
        assert DEFAULT_STRATEGY_COIN_CFG.get("ai_manage") is False
