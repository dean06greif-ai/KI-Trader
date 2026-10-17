"""Regressionstests: Seeding-Automatik (services/setup_backtest/auto.py) und
Feintuning (detectors.tune_candidates, runner.tune / run_variant mit params)."""
import os
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import setup_asset_class as ac  # noqa: E402
from services.candles import CandleArray  # noqa: E402
from services.setup_backtest import auto, detectors, runner  # noqa: E402
from services.setup_backtest.detectors import Features  # noqa: E402

M1 = 60_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % (5 * M1))


# ---------------------------------------------------------------- Automatik-Einstellungen
def test_auto_normalize_defaults_and_bounds():
    cfg = auto.normalize(None)
    assert cfg["enabled"] is False and cfg["interval_hours"] == 24 and cfg["days"] == 90
    assert cfg["mode"] == "loop" and cfg["asset_classes"] == list(ac.CLASSES)
    cfg = auto.normalize({"enabled": 1, "interval_hours": 1, "days": 9999, "mode": "single",
                          "asset_classes": ["crypto", "quatsch"]})
    assert cfg["enabled"] is True and cfg["interval_hours"] == 6 and cfg["days"] == 365
    assert cfg["mode"] == "single" and cfg["asset_classes"] == ["crypto"]
    assert auto.normalize({"interval_hours": "abc"})["interval_hours"] == 24


def test_auto_due_and_next_run():
    now = datetime(2026, 9, 6, 12, 0, tzinfo=timezone.utc)
    assert not auto.is_due({"enabled": False}, now)
    assert auto.is_due(auto.normalize({"enabled": True}), now)            # noch nie gelaufen
    cfg = auto.normalize({"enabled": True, "interval_hours": 24,
                          "last_run_at": (now - timedelta(hours=23)).isoformat()})
    assert not auto.is_due(cfg, now)
    cfg["last_run_at"] = (now - timedelta(hours=25)).isoformat()
    assert auto.is_due(cfg, now)
    assert auto.next_run_at(cfg).startswith((now - timedelta(hours=1)).isoformat()[:13])
    assert auto.next_run_at({"enabled": False}) is None
    pub = auto.public_state({"enabled": True, "last_run_at": "kaputt"})
    assert pub["due"] is True and pub["next_run_at"]


# ---------------------------------------------------------------- Feintuning
def test_tune_candidates_vary_only_risk_keys():
    base = detectors.VARIANTS["breakout"][0]
    cands = detectors.tune_candidates("breakout", base)
    assert len(cands) == 4
    for c in cands:
        assert c["lookback"] == base["lookback"] and c["vol_mult"] == base["vol_mult"]
        assert c["sl_atr"] in (round(base["sl_atr"] * 0.8, 3), round(base["sl_atr"] * 1.25, 3))
        assert c["name"].startswith("standard·")
    # nur ein Tuning-Schlüssel -> 2 Kandidaten; keiner -> leer
    assert len(detectors.tune_candidates("range_fade", detectors.VARIANTS["range_fade"][0])) == 2
    assert detectors.tune_candidates("x", {"name": "n", "foo": 1}) == []


def _ca(closes, spread=0.001, vol=None):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    op = np.concatenate([[closes[0]], closes[:-1]])
    hi = np.maximum(op, closes) * (1 + spread)
    lo = np.minimum(op, closes) * (1 - spread)
    v = np.full(n, 100.0) if vol is None else np.asarray(vol, dtype=float)
    return CandleArray(T0 + np.arange(n) * M1, op, hi, lo, closes, v)


def _flat_then_breakout(n_flat=1500, level=100.0):
    rng = np.random.default_rng(1)
    flat = level + rng.normal(0, 0.05, n_flat)
    spike = np.linspace(level, level * 1.03, 40)
    after = level * 1.03 + rng.normal(0, 0.05, 300)
    vol = np.concatenate([np.full(n_flat, 100.0), np.full(40, 500.0), np.full(300, 100.0)])
    return _ca(np.concatenate([flat, spike, after]), vol=vol)


def test_run_variant_with_params_and_tune_shape():
    f = Features(_flat_then_breakout())
    split = int(f.c5.ts[0] + (f.c5.ts[-1] - f.c5.ts[0]) * 0.5)
    params = dict(detectors.VARIANTS["breakout"][0], name="standard·test")
    res = runner.run_variant("breakout", {"QQQUSDT": f}, 0, ac.INDICES, split, "job", params=params)
    assert res["variant_name"] == "standard·test" and res["params"] == params
    base = runner.run_variant("breakout", {"QQQUSDT": f}, 0, ac.INDICES, split, "job")
    assert base["params"] is None and base["variants_total"] == 3
    tr = runner.tune("breakout", {"QQQUSDT": f}, 0, ac.INDICES, split, "job")
    assert tr["tried"] == 4 and (tr["best"] is None or tr["best"]["passed"])


def test_best_base_variant_and_passed_states():
    hist = [{"variant": 0, "is": {"trades": 12, "pnl": -1.0}},
            {"variant": 2, "is": {"trades": 30, "pnl": 3.5}},
            {"variant": 1, "is": {"trades": 2, "pnl": 9.0}}]       # zu wenig Trades
    assert runner.best_base_variant(hist) == 2
    assert runner.best_base_variant([{"variant": 1, "is": {"trades": 1, "pnl": 1}}]) is None
    assert "tuned" in runner.PASSED_STATES and "passed" in runner.PASSED_STATES
    # feingetunte Setups zählen NICHT als "ohne Edge"
    assert runner.no_edge_line("X", {"a": {"status": "tuned"}, "b": {"status": "exhausted"}}) == \
        runner.no_edge_line("X", {"b": {"status": "exhausted"}})


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
