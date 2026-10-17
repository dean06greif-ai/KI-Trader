"""Regressionstests TradeX VWAP Scalping + generische Backtester-Erweiterungen
(Zeit-Exit, Limit-Entry/Maker-Fee, TP-Limit, Richtung als String) – ohne Netzwerk."""
import numpy as np

from services import market_positioning as mp
from services.backtester import simulate_pair
from strategies.registry import registry
from strategies.tradex_vwap_scalping_strategy import vwap_bands

DAY = 86_400_000
MIN = 60_000


def _candles(prices, start=1_704_067_200_000, vol=1.0):
    return [{"timestamp": start + i * MIN, "open": p, "high": p * 1.0005, "low": p * 0.9995,
             "close": p, "volume": vol} for i, p in enumerate(prices)]


class _Sig:
    """Ein Signal an Kerze k (LONG), Entry = Schluss (bzw. limit)."""
    STRATEGY_ID = "t"

    def __init__(self, k, limit=None):
        self.k, self.limit = k, limit

    def provider(self, candles):
        def p(i):
            if i == self.k:
                e = candles[i]["close"]
                return {"type": "LONG", "signal_class": "SIGNAL", "entry_price": e,
                        "limit_price": self.limit or e, "rules_met_count": 1, "rules_total": 1}
            return None
        return p


BASE_CFG = {"max_capital": 100, "leverage": 1, "fee_percent": 0.06, "tp_mode": "fixed_pct",
            "tp1_percent": 5, "tp_full_percent": 5, "tp1_close_percent": 100, "sl_mode": "fixed",
            "sl_fixed_percent": 5, "be_mode": "off", "trail_after_tp1": False, "min_risk_percent": 0.1}


def test_strategy_registered_with_metadata():
    s = registry.get("tradex_vwap_scalping")
    meta = s.get_metadata()
    assert meta["requires_positioning"] is True
    assert meta["recommended_trade_cfg"]["max_hold_minutes"] == 360
    assert s.get_params({})["trade_short"] == 0
    assert registry.get("horst_vwap_obv") is not None     # Original bleibt unverändert


def test_session_vwap_resets_daily_and_first_partial_day_nan():
    ts = np.arange(0, 3 * 1440) * MIN + DAY + 600 * MIN      # Start mitten am Tag
    c = 100 + np.sin(np.arange(len(ts)) / 50)
    vw, lo, up = vwap_bands(ts, c + 0.1, c - 0.1, c, np.ones(len(ts)), 2.0, session=True,
                            min_session_bars=30)
    first_day = ts // DAY == ts[0] // DAY
    assert np.all(np.isnan(vw[first_day]))
    day2 = np.where(ts // DAY == ts[0] // DAY + 1)[0]
    assert np.all(np.isnan(vw[day2[:29]])) and np.isfinite(vw[day2[29]])
    assert np.all(lo[day2[40:]] <= vw[day2[40:]]) and np.all(up[day2[40:]] >= vw[day2[40:]])


def test_session_vwap_no_lookahead():
    ts = np.arange(0, 2 * 1440) * MIN + DAY
    c = 100 + np.cumsum(np.random.default_rng(1).normal(0, 0.05, len(ts)))
    full = vwap_bands(ts, c, c, c, np.ones(len(ts)), 2.0)[0]
    cut = vwap_bands(ts[:2000], c[:2000], c[:2000], c[:2000], np.ones(2000), 2.0)[0]
    assert np.allclose(full[:2000], cut, equal_nan=True)


def test_positioning_asof_uses_only_past_values():
    src_ts = np.array([1000, 2000, 3000], dtype=np.int64)
    val = np.array([1.0, 2.0, 3.0])
    out = mp.asof(src_ts, val, np.array([999, 1000, 2500, 3000, 99999]), max_age_ms=5000)
    assert np.isnan(out[0]) and out[1] == 1.0 and out[2] == 2.0 and out[3] == 3.0
    assert np.isnan(out[4])                                  # zu alt -> keine Daten


def test_rolling_percentile_window():
    ts = (np.arange(10) * 8 * 3_600_000).astype(np.int64)
    pct = mp.rolling_pct(ts, np.arange(10, dtype=float), 1)
    assert pct[0] == 1.0 and pct[-1] == 1.0                   # steigend -> immer oberstes Perzentil


def test_time_exit_closes_after_max_hold():
    candles = _candles([100.0] * 700)
    res = simulate_pair(None, candles, "BTCUSDT", {}, {**BASE_CFG, "max_hold_minutes": 60},
                        collect_trades=True, signal_provider=_Sig(100).provider(candles))
    t = res["all_trades"][0]
    assert t["time_exit"] is True and res["time_exits"] == 1
    assert 59 <= t["duration_min"] <= 61


def test_limit_entry_fills_with_maker_fee_and_expires():
    candles = _candles([100.0] * 300)
    cfg = {**BASE_CFG, "entry_order_type": "limit", "limit_expiry_bars": 3, "maker_fee_percent": 0.02,
           "max_hold_minutes": 30}
    res = simulate_pair(None, candles, "BTCUSDT", {}, cfg, collect_trades=True,
                        signal_provider=_Sig(100, limit=99.99).provider(candles))
    t = res["all_trades"][0]
    assert t["entry_order"] == "limit" and t["entry"] == 99.99
    qty = 100 / 99.99
    assert abs(t["fees"] - (99.99 * qty * 0.0002 + t["exit"] * qty * 0.0006)) < 1e-3
    # Limit weit unter dem Markt -> verfällt, kein Trade
    res2 = simulate_pair(None, candles, "BTCUSDT", {}, cfg, collect_trades=True,
                         signal_provider=_Sig(100, limit=90.0).provider(candles))
    assert res2["trades"] == 0 and res2["limit_expired"] == 1


def test_tp_limit_uses_maker_fee():
    prices = [100.0] * 150 + [106.0] * 50
    candles = _candles(prices)
    cfg = {**BASE_CFG, "tp_order_type": "limit", "maker_fee_percent": 0.02}
    t = simulate_pair(None, candles, "BTCUSDT", {}, cfg, collect_trades=True,
                      signal_provider=_Sig(100).provider(candles))["all_trades"][0]
    qty = 1.0
    assert t["result"] == "win"
    assert abs(t["fees"] - (100 * qty * 0.0006 + 105 * qty * 0.0002)) < 1e-3


def test_allowed_sides_string_blocks_other_side():
    candles = _candles([100.0] * 300)
    res = simulate_pair(None, candles, "BTCUSDT", {}, {**BASE_CFG, "allowed_sides": "SHORT"},
                        signal_provider=_Sig(100).provider(candles))
    assert res["trades"] == 0


def test_default_market_behaviour_unchanged():
    candles = _candles([100.0] * 150 + [106.0] * 50)
    t = simulate_pair(None, candles, "BTCUSDT", {}, BASE_CFG, collect_trades=True,
                      signal_provider=_Sig(100).provider(candles))["all_trades"][0]
    assert t["entry_order"] == "market" and t["time_exit"] is False and t["result"] == "win"
