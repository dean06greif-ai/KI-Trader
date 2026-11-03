"""Baustein A: Low-Vol-Market-Block – Market-Entries sperren, wenn die ATR
(% vom Preis) unter der Schwelle liegt (Gebühren > erwartbare Bewegung)."""
from services.ai_engine import DEFAULT_AI_CONFIG
from services.bitunix_trade import atr_market_block


def test_blocks_below_threshold():
    assert atr_market_block(0.069, 0.10) is True   # BTC-Beispiel aus der Analyse
    assert atr_market_block(0.061, 0.10) is True   # BNB-Beispiel


def test_allows_at_or_above_threshold():
    assert atr_market_block(0.10, 0.10) is False   # exakt Schwelle = erlaubt
    assert atr_market_block(0.25, 0.10) is False


def test_threshold_zero_or_invalid_disables_block():
    assert atr_market_block(0.05, 0) is False
    assert atr_market_block(0.05, None) is False
    assert atr_market_block(None, 0.10) is False   # fail-open
    assert atr_market_block("abc", 0.10) is False


def test_defaults_present_in_ai_config():
    assert DEFAULT_AI_CONFIG.get("low_vol_market_block_enabled") is True
    assert DEFAULT_AI_CONFIG.get("low_vol_atr_threshold_pct") == 0.10
