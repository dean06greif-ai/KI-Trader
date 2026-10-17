"""Regressionstests Volatility Squeeze Breakout – ohne Netzwerk."""
import numpy as np

from services import fast_sim
from services.candles import CandleArray
from strategies.registry import registry
from strategies.vol_squeeze_breakout_strategy import _rolling_pct_rank, _signals

H = 3_600_000


def _series(n=900, seed=3):
    rng = np.random.default_rng(seed)
    cl = 100 * np.exp(np.cumsum(rng.normal(0.0003, 0.01, n)))
    # Kompression, dann Ausbruch nach oben
    cl[680:700] = cl[679] * (1 + rng.normal(0, 0.0005, 20))
    cl[700:720] = cl[699] * np.linspace(1.01, 1.08, 20)
    hi, lo = cl * 1.002, cl * 0.998
    vol = np.full(n, 100.0)
    vol[700:720] = 400.0
    ts = (1_700_000_000_000 // H * H + np.arange(n) * H).astype(np.int64)
    return ts, cl.copy(), hi, lo, cl, vol


def test_registered_with_recommended_cfg():
    s = registry.get("vol_squeeze_breakout")
    assert s.STRATEGY_TIMEFRAME == "1h"
    assert s.get_metadata()["recommended_trade_cfg"]["atr_sl_multiplier"] == 3.0


def test_rolling_pct_rank():
    r = _rolling_pct_rank(np.arange(10, dtype=float), 5)
    assert np.isnan(r[3]) and r[4] == 1.0 and r[-1] == 1.0


def test_breakout_after_squeeze_gives_long():
    ts, op, hi, lo, cl, vol = _series()
    p = registry.get("vol_squeeze_breakout").get_params({})
    s = _signals(ts, op, hi, lo, cl, vol, {**p, "trend_filter": 0})
    assert s["long"][700:720].any()
    assert not s["short"][700:720].any()


def test_no_lookahead_signals_identical_on_prefix():
    ts, op, hi, lo, cl, vol = _series()
    p = registry.get("vol_squeeze_breakout").get_params({})
    full = _signals(ts, op, hi, lo, cl, vol, p)
    cut = _signals(ts[:710], op[:710], hi[:710], lo[:710], cl[:710], vol[:710], p)
    assert np.array_equal(full["long"][:710], cut["long"])


def test_live_analyze_matches_fast_path():
    ts, op, hi, lo, cl, vol = _series()
    strat = registry.get("vol_squeeze_breakout")
    p = {**strat.get_params({}), "trend_filter": 0}
    ca = CandleArray(ts, op, hi, lo, cl, vol)
    vs = strat.vectorized_signals(fast_sim.FastSeries(ca), p, "TESTUSDT")
    candles = [{"timestamp": int(ts[i]), "open": op[i], "high": hi[i], "low": lo[i],
                "close": cl[i], "volume": vol[i]} for i in range(len(ts))]
    for i in range(690, 725):
        res = strat.analyze(candles[:i + 1], "TESTUSDT", p)
        assert (res["signal_type"] == "LONG") == bool(vs["long"][i])
        if res["signal_type"]:
            assert res["levels"]["stop_loss"] < res["levels"]["entry"]
