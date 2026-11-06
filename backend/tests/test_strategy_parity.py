"""T4: Strategie-Pfad-Parität – für jede Built-in-Strategie mit Fast-Path
(`vectorized_signals`) muss `fast_sim.build_builtin_signal_provider(...)` auf
identischen Kerzen dieselben Signale liefern wie der Live-/Referenzpfad
`strategy.check_signal(candles[:i+1], ...)` (klärt Befund F5/F10).

Property-artiger Test: deterministische Zufalls-Kerzen (mehrere Seeds/Trends),
Vergleich von (type, signal_class, entry_price) an JEDEM Index. Zusätzlich
Wächter gegen einen vakuosen Durchlauf (irgendwo müssen Signale feuern).
"""
import random
import time

import pytest

from services import fast_sim
from strategies.registry import StrategyRegistry

N_CANDLES = 260          # <= Backtester-WINDOW+1: Prefix == Fenster, keine EMA-Drift
WARMUP_MIN = 60
SEEDS = [(1, 0.0006), (2, -0.0006), (3, 0.0), (4, 0.001), (5, -0.001)]

# Nicht-Default-Parameter: Fast-Path muss dieselben Overrides anwenden
PARAM_OVERRIDES = {
    "strategy_params": {
        "ema_pullback_scalping": {"require_mid_ema_touch": 1, "ema_fast_period": 20,
                                  "pullback_lookback": 6},
        "rsi_only": {"rsi_period": 7, "rsi_oversold": 40, "rsi_overbought": 60},
        "bollinger_reversion": {"bb_period": 14, "bb_std": 1.5},
        "stoch_reversal": {"stoch_k": 9, "oversold": 30, "overbought": 70},
    }
}

_registry = StrategyRegistry()
VECTORIZED_IDS = sorted(
    sid for sid in _registry.list_ids()
    if callable(getattr(_registry.get(sid), "vectorized_signals", None)))

# Zähler über alle Parametrisierungen: am Ende muss ES signale gegeben haben
_signal_counter = {"n": 0}


def make_candles(n, seed, trend=0.0):
    random.seed(seed)
    out, ts, price = [], int(time.time() * 1000) - n * 60_000, 100.0
    for i in range(n):
        o = price
        price = max(1.0, price * (1 + random.gauss(trend, 0.004)))
        hi = max(o, price) * (1 + abs(random.gauss(0, 0.0015)))
        lo = min(o, price) * (1 - abs(random.gauss(0, 0.0015)))
        out.append({"timestamp": ts + i * 60_000, "open": o, "close": price,
                    "high": hi, "low": lo, "volume": random.uniform(10, 500)})
    return out


def _sig_key(sig):
    if not isinstance(sig, dict):
        return None
    return (sig.get("type"), sig.get("signal_class"))


def _compare(strategy, settings):
    mismatches, signal_points = [], 0
    for seed, trend in SEEDS:
        candles = make_candles(N_CANDLES, seed, trend)
        fs = fast_sim.FastSeries(candles)
        provider = fast_sim.build_builtin_signal_provider(
            strategy, fs, settings, "BTCUSDT")
        assert provider is not None, \
            f"{strategy.STRATEGY_ID}: vectorized_signals baut keinen Provider"
        for i in range(WARMUP_MIN, N_CANDLES):
            fast_sig = provider(i)
            slow_sig = strategy.check_signal(candles[:i + 1], "BTCUSDT", settings)
            fk, sk = _sig_key(fast_sig), _sig_key(slow_sig)
            if fk or sk:
                signal_points += 1
            if fk != sk:
                mismatches.append((seed, i, fk, sk))
                continue
            if fk and slow_sig.get("entry_price"):
                # Entry = Close der Signalkerze in beiden Pfaden
                assert fast_sig["entry_price"] == pytest.approx(
                    float(slow_sig["entry_price"]), rel=1e-6), \
                    (strategy.STRATEGY_ID, seed, i)
    _signal_counter["n"] += signal_points
    assert not mismatches, (
        f"{strategy.STRATEGY_ID}: {len(mismatches)} Paritäts-Abweichungen "
        f"fast_sim vs. check_signal, erste: {mismatches[:5]}")


@pytest.mark.parametrize("sid", VECTORIZED_IDS)
def test_parity_default_params(sid):
    _compare(_registry.get(sid), {})


@pytest.mark.parametrize("sid", sorted(PARAM_OVERRIDES["strategy_params"]))
def test_parity_param_overrides(sid):
    assert sid in VECTORIZED_IDS
    _compare(_registry.get(sid), PARAM_OVERRIDES)


def test_vectorized_strategies_exist():
    # Registry-Drift-Wächter: die bekannten Fast-Path-Strategien bleiben abgedeckt
    expected = {"scalping_4_rules", "ema_pullback_scalping", "rsi_only",
                "bollinger_reversion", "macd_rsi_momentum", "bollinger_squeeze",
                "vwap_reversion", "horst_vwap_obv", "stoch_reversal",
                "nnfx_trend", "nnfx_reversion", "nnfx_breakout", "trend_surfer"}
    assert expected.issubset(set(VECTORIZED_IDS)), set(VECTORIZED_IDS)


def test_parity_not_vacuous():
    # Läuft durch xdist/loadscope im selben Worker wie die Parametrisierungen
    # (ein Modul = ein Worker); ohne echte Signalpunkte wäre der Test wertlos.
    if _signal_counter["n"] == 0:
        _compare(_registry.get("ema_pullback_scalping"), {})
    assert _signal_counter["n"] > 0
