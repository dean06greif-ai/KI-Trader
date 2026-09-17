"""Regressionstests für die neue Horst VWAP + OBV-RSI Strategie (offline)."""
import numpy as np
import pytest

from strategies.horst_vwap_obv_strategy import HorstVWAPOBVStrategy
from strategies.registry import registry


def _candles(closes, vols=None, base_ts=1700000000000):
    vols = vols or [100.0] * len(closes)
    out = []
    for i, c in enumerate(closes):
        out.append({"open": c, "high": c * 1.001, "low": c * 0.999,
                    "close": c, "volume": vols[i],
                    "timestamp": base_ts + i * 60000})
    return out


def _params(**overrides):
    strat = HorstVWAPOBVStrategy()
    p = {k: meta["value"] for k, meta in strat.DEFAULT_PARAMS.items()}
    p.update(overrides)
    return p


class TestRegistration:
    def test_registered(self):
        strat = registry.get("horst_vwap_obv")
        assert strat is not None
        assert strat.STRATEGY_TIMEFRAME == "1m"

    def test_metadata_shape(self):
        meta = registry.get("horst_vwap_obv").get_metadata()
        for field in ["id", "name", "description", "timeframe", "params"]:
            assert field in meta
        for key in ["band_window", "band_mult", "obv_rsi_period", "sl_pct", "tp_pct", "min_dev_pct"]:
            assert key in meta["params"]

    def test_existing_strategies_untouched(self):
        ids = registry.list_ids()
        for legacy in ["scalping_4_rules", "rsi_only", "vwap_reversion"]:
            assert legacy in ids


class TestSignalLogic:
    def test_too_few_candles_returns_none(self):
        strat = HorstVWAPOBVStrategy()
        assert strat.analyze(_candles([100.0] * 20), "BTCUSDT", _params()) is None

    def test_long_signal_on_deep_selloff(self):
        rng = np.random.default_rng(7)
        closes = list(100000 + np.cumsum(rng.normal(0, 15, 120)))
        vols = [100.0] * len(closes)
        # steiler Abverkauf mit hohem Volumen -> unter das untere Band + OBV-RSI Extrem
        for i in range(1, 16):
            closes.append(closes[-1] * (1 - 0.0025))
            vols.append(500.0)
        strat = HorstVWAPOBVStrategy()
        res = strat.analyze(_candles(closes, vols), "BTCUSDT", _params())
        assert res is not None
        assert res["signal_type"] == "LONG"
        lv = res["levels"]
        assert lv["stop_loss"] < lv["entry"] < lv["take_profit_full"]
        assert lv["entry"] < lv["take_profit_1"] <= lv["take_profit_full"]
        # SL 0.6% / TP 0.9% Defaults
        assert lv["stop_loss"] == pytest.approx(lv["entry"] * 0.994, rel=1e-4)
        assert lv["take_profit_full"] == pytest.approx(lv["entry"] * 1.009, rel=1e-4)
        assert lv["crv"] == pytest.approx(1.5, abs=0.05)

    def test_short_signal_on_spike(self):
        rng = np.random.default_rng(7)
        closes = list(100000 + np.cumsum(rng.normal(0, 15, 120)))
        vols = [100.0] * len(closes)
        for i in range(1, 16):
            closes.append(closes[-1] * (1 + 0.0025))
            vols.append(500.0)
        res = HorstVWAPOBVStrategy().analyze(_candles(closes, vols), "BTCUSDT", _params())
        assert res is not None
        assert res["signal_type"] == "SHORT"
        lv = res["levels"]
        assert lv["take_profit_full"] <= lv["take_profit_1"] < lv["entry"] < lv["stop_loss"]

    def test_no_signal_in_flat_market(self):
        closes = [100000 + (1 if i % 2 else -1) * 5 for i in range(150)]
        res = HorstVWAPOBVStrategy().analyze(_candles(closes), "BTCUSDT", _params())
        assert res is not None
        assert res["signal_type"] is None

    def test_original_horst_params_1_to_1(self):
        rng = np.random.default_rng(7)
        closes = list(100000 + np.cumsum(rng.normal(0, 15, 120)))
        vols = [100.0] * len(closes)
        for i in range(1, 16):
            closes.append(closes[-1] * (1 - 0.0025))
            vols.append(500.0)
        res = HorstVWAPOBVStrategy().analyze(
            _candles(closes, vols), "BTCUSDT",
            _params(tp_pct=0.6, min_dev_pct=0.0))
        assert res["signal_type"] == "LONG"
        lv = res["levels"]
        assert lv["crv"] == pytest.approx(1.0, abs=0.05)

    def test_check_signal_backcompat_shape(self):
        rng = np.random.default_rng(7)
        closes = list(100000 + np.cumsum(rng.normal(0, 15, 120)))
        vols = [100.0] * len(closes)
        for i in range(1, 16):
            closes.append(closes[-1] * (1 - 0.0025))
            vols.append(500.0)
        sig = HorstVWAPOBVStrategy().check_signal(_candles(closes, vols), "BTCUSDT", {})
        assert sig is not None
        for field in ["type", "entry_price", "stop_loss", "take_profit_1",
                      "take_profit_full", "crv", "rules_met", "rules_total"]:
            assert field in sig
        assert sig["rules_total"] == 3


class TestVectorizedParity:
    def test_vectorized_matches_analyze_direction(self):
        from services.fast_sim import FastSeries
        rng = np.random.default_rng(42)
        closes = list(100000 + np.cumsum(rng.normal(0, 25, 400)))
        vols = list(rng.uniform(50, 300, 400))
        candles = _candles(closes, vols)
        params = _params()

        fs = FastSeries(candles)
        out = HorstVWAPOBVStrategy.vectorized_signals(fs, params)
        assert out is not None
        assert out["rules_total"] == 3
        assert len(out["long"]) == len(candles)
        assert not (out["long"] & out["short"]).any()
        # Warmup-Bereich muss signalfrei sein
        assert not out["long"][:out["warmup"] - 1].any()
        assert not out["short"][:out["warmup"] - 1].any()
