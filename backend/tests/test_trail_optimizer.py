"""Regressionstests: Trail-SL-Parameter-Optimierung (Optimizer-Gruppe 'trail')."""
from core.defaults import OPT_TRADE_KEYS
from services.optimizer import TRADE_SPACES, build_trade_space


class TestTrailSpace:
    def test_trail_group_exists_with_wider_mults(self):
        space = TRADE_SPACES["trail"]
        assert set(space["trail_after_tp1"]) == {False, True}
        # gegen zu engen Trail: Suchraum reicht deutlich über den Default 1.5 hinaus
        assert max(space["trail_atr_mult"]) >= 3.0
        assert 1.5 in space["trail_atr_mult"]

    def test_build_trade_space_includes_trail_when_enabled(self):
        space = build_trade_space({"trail": True})
        assert "trail_after_tp1" in space and "trail_atr_mult" in space

    def test_build_trade_space_excludes_trail_when_disabled(self):
        space = build_trade_space({"tpsl": True, "trail": False})
        assert "trail_after_tp1" not in space and "trail_atr_mult" not in space


class TestApplyKeys:
    def test_opt_trade_keys_allow_trail_apply(self):
        # /api/optimizer/apply schreibt nur OPT_TRADE_KEYS in Live/Paper/Backtest-
        # Configs – Trail-Parameter müssen dabei sein, sonst geht "Übernehmen" verloren.
        assert "trail_after_tp1" in OPT_TRADE_KEYS
        assert "trail_atr_mult" in OPT_TRADE_KEYS


class TestSimulatorHonorsTrail:
    def test_backtester_reads_trail_cfg(self):
        # simulate_pair liest trail_after_tp1/trail_atr_mult aus cfg (Zeilen ~244)
        import inspect
        from services import backtester
        src = inspect.getsource(backtester.simulate_pair)
        assert "trail_after_tp1" in src and "trail_atr_mult" in src
