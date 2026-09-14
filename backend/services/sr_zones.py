"""4h/1d-Haupt-S/R-Zonen für das Live-Chart-Overlay.

Serverseitige Pivot-/Cluster-Analyse (gleiche Kerzenquelle wie
services/macro_context.key_levels): Swing-Hochs/-Tiefs werden je Timeframe
zu Zonen geclustert; Berührungen + Aktualität ergeben die Stärke.
Ressourcenschonend: ein Abruf je Symbol mit 5-Minuten-Cache – 4h/1d-Zonen
ändern sich langsam. Das Overlay im Chart ist standardmäßig AUS
(frontend/src/hooks/useSRZonesOverlay.js).
"""
import logging
import time
from typing import Dict, List, Tuple

import aiohttp

from services import macro_context as mc

logger = logging.getLogger(__name__)

CACHE_TTL = 300
MAX_ZONES_PER_SIDE = 3
# tol_pct: Cluster-Toleranz (Level innerhalb x% werden zu EINER Zone vereint)
# max_dist_pct: Alt-Level weiter als x% vom Preis sind fürs Overlay irrelevant
TF_CFG = {"4h": {"limit": 180, "tol_pct": 0.6, "max_dist_pct": 12.0},
          "1d": {"limit": 180, "tol_pct": 1.2, "max_dist_pct": 25.0}}
_cache: Dict[str, Tuple[float, Dict]] = {}


def _pivots(candles: List[Dict], left: int = 3, right: int = 3):
    """Swing-Hochs/-Tiefs als (index, preis)-Paare (wie macro_context._pivot_levels)."""
    highs, lows = [], []
    for i in range(left, len(candles) - right):
        win = candles[i - left:i + right + 1]
        c = candles[i]
        if c["high"] == max(w["high"] for w in win):
            highs.append((i, float(c["high"])))
        if c["low"] == min(w["low"] for w in win):
            lows.append((i, float(c["low"])))
    return highs, lows


def compute_zones(candles: List[Dict], tf: str, tol_pct: float,
                  max_per_side: int = MAX_ZONES_PER_SIDE,
                  max_dist_pct: float = 100.0) -> List[Dict]:
    """Pivot-Cluster -> Zonen {kind, tf, low, high, mid, touches, strength}."""
    if len(candles) < 20:
        return []
    price = float(candles[-1]["close"])
    if price <= 0:
        return []
    n = len(candles)
    highs, lows = _pivots(candles)
    tol = price * tol_pct / 100
    min_half = price * 0.0008  # Mindest-Bandbreite, damit 1-Touch-Zonen sichtbar sind

    def _cluster(pivots, kind):
        groups = []
        for idx, val in sorted(pivots, key=lambda p: p[1]):
            if groups and val - groups[-1]["vals"][-1] <= tol:
                groups[-1]["vals"].append(val)
                groups[-1]["idx"].append(idx)
            else:
                groups.append({"vals": [val], "idx": [idx]})
        zones = []
        for g in groups:
            vals, idxs = g["vals"], g["idx"]
            lo, hi = min(vals), max(vals)
            if hi - lo < 2 * min_half:
                mid = (hi + lo) / 2
                lo, hi = mid - min_half, mid + min_half
            recency = max(idxs) / max(n - 1, 1)
            touches = len(vals)
            strength = int(min(100, touches * 18 + recency * 45))
            zones.append({"kind": kind, "tf": tf,
                          "low": round(lo, 6), "high": round(hi, 6),
                          "mid": round(sum(vals) / len(vals), 6),
                          "touches": touches, "strength": strength})
        return zones

    max_dist = price * max_dist_pct / 100
    sup = [z for z in _cluster(lows, "support")
           if z["mid"] < price and price - z["mid"] <= max_dist]
    res = [z for z in _cluster(highs, "resistance")
           if z["mid"] > price and z["mid"] - price <= max_dist]
    sup.sort(key=lambda z: (-z["strength"], price - z["mid"]))
    res.sort(key=lambda z: (-z["strength"], z["mid"] - price))
    return sup[:max_per_side] + res[:max_per_side]


async def get_sr_zones(symbol: str) -> Dict:
    """Alle 4h/1d-Zonen eines Symbols (gecacht, OKX/Binance-Kerzen)."""
    symbol = symbol.upper()
    hit = _cache.get(symbol)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return hit[1]
    zones: List[Dict] = []
    price = None
    async with aiohttp.ClientSession() as session:
        for tf, cfg in TF_CFG.items():
            try:
                candles = await mc.fetch_klines(session, symbol, tf, cfg["limit"])
            except Exception as e:
                logger.debug(f"sr_zones klines {symbol} {tf}: {e}")
                continue
            if not candles:
                continue
            price = float(candles[-1]["close"])
            zones.extend(compute_zones(candles, tf, cfg["tol_pct"],
                                       max_dist_pct=cfg["max_dist_pct"]))
    zones.sort(key=lambda z: -z["strength"])
    out = {"symbol": symbol, "price": price, "zones": zones,
           "timeframes": list(TF_CFG)}
    if zones:
        _cache[symbol] = (time.time(), out)
        if len(_cache) > 40:
            _cache.pop(min(_cache, key=lambda k: _cache[k][0]), None)
    return out
