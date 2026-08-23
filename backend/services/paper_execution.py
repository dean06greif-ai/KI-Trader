"""Ehrliches Paper-Trading: simulierte Spread- + Slippage-Abrechnung.

Paper-Trades füllten bisher exakt zum Signal-/Level-Preis – Live-Trades zahlen
aber immer Spread (Market-Order kreuzt das Buch) und Slippage. Dieses Modul
rechnet Paper-Fills wie Live-Fills ab, damit Paper und Live vergleichbar sind:

  1. Bevorzugt: echter Spread aus dem Live-Orderbuch (Top-of-Book,
     Binance -> OKX -> Bybit, keyless, 20s-Cache – gleiche Quellen wie
     services/liquidity_data.py).
  2. Fallback: konservative Schätzwerte je Liquiditäts-Tier
     (Majors eng, übrige Kryptos mittel, Nicht-Krypto/Yahoo weiter).

Übersteuerbar über die Scanner-Settings: paper_realistic_fills (an/aus),
paper_fallback_spread_pct, paper_slippage_pct. Keine neue Dependency, kein
eigener Task – reine Berechnungsschicht, die von services/bitunix_trade.py
bei Entry (Market-Fill) und Exit (SL/TP/Manual/Teil-Close) aufgerufen wird.
"""
import logging
import time
from typing import Dict, Optional, Tuple

import aiohttp

logger = logging.getLogger(__name__)

MAJORS = {"BTCUSDT", "ETHUSDT"}
# Fallback-Schätzwerte in Prozent (halber Spread wird angerechnet, Mid-Preis-Annahme)
FALLBACK_SPREAD_PCT = {"major": 0.01, "crypto": 0.04, "other": 0.06}
SLIPPAGE_PCT = {"major": 0.01, "crypto": 0.03, "other": 0.05}
SPREAD_TTL_S = 20          # Top-of-Book-Cache
SPREAD_SANITY_MAX = 0.5    # % – darüber gilt das Buch als kaputt -> Fallback

_spread_cache: Dict[str, tuple] = {}   # symbol -> (ts, spread_pct)

_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _settings() -> Dict:
    try:
        from core.state import scanner
        return scanner.settings or {}
    except Exception:
        return {}


def enabled() -> bool:
    return bool(_settings().get("paper_realistic_fills", True))


def _tier(symbol: str) -> str:
    if symbol in MAJORS:
        return "major"
    try:
        from core.instruments import get as get_instrument
        inst = get_instrument(symbol)
        if inst is not None and inst.live_source == "yahoo":
            return "other"
    except Exception:
        pass
    return "crypto" if str(symbol).endswith("USDT") else "other"


def _sig_round(x: float) -> float:
    """Auf 8 signifikante Stellen runden (BTC 6-stellig bis Micro-Alts)."""
    return float(f"{x:.8g}")


async def _fetch_json(session, url: str, params: dict):
    async with session.get(url, params=params, headers=_HEADERS,
                           timeout=aiohttp.ClientTimeout(total=6)) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return await resp.json(content_type=None)


async def _book_spread_pct(symbol: str) -> Optional[float]:
    """Echter Top-of-Book-Spread in % (Binance -> OKX -> Bybit), None bei Fehler."""
    cached = _spread_cache.get(symbol)
    if cached and (time.time() - cached[0]) < SPREAD_TTL_S:
        return cached[1]
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    async with aiohttp.ClientSession() as s:
        for fetch in (
            lambda: _fetch_json(s, "https://fapi.binance.com/fapi/v1/ticker/bookTicker",
                                {"symbol": symbol}),
            lambda: _fetch_json(s, "https://www.okx.com/api/v5/market/ticker",
                                {"instId": f"{base}-USDT-SWAP"}),
            lambda: _fetch_json(s, "https://api.bybit.com/v5/market/tickers",
                                {"category": "linear", "symbol": symbol}),
        ):
            try:
                d = await fetch()
                if "bidPrice" in d:                       # Binance
                    bid, ask = float(d["bidPrice"]), float(d["askPrice"])
                elif d.get("data"):                       # OKX
                    row = d["data"][0]
                    bid, ask = float(row["bidPx"]), float(row["askPx"])
                elif d.get("result", {}).get("list"):     # Bybit
                    row = d["result"]["list"][0]
                    bid, ask = float(row["bid1Price"]), float(row["ask1Price"])
                else:
                    continue
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                spread = (ask - bid) / ((ask + bid) / 2) * 100
                if 0 <= spread <= SPREAD_SANITY_MAX:
                    _spread_cache[symbol] = (time.time(), round(spread, 5))
                    return _spread_cache[symbol][1]
            except Exception as e:
                logger.debug(f"paper spread {symbol}: {e}")
    return None


async def get_costs(symbol: str) -> Tuple[float, float, str]:
    """(spread_pct, slippage_pct, quelle) – Live-Orderbuch bevorzugt, sonst Schätzung."""
    tier = _tier(symbol)
    s = _settings()
    try:
        slip = float(s.get("paper_slippage_pct") or 0) or SLIPPAGE_PCT[tier]
    except (TypeError, ValueError):
        slip = SLIPPAGE_PCT[tier]
    spread = None
    if tier != "other":  # Yahoo-Instrumente (Gold/Öl/Forex) haben kein Perp-Buch
        spread = await _book_spread_pct(symbol)
    if spread is not None:
        return spread, slip, "orderbook"
    try:
        fb = float(s.get("paper_fallback_spread_pct") or 0) or FALLBACK_SPREAD_PCT[tier]
    except (TypeError, ValueError):
        fb = FALLBACK_SPREAD_PCT[tier]
    return fb, slip, "fallback"


async def _fill(symbol: str, buy: bool, ref_price: float) -> Tuple[float, Optional[Dict]]:
    if not enabled() or not ref_price or ref_price <= 0:
        return ref_price, None
    spread, slip, source = await get_costs(symbol)
    adj = (spread / 2 + slip) / 100
    fill = _sig_round(ref_price * (1 + adj) if buy else ref_price * (1 - adj))
    return fill, {
        "ref_price": ref_price, "fill_price": fill,
        "spread_pct": round(spread, 4), "slippage_pct": round(slip, 4),
        "cost_pct": round(adj * 100, 4), "source": source,
    }


async def entry_fill(symbol: str, side: str, ref_price: float) -> Tuple[float, Optional[Dict]]:
    """Entry als Market-Order: LONG kauft am Ask (+Slippage), SHORT verkauft am Bid."""
    return await _fill(symbol, buy=(str(side).upper() == "LONG"), ref_price=ref_price)


async def exit_fill(symbol: str, side: str, ref_price: float) -> Tuple[float, Optional[Dict]]:
    """Exit (SL/TP-Trigger/Manual = Market): LONG verkauft am Bid, SHORT kauft am Ask."""
    return await _fill(symbol, buy=(str(side).upper() != "LONG"), ref_price=ref_price)
