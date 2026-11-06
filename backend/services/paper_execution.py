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
# Größenabhängige Slippage (Sqrt-Impact-Modell): bis zur Referenz-Notional
# bleibt die Basis-Slippage, darüber wächst sie mit sqrt(Notional/Referenz).
# Ist echte Top-of-Book-Tiefe bekannt und größer, ersetzt sie die Referenz
# (tiefe Bücher absorbieren große Orders besser). Hart gedeckelt.
TIER_REF_NOTIONAL = {"major": 100_000.0, "crypto": 25_000.0, "other": 10_000.0}
MAX_SLIPPAGE_PCT_DEFAULT = 0.30
SPREAD_TTL_S = 20          # Top-of-Book-Cache
SPREAD_SANITY_MAX = 0.5    # % – darüber gilt das Buch als kaputt -> Fallback

_book_cache: Dict[str, tuple] = {}   # symbol -> (ts, {spread_pct, bid_usd, ask_usd})

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
        if inst is not None and inst.yahoo_ref:
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


async def _book_top(symbol: str) -> Optional[Dict]:
    """Top-of-Book (Spread % + Bid/Ask-Tiefe in USD), Binance -> OKX -> Bybit.
    None bei Fehler; Tiefe kann fehlen (None), wenn die Quelle sie nicht liefert."""
    cached = _book_cache.get(symbol)
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
                bid_qty = ask_qty = None
                if "bidPrice" in d:                       # Binance (Qty in Coin)
                    bid, ask = float(d["bidPrice"]), float(d["askPrice"])
                    bid_qty = float(d.get("bidQty") or 0)
                    ask_qty = float(d.get("askQty") or 0)
                elif d.get("data"):                       # OKX (Sz in Kontrakten)
                    row = d["data"][0]
                    bid, ask = float(row["bidPx"]), float(row["askPx"])
                    try:
                        from services.liquidity_data import _okx_ctval
                        ct = _okx_ctval(symbol)
                        bid_qty = float(row.get("bidSz") or 0) * ct
                        ask_qty = float(row.get("askSz") or 0) * ct
                    except Exception:
                        bid_qty = ask_qty = None
                elif d.get("result", {}).get("list"):     # Bybit (Size in Coin)
                    row = d["result"]["list"][0]
                    bid, ask = float(row["bid1Price"]), float(row["ask1Price"])
                    bid_qty = float(row.get("bid1Size") or 0)
                    ask_qty = float(row.get("ask1Size") or 0)
                else:
                    continue
                if bid <= 0 or ask <= 0 or ask < bid:
                    continue
                spread = (ask - bid) / ((ask + bid) / 2) * 100
                if not (0 <= spread <= SPREAD_SANITY_MAX):
                    continue
                book = {"spread_pct": round(spread, 5),
                        "bid_usd": round(bid_qty * bid, 2) if bid_qty else None,
                        "ask_usd": round(ask_qty * ask, 2) if ask_qty else None}
                _book_cache[symbol] = (time.time(), book)
                return book
            except Exception as e:
                logger.debug(f"paper book {symbol}: {e}")
    return None


async def get_costs(symbol: str, buy: bool = True):
    """(spread_pct, basis_slippage_pct, quelle, tiefe_usd) – Live-Orderbuch
    bevorzugt, sonst Schätzung. Tiefe = verfügbare USD am Touch der Seite,
    die die Order konsumiert (Kauf -> Asks, Verkauf -> Bids)."""
    tier = _tier(symbol)
    s = _settings()
    try:
        slip = float(s.get("paper_slippage_pct") or 0) or SLIPPAGE_PCT[tier]
    except (TypeError, ValueError):
        slip = SLIPPAGE_PCT[tier]
    book = None
    if tier != "other":  # Yahoo-Instrumente (Gold/Öl/Forex) haben kein Perp-Buch
        book = await _book_top(symbol)
    if book is not None:
        depth = book.get("ask_usd") if buy else book.get("bid_usd")
        return book["spread_pct"], slip, "orderbook", depth
    try:
        fb = float(s.get("paper_fallback_spread_pct") or 0) or FALLBACK_SPREAD_PCT[tier]
    except (TypeError, ValueError):
        fb = FALLBACK_SPREAD_PCT[tier]
    return fb, slip, "fallback", None


def size_scaled_slippage(base_slip: float, notional_usdt: float, tier: str,
                         depth_usd: Optional[float] = None):
    """(slippage_pct, größen_faktor): Sqrt-Impact-Modell.
    <= Referenz-Notional: Basis-Slippage. Darüber: Basis × sqrt(Notional/Referenz),
    hart gedeckelt (paper_max_slippage_pct, Default 0.30%). Echte Top-of-Book-
    Tiefe ersetzt die Referenz, wenn sie größer ist (tiefes Buch = weniger Impact)."""
    s = _settings()
    if notional_usdt <= 0 or not bool(s.get("paper_size_slippage_enabled", True)):
        return base_slip, 1.0
    try:
        ref = float(s.get("paper_size_ref_notional") or 0)
    except (TypeError, ValueError):
        ref = 0.0
    if ref <= 0:
        ref = TIER_REF_NOTIONAL.get(tier, TIER_REF_NOTIONAL["other"])
        if depth_usd and depth_usd > ref:
            ref = float(depth_usd)
    mult = max(1.0, (notional_usdt / ref) ** 0.5)
    try:
        cap = float(s.get("paper_max_slippage_pct") or 0) or MAX_SLIPPAGE_PCT_DEFAULT
    except (TypeError, ValueError):
        cap = MAX_SLIPPAGE_PCT_DEFAULT
    return min(base_slip * mult, cap), round(mult, 3)


async def _fill(symbol: str, buy: bool, ref_price: float,
                notional_usdt: float = 0.0) -> Tuple[float, Optional[Dict]]:
    if not enabled() or not ref_price or ref_price <= 0:
        return ref_price, None
    spread, base_slip, source, depth = await get_costs(symbol, buy=buy)
    slip, size_mult = size_scaled_slippage(base_slip, notional_usdt,
                                           _tier(symbol), depth)
    adj = (spread / 2 + slip) / 100
    fill = _sig_round(ref_price * (1 + adj) if buy else ref_price * (1 - adj))
    return fill, {
        "ref_price": ref_price, "fill_price": fill,
        "spread_pct": round(spread, 4), "slippage_pct": round(slip, 4),
        "base_slippage_pct": round(base_slip, 4), "size_mult": size_mult,
        "notional_usdt": round(notional_usdt, 2) if notional_usdt else None,
        "book_depth_usd": depth,
        "cost_pct": round(adj * 100, 4), "source": source,
    }


async def entry_fill(symbol: str, side: str, ref_price: float,
                     notional_usdt: float = 0.0) -> Tuple[float, Optional[Dict]]:
    """Entry als Market-Order: LONG kauft am Ask (+Slippage), SHORT verkauft am Bid."""
    return await _fill(symbol, buy=(str(side).upper() == "LONG"),
                       ref_price=ref_price, notional_usdt=notional_usdt)


async def exit_fill(symbol: str, side: str, ref_price: float,
                    notional_usdt: float = 0.0) -> Tuple[float, Optional[Dict]]:
    """Exit (SL/TP-Trigger/Manual = Market): LONG verkauft am Bid, SHORT kauft am Ask."""
    return await _fill(symbol, buy=(str(side).upper() != "LONG"),
                       ref_price=ref_price, notional_usdt=notional_usdt)
