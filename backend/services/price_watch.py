"""Schneller Preis-Wächter: holt im Sekundentakt (FAST_PRICE_INTERVAL, Default
3s) mit EINEM Bitunix-Ticker-Call die Mark-Preise aller Symbole mit offenen
Trades und stößt die Trade-Verwaltung (TP1/Break-Even/Trailing/SL) an.

Hintergrund (06/2026): Der Scanner-Kerzen-Pass läuft nur alle POLL_INTERVAL
Sekunden über ALLE Instrumente – Live-Wicks (z.B. TP1-Touch) wurden dadurch
teils spät erkannt. Dieser Wächter entkoppelt die Trade-Überwachung vom
Kerzen-Pass. Instrumente mit Handelspausen (Gold/Indizes/Forex) bleiben beim
Scanner-Takt (synthetische Börsenpreise in Pausen könnten falsche SL auslösen).
"""
import asyncio
import logging
import os

from core import instruments

logger = logging.getLogger(__name__)

FAST_PRICE_INTERVAL = float(os.environ.get("FAST_PRICE_INTERVAL", "3") or 3)


def _fast_symbols(symbols) -> list:
    """Nur Symbole ohne Handelspausen (Krypto 24/7) – rein & testbar."""
    out = []
    for s in symbols:
        inst = instruments.get(s)
        if inst is not None and inst.has_trading_pauses:
            continue
        out.append(s)
    return out


async def tick(autotrader) -> int:
    """Ein Durchlauf: Mark-Preise der offenen Trade-Symbole (1 API-Call) ->
    autotrader.monitor(). Rückgabe: Anzahl aktualisierter Preise."""
    if autotrader.db is None:
        return 0
    symbols = await autotrader.db.auto_trades.distinct(
        "symbol", {"status": "open", "external_adopted": {"$ne": True}})
    symbols = _fast_symbols(symbols)
    if not symbols:
        return 0
    ticks = await autotrader.client.get_all_mark_prices()
    if not ticks:
        return 0
    prices = {}
    for s in symbols:
        p = ticks.get(str(autotrader.client.to_bitunix_symbol(s)).upper())
        if p:
            prices[s] = p
    if prices:
        await autotrader.monitor(prices)
    return len(prices)


async def run_loop():
    from core.state import autotrader
    if FAST_PRICE_INTERVAL <= 0:
        logger.info("Preis-Wächter deaktiviert (FAST_PRICE_INTERVAL <= 0)")
        return
    logger.info(f"Schneller Preis-Wächter gestartet (alle {FAST_PRICE_INTERVAL:g}s)")
    while True:
        await asyncio.sleep(FAST_PRICE_INTERVAL)
        try:
            await tick(autotrader)
        except Exception as e:
            logger.warning(f"Preis-Wächter-Tick fehlgeschlagen: {e}")
