"""Iter25: Regime-Nutzen (Plan 1.4) unit tests.

Autopilot Plateau/Duplicate/space_exhausted verhalten wird bereits durch
tests/test_pruefung_2409.py::test_autopilot_skips_duplicates_and_stops_on_plateau
und tests/test_worker_reconnect_and_regime_autopilot.py abgedeckt.
"""
import numpy as np
import pytest

from services import regime_utility as ru


pytestmark = pytest.mark.unit


def _mk_candles(closes):
    return [{"timestamp": i * 60_000, "close": float(c)} for i, c in enumerate(closes)]


def test_regime_utility_prescient_labels_positive_separation():
    rng = np.random.default_rng(42)
    n = 400
    returns = rng.normal(0, 0.005, n)
    closes = 100.0 * np.exp(np.cumsum(returns))
    candles = _mk_candles(closes)
    labels = []
    for i in range(n):
        if i + 1 >= n:
            labels.append(1)
            continue
        fwd = closes[i + 1] / closes[i] - 1.0
        labels.append(2 if fwd > 0.001 else (0 if fwd < -0.001 else 1))
    out = ru.compute(candles, labels, mode=3, bpd=1.0, train_end_ts=None)
    h1 = out["horizons"]["1d"]
    assert h1.get("separation_pct") is not None and h1["separation_pct"] > 0.1, h1
    assert h1.get("sign_hit_pct") is not None and h1["sign_hit_pct"] > 90.0, h1


def test_regime_utility_random_labels_no_meaningful_separation():
    rng = np.random.default_rng(7)
    n = 600
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    candles = _mk_candles(closes)
    labels = rng.choice([0, 1, 2], size=n).tolist()
    out = ru.compute(candles, labels, mode=3, bpd=1.0, train_end_ts=None)
    h1 = out["horizons"]["1d"]
    if h1.get("separation_pct") is not None:
        assert abs(h1["separation_pct"]) < 0.3, h1
    if h1.get("sign_hit_pct") is not None:
        assert 35.0 < h1["sign_hit_pct"] < 65.0, h1


def test_regime_utility_short_input_returns_empty():
    candles = _mk_candles([100, 101, 102])
    assert ru.compute(candles, [2, 2, 2], mode=3, bpd=1.0) == {}


def test_regime_utility_summary_shape():
    rng = np.random.default_rng(1)
    n = 300
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.005, n)))
    candles = _mk_candles(closes)
    labels = rng.choice([0, 1, 2], size=n).tolist()
    out = ru.compute(candles, labels, mode=3, bpd=1.0)
    s = ru.summary(out, "3d")
    for k in ("utility_separation_pct", "utility_sign_hit_pct",
              "utility_side_range_ratio", "utility_basis"):
        assert k in s
