"""Regression: Bitunix code 10002 "Parameter error" (Telegram POLUSDT SHORT).

Bitunix liefert `basePrecision=0` für Kontrakte, die nur in ganzen Einheiten
gehandelt werden (POL, AVAX, ...). `_precision_to_step(0)` lieferte 0.0 statt
1.0 -> `_fmt_qty` ließ die Menge ungerundet ("928.37") und die Börse lehnte
jede Order ab. Preise (quotePrecision=5) waren korrekt gerundet.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

from services.bitunix_trade import BitunixTradeClient, _precision_to_step  # noqa: E402


def _client_with(symbol, base_prec, quote_prec, min_qty):
    c = BitunixTradeClient()
    c._pairs_meta = {symbol: {"qty_step": _precision_to_step(base_prec),
                              "price_tick": _precision_to_step(quote_prec),
                              "min_qty": float(min_qty)}}
    c._valid_bitunix_symbols = {symbol}
    return c


def test_precision_zero_means_whole_units():
    assert _precision_to_step(0) == 1.0
    assert _precision_to_step("0") == 1.0
    assert _precision_to_step(0.0) == 1.0
    assert _precision_to_step(None) == 0.0
    assert _precision_to_step("") == 0.0
    assert _precision_to_step(-1) == 0.0
    assert _precision_to_step(5) == 0.00001
    assert _precision_to_step(0.001) == 0.001


def test_pol_qty_is_rounded_to_whole_units():
    """Exaktes Szenario aus dem Screenshot: POLUSDT basePrecision=0,
    quotePrecision=5, minTradeVolume=10."""
    c = _client_with("POLUSDT", 0, 5, 10)
    assert c._fmt_qty("POLUSDT", 928.3734) == "928"
    assert c._fmt_qty("POLUSDT", 928.0) == "928"
    assert c._fmt_qty("POLUSDT", 10.9) == "10"
    assert c._fmt_qty("POLUSDT", 3.2) == "10"  # unter Minimum -> Minimum
    assert c._fmt_qty("POLUSDT", 928.3734, round_up=True) == "929"


def test_pol_prices_keep_five_decimals():
    c = _client_with("POLUSDT", 0, 5, 10)
    # SHORT: SL wird AUFgerundet, TP ABgerundet (weg vom Mark)
    assert c._fmt_price("POLUSDT", 0.109874, "up") == "0.10988"
    assert c._fmt_price("POLUSDT", 0.105566, "down") == "0.10556"
    assert c._fmt_price("POLUSDT", 0.10772) == "0.10772"


def test_fractional_contracts_unchanged():
    c = _client_with("BTCUSDT", 4, 1, 0.0001)
    assert c._fmt_qty("BTCUSDT", 0.012345) == "0.0123"
    assert c._fmt_price("BTCUSDT", 65432.16, "up") == "65432.2"
