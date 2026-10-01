"""Regressionstests: Forex-Gebührenmodell, IBKR-Helfer, Instrument-Broker."""
import pytest

from services import fee_model
from services.ibkr_client import base_qty_for_notional
from services.ibkr_trade import pnl_usd
from core import instruments


def test_forex_symbols_have_ibkr_broker():
    eur = instruments.get("EURUSD")
    assert eur.broker == "ibkr"
    assert eur.ibkr == "EUR.USD"
    assert eur.live_tradable is True
    assert eur.tradable is False  # kein Bitunix-Kontrakt


def test_crypto_stays_bitunix():
    btc = instruments.get("BTCUSDT")
    assert btc.broker == "bitunix"
    assert btc.ibkr is None
    assert btc.tradable is True


def test_fee_percent_for_crypto_unchanged():
    assert fee_model.fee_percent_for("BTCUSDT", 0.06, notional_usd=10000) == 0.06


def test_forex_fee_uses_commission_default():
    # Ohne Server-Settings gelten die IBKR-Defaults: 0.002%/Seite, min 2 USD.
    # Großes Notional (200k): Kommission 4 USD > Minimum -> 0.002%
    assert fee_model.fee_percent_for("EURUSD", 0.06, notional_usd=200_000) == pytest.approx(0.002)
    # Kleines Notional (10k): 0.002% wären 0.20 USD -> Minimum 2 USD greift = 0.02%
    assert fee_model.fee_percent_for("EURUSD", 0.06, notional_usd=10_000) == pytest.approx(0.02)


def test_base_qty_for_notional():
    # EURUSD @ 1.10: 22.000 USD Notional -> 20.000 EUR
    assert base_qty_for_notional("EURUSD", 22_000, 1.10) == 20_000
    # USDJPY: Basis ist USD -> qty = Notional in USD
    assert base_qty_for_notional("USDJPY", 25_000, 155.0) == 25_000


def test_pnl_usd_quote_usd():
    # LONG EURUSD 20.000 @ 1.1000 -> 1.1050 = +100 USD
    assert pnl_usd("EURUSD", "LONG", 1.10, 1.105, 20_000) == pytest.approx(100.0)
    # SHORT verliert dieselbe Bewegung
    assert pnl_usd("EURUSD", "SHORT", 1.10, 1.105, 20_000) == pytest.approx(-100.0)


def test_pnl_usd_jpy_quote_converted():
    # LONG USDJPY 25.000 @ 150 -> 151: +25.000 JPY = +165.56 USD @ 151
    assert pnl_usd("USDJPY", "LONG", 150.0, 151.0, 25_000) == pytest.approx(25_000 / 151.0)
