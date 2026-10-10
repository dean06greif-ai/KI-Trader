"""Regressionstests 09/2026: Trendfolge 2 (Detektor + Live-Signal), adaptive
Mindest-Trades, Setup-Review (chronisch/inaktiv), News-Impuls-Symbole,
Runner-A/B bei Trendfolge, Scanner-Puffer für SMC-Zonen (order_block)."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services import runner_policy, setup_review, tf2_signal, vec  # noqa: E402
from services import ai_playbook  # noqa: E402
from services.ai_news_watcher import news_symbols  # noqa: E402
from services.candles import CandleArray  # noqa: E402
from services.setup_backtest import detectors, revise, runner  # noqa: E402

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# Synthetische Kerzen: Seitwärts, dann Impuls nach oben mit Volumen
# --------------------------------------------------------------------------
def _impulse_candles(n_flat: int = 900, n_up: int = 120, seed: int = 3):
    rng = np.random.default_rng(seed)
    px = 100.0
    rows = []
    ts = 1_700_000_000_000
    for i in range(n_flat + n_up):
        drift = 0.0 if i < n_flat else 0.06
        o = px
        c = px * (1 + drift / 100 + rng.normal(0, 0.0004))
        h, l = max(o, c) * (1 + 0.0003), min(o, c) * (1 - 0.0003)
        vol = 100.0 * (3.0 if i >= n_flat else 1.0)
        rows.append({"timestamp": ts + i * 60_000, "open": o, "high": h, "low": l,
                     "close": c, "volume": vol})
        px = c
    return rows


def _to_array(rows):
    return CandleArray(np.array([r["timestamp"] for r in rows], dtype=np.int64),
                       np.array([r["open"] for r in rows]), np.array([r["high"] for r in rows]),
                       np.array([r["low"] for r in rows]), np.array([r["close"] for r in rows]),
                       np.array([r["volume"] for r in rows]))


def test_macd_no_nan_after_warmup():
    close = np.linspace(100, 110, 80)
    line, sig = vec.macd(close)
    assert np.isnan(sig[20]) and not np.isnan(line[40]) and not np.isnan(sig[40])


def test_detect_trend_follow2_finds_long_on_impulse():
    f = detectors.Features(_to_array(_impulse_candles()))
    sigs = detectors.detect_trend_follow2(f, {**detectors.VARIANTS["trend_follow2"][2], "chg_pct": 0.2})
    assert sigs, "Impuls mit Volumen + MACD-Kreuz muss ein TF2-Signal liefern"
    s = sigs[0]
    assert s.side == "LONG" and s.sl < s.entry < s.tp1 <= s.tpf
    # Ziel = tp_r x Risiko
    assert abs((s.tpf - s.entry) / (s.entry - s.sl) - 2.5) < 1e-6


def test_trend_follow2_registered_everywhere():
    assert "trend_follow2" in detectors.DETECTORS and len(detectors.VARIANTS["trend_follow2"]) == 3
    assert "trend_follow2" in ai_playbook.SETUPS
    assert ai_playbook.normalize_setup("TF2") == "trend_follow2"
    assert ai_playbook.normalize_setup("Trendfolge2") == "trend_follow2"
    assert ai_playbook.normalize_setup("trend_follow") == "trend_follow"
    assert "sl_lookback" in revise.INT_KEYS
    assert "chg_pct" in detectors.param_help("trend_follow2")


def test_tf2_signal_live_detect_and_context_line():
    rows = _impulse_candles()
    # Signal irgendwo im Impuls: Kreuz auf einer der Kerzen -> context_line/detect_last
    found = None
    for cut in range(len(rows) - 300, len(rows)):
        sig = tf2_signal.detect_last(rows[:cut], {"vol_mult": 1.0, "chg_pct": 0.1})
        if sig:
            found = (cut, sig)
            break
    assert found, "TF2-Live-Detektor muss im Impuls ein Signal finden"
    cut, sig = found
    assert sig["side"] == "LONG" and sig["sl"] < sig["entry"] < sig["tpf"]
    line = tf2_signal.context_line(rows[:cut], rows[cut - 1]["close"], {"vol_mult": 1.0, "chg_pct": 0.1})
    assert line and line.startswith("TF2-Signal LONG")
    # Ohne Impuls: keine Zeile (Token-Budget)
    assert tf2_signal.context_line(rows[:800], rows[799]["close"]) is None


def test_tf2_budget_and_clamp():
    b = tf2_signal.TF2Budget()
    cfg = dict(tf2_signal.DEFAULTS)
    assert b.allowed("BTCUSDT", cfg, now=100000.0) is None
    b.fire("BTCUSDT", {"side": "LONG"}, now=100000.0)
    assert "Cooldown" in b.allowed("BTCUSDT", cfg, now=100010.0)
    cfg2 = {}
    tf2_signal.clamp_updates({"tf2_trigger_daily_cap": 999, "tf2_trigger_enabled": 0}, cfg2)
    assert cfg2 == {"tf2_trigger_enabled": False, "tf2_trigger_daily_cap": 100}


# --------------------------------------------------------------------------
# Adaptive Mindest-Trades
# --------------------------------------------------------------------------
def test_min_trades_scales_with_symbols_and_days():
    # 2 Indizes / 90 Tage -> alte Untergrenzen; 11 Kryptos / 365 Tage -> Deckel
    small = runner.min_trades_for("trend_follow", 2, 90)
    big = runner.min_trades_for("trend_follow", 11, 365)
    assert small == {"is": runner.MIN_IS_TRADES, "oos": runner.MIN_OOS_TRADES, "expected": 12, "factor": 1.0}
    assert big == {"is": runner.MIN_TRADES_CAP[0], "oos": 32, "expected": 268, "factor": 1.0}
    mid = runner.min_trades_for("trend_follow", 11, 180)
    assert mid == {"is": 37, "oos": 16, "expected": 132, "factor": 1.0}
    # KI-Faktor wird geklemmt
    assert runner.min_trades_for("trend_follow", 8, 180, 5.0)["factor"] == 1.5
    assert runner.min_trades_for("trend_follow", 8, 180, 0.1)["factor"] == 0.7


def test_passed_uses_adaptive_requirement():
    is_st = {"trades": 15, "wins": 9, "pnl": 30.0, "winrate": 60}
    oos_st = {"trades": 12, "wins": 7, "pnl": 12.0, "winrate": 58}
    assert runner.passed(is_st, oos_st) is True
    assert runner.passed(is_st, oos_st, {"is": 40, "oos": 20}) is False


def test_revise_sanitize_accepts_min_trades_factor():
    base = dict(detectors.VARIANTS["trend_follow2"][0])
    out = revise.sanitize("trend_follow2", {"min_trades_factor": 3.0, "sl_lookback": 14.6}, base, 1)
    assert out["min_trades_factor"] == 1.5 and out["sl_lookback"] == 15
    rules = runner.rules_for_prompt({"is": 25, "oos": 14, "expected": 120})
    assert "25" in revise.build_prompt("crypto", "trend_follow2", "x", base, [], rules)


# --------------------------------------------------------------------------
# Setup-Review
# --------------------------------------------------------------------------
def test_chronic_reason_rules():
    bad = {"trades": 20, "wins": 6, "pnl": -80.0, "winrate": 30}
    assert setup_review.chronic_reason(bad, []) is not None
    assert setup_review.chronic_reason({"trades": 5, "wins": 0, "pnl": -50.0, "winrate": 0}, []) is None
    assert setup_review.chronic_reason({"trades": 20, "wins": 12, "pnl": -5.0, "winrate": 60}, []) is None
    # eine positive Version mit genug Trades -> nicht chronisch
    versions = [{"stats": {"trades": 6, "pnl": 12.0}}]
    assert setup_review.chronic_reason(bad, versions) is None
    assert setup_review.chronic_reason(bad, [{"stats": {"trades": 2, "pnl": 5.0}}]) is not None


def test_review_due_and_budget():
    now = datetime(2026, 9, 12, 12, tzinfo=timezone.utc)
    assert setup_review.is_due(None, now)
    assert not setup_review.is_due({"last_run": (now - timedelta(hours=3)).isoformat()}, now)
    assert setup_review.is_due({"last_run": (now - timedelta(hours=25)).isoformat()}, now)
    assert setup_review.llm_budget_left({"llm_day": "2026-09-12", "llm_used": 2}, now) == 1
    assert setup_review.llm_budget_left({"llm_day": "2026-09-11", "llm_used": 3}, now) == 3
    assert setup_review.inactive_old_enough({"at": (now - timedelta(days=3)).isoformat()}, now)
    assert not setup_review.inactive_old_enough({"at": (now - timedelta(days=1)).isoformat()}, now)


def test_review_prompt_compact():
    p = setup_review.build_prompt("crypto", "breakout", "Regel", "inaktiv", "nur 0 Trades",
                                  {"trades": 3, "winrate": 33, "pnl": -4.0}, ["SL zu eng"], ["L1"], "alt")
    assert "INAKTIV" in p and "SL zu eng" in p and len(p) < 1500


# --------------------------------------------------------------------------
# News-Impuls-Symbole, Runner-A/B, Scanner-Puffer
# --------------------------------------------------------------------------
def test_news_symbols_mapping():
    syms = ["BTCUSDT", "ETHUSDT", "GOLD", "OIL", "EURUSD", "USDJPY", "QQQUSDT"]
    ev = [{"affects": ["BTC", "XAU", "USD", "Nasdaq", "WTI", "EUR/USD", "SOL"]}]
    assert news_symbols(ev, syms) == ["BTCUSDT", "GOLD", "QQQUSDT", "OIL"]
    assert news_symbols([{"affects": ["USD", "FX"]}], syms) == []


def test_runner_trend_ab_split():
    cfg = {"runner_scalp_enabled": True, "runner_scalp_news_only": True,
           "runner_trend_setups": True, "runner_trend_share": 0.5}
    decs = [{"id": f"dec-{i}", "runner": True, "setup": "trend_follow2", "news_impact": "neutral"}
            for i in range(200)]
    hits = sum(runner_policy.runner_allowed(d, cfg) for d in decs)
    assert 60 < hits < 140, "A/B-Anteil ~50 %"
    assert not runner_policy.runner_allowed({**decs[0], "setup": "range_fade"}, cfg)
    assert not runner_policy.runner_allowed(decs[0], {**cfg, "runner_trend_setups": False})
    assert runner_policy.runner_allowed({**decs[0], "news_impact": "positive"}, cfg)
    # deterministisch
    assert runner_policy.runner_allowed(decs[7], cfg) == runner_policy.runner_allowed(decs[7], cfg)


def test_scanner_buffer_min_for_ai_trader():
    from services.strategy_scanner import AI_TRADER_MIN_BUFFER, StrategyScanner
    sc = StrategyScanner.__new__(StrategyScanner)
    sc.settings = {"enabled_strategies": ["ai_trader"], "strategy_timeframes": {}}
    sc.enabled_strategies = lambda: ["ai_trader"]
    sc.strategy_timeframe = lambda sid: "1m"
    sc.rule_timeframe_minutes = lambda sid: 0
    assert sc.buffer_limit() == AI_TRADER_MIN_BUFFER >= 60 * 60
    sc.enabled_strategies = lambda: ["custom_x"]
    assert sc.buffer_limit() == 220
