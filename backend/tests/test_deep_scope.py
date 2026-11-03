"""Regressionstests: adaptives Volatilitäts-Radar der Tiefenanalyse
(services/deep_scope.py) – Nicht-Krypto-Assets mit erhöhter relativer
Volatilität werden markiert, Krypto bleibt außen vor, '' ohne Daten.
"""
import pytest

from services import deep_scope as ds


def _candles_1m(n: int, base: float = 100.0, rng: float = 0.05,
                spike_last: int = 0, spike_mult: float = 4.0):
    """Synthetische 1m-Kerzen mit konstanter Range; optional Spike am Ende."""
    out = []
    ts = 1_700_000_000_000
    for i in range(n):
        r = rng * (spike_mult if spike_last and i >= n - spike_last else 1.0)
        out.append({"timestamp": ts + i * 60_000, "open": base, "close": base,
                    "high": base + r, "low": base - r, "volume": 10.0})
    return out


class TestRelVol:
    def test_none_with_too_little_data(self):
        assert ds.rel_vol([]) is None
        assert ds.rel_vol(_candles_1m(100)) is None  # < 40 15m-Bars

    def test_flat_market_ratio_near_one(self):
        rv = ds.rel_vol(_candles_1m(1500))
        assert rv is not None
        assert 0.8 <= rv["ratio"] <= 1.2

    def test_spike_detected_as_elevated(self):
        rv = ds.rel_vol(_candles_1m(1500, spike_last=240, spike_mult=5.0))
        assert rv is not None
        assert rv["ratio"] >= ds.ELEVATED_RATIO


class TestRadar:
    def test_crypto_excluded_and_noncrypto_flagged(self):
        buffers = {
            "BTCUSDT": _candles_1m(1500, base=70000, rng=40, spike_last=240),
            "GOLD": _candles_1m(1500, base=2600, rng=0.5, spike_last=240, spike_mult=5.0),
            "EURUSD": _candles_1m(1500, base=1.08, rng=0.0004),
        }
        rows = ds.radar(buffers, ["BTCUSDT", "GOLD", "EURUSD"])
        syms = [r["symbol"] for r in rows]
        assert "BTCUSDT" not in syms
        assert "GOLD" in syms and "EURUSD" in syms
        gold = next(r for r in rows if r["symbol"] == "GOLD")
        assert gold["elevated"] is True
        eur = next(r for r in rows if r["symbol"] == "EURUSD")
        assert eur["elevated"] is False
        # sortiert nach Ratio absteigend
        assert rows[0]["symbol"] == "GOLD"

    def test_symbols_without_buffer_skipped(self):
        rows = ds.radar({}, ["GOLD", "SPYUSDT"])
        assert rows == []


class TestBlockText:
    def test_empty_without_rows(self):
        assert ds.block_text([]) == ""

    def test_block_contains_flag_and_guidance(self):
        rows = [{"symbol": "GOLD", "cls": "resources", "ratio": 1.8,
                 "atr_pct": 0.045, "elevated": True},
                {"symbol": "EURUSD", "cls": "forex", "ratio": 0.9,
                 "atr_pct": 0.012, "elevated": False}]
        txt = ds.block_text(rows)
        assert "VOLATILITÄTS-RADAR" in txt
        assert "GOLD" in txt and "ERHÖHT" in txt
        assert "EURUSD" in txt and "(normal)" in txt
        assert "EIGENEN Baseline" in txt
