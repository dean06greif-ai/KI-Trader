"""Regressionstests 06/2026: Trendfilter-Indikatoren, ML-Gate-Label-Trefferquote,
Datensammel-Ausschluss in Tages-Statistik, Copilot-Regime-Kontext."""
import inspect

import numpy as np

from services import fast_sim, ml_gate, regime_cockpit
from strategies import custom_params as cp
from strategies.custom_strategy import INDICATORS, OPERATORS, INDICATOR_META, PERIOD_FIELDS

NEW_INDICATORS = ["supertrend", "supertrend_dir", "ema_slope_pct", "roc", "chop",
                  "aroon_up", "aroon_down", "williams_r"]


def _candles(n=600, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.1, 1, n)
    low = close - rng.uniform(0.1, 1, n)
    return [{"timestamp": int(1.7e12) + i * 900000, "open": float(close[i]), "high": float(high[i]),
             "low": float(low[i]), "close": float(close[i]), "volume": 50.0} for i in range(n)]


def test_new_indicators_registered_everywhere():
    for name in NEW_INDICATORS:
        assert name in INDICATORS
        assert name in INDICATOR_META
        assert name in cp.INDICATOR_PERIODS
        for pk in cp.INDICATOR_PERIODS[name]:
            assert pk in cp.PERIOD_RANGES, pk
    keys = {f["key"] for f in PERIOD_FIELDS}
    assert {"supertrend_period", "supertrend_mult", "roc_period", "chop_period",
            "aroon_period", "williams_period", "slope_lookback"} <= keys
    assert "supertrend_dir" in cp.BINARY_INDICATORS
    assert "adx" in INDICATORS and "plus_di" in INDICATORS


def test_new_indicators_compute_and_rules_work():
    fs = fast_sim.FastSeries(_candles())
    for name in NEW_INDICATORS:
        arr = fs.get(name, {})
        assert arr.shape == (600,)
        assert np.isfinite(arr[-50:]).all(), name
    d = fs.get("supertrend_dir", {})
    assert set(np.unique(d[-50:])) <= {1.0, -1.0}
    chop = fs.get("chop", {})
    assert np.nanmin(chop) >= 0 and np.nanmax(chop) <= 100
    wr = fs.get("williams_r", {})
    assert np.nanmin(wr) >= -100 and np.nanmax(wr) <= 0
    hits = fast_sim._rule_cond({"indicator": "price", "op": ">", "value": "supertrend"}, fs, {})
    dir_hits = fast_sim._rule_cond({"indicator": "supertrend_dir", "op": "==", "value": 1}, fs, {})
    assert int(hits.sum()) == int(dir_hits.sum()) > 0


def test_optimizer_search_space_includes_new_params():
    d = {"name": "t", "long_rules": [{"indicator": "supertrend_dir", "op": "==", "value": 1},
                                    {"indicator": "chop", "op": "<", "value": 50}],
         "short_rules": [{"indicator": "aroon_down", "op": ">", "value": 70}]}
    nd = cp.normalize_definition(d, INDICATORS, OPERATORS)
    nd = nd[0] if isinstance(nd, tuple) else nd
    meta = cp.build_param_meta(nd)
    assert {"supertrend_period", "supertrend_mult", "chop_period", "aroon_period"} <= set(meta)
    # binärer Supertrend-Richtungswert wird nicht als Schwelle optimiert
    assert "long1_value" not in meta and "long2_value" in meta


def test_ml_gate_label_hit_feature():
    assert "regime_label_hit_pct" in ml_gate.GATE_FEATURES
    assert "regime_hit_pct" in ml_gate.GATE_FEATURES
    src = inspect.getsource(ml_gate)
    assert 'f.get("regime_label_hit_pct")' in src


def test_cockpit_summary_has_per_label():
    cockpit = {"symbol": "BTCUSDT", "days": 14,
               "observer": {"hits": {"hit_pct": 55.0, "n": 40, "reliable": True,
                                     "per_label": [{"label": "trend_up", "hit_pct": 70.0, "n": 20, "reliable": True},
                                                   {"label": "range", "hit_pct": 40.0, "n": 5, "reliable": False}]},
                            "current": {"label": "trend_up"}},
               "structural": {}, "trades": [], "agreement": None}
    s = regime_cockpit.summary_of(cockpit)
    assert s["observer_per_label"]["trend_up"] == {"hit_pct": 70.0, "n": 20, "reliable": True}
    assert s["observer_per_label"]["range"]["reliable"] is False


def test_observer_label_hit_uses_reliable_only(monkeypatch):
    from services import ai_market_observer as mo
    obs = mo.MarketObserver.__new__(mo.MarketObserver)
    regime_cockpit._overview_cache["XUSDT:14"] = {"row": {"observer_per_label": {
        "trend_up": {"hit_pct": 70.0, "n": 20, "reliable": True},
        "range": {"hit_pct": 40.0, "n": 3, "reliable": False}}}}
    try:
        assert obs._label_hit_pct("XUSDT", "trend_up") == 70.0
        assert obs._label_hit_pct("XUSDT", "range") is None
        assert obs._label_hit_pct("XUSDT", None) is None
    finally:
        regime_cockpit._overview_cache.pop("XUSDT:14", None)


def test_daily_stats_exclude_data_collection():
    from core import scheduler
    from routers import analytics
    src = inspect.getsource(scheduler.perform_daily_reset)
    assert src.count('"data_collection": {"$ne": True}') >= 2
    src2 = inspect.getsource(analytics.reaggregate_daily_stats)
    assert src2.count('"data_collection": {"$ne": True}') >= 2


def test_copilot_context_contains_regime_block():
    import asyncio
    from services import strategy_copilot as sc
    text = asyncio.run(sc.copilot._context_block({"panel": "regime_lab",
                                                  "regime": {"detector": "kombi", "analysis": {"grade": "gut"}}}))
    assert "REGIME-LAB-STAND" in text and '"kombi"' in text
    assert "Grundgerüst" in sc.PANEL_PROMPTS["regime_lab"]
