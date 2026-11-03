import aiohttp
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

# Bitunix public endpoints (no API key required for market data)
REST_KLINE_URL = "https://fapi.bitunix.com/api/v1/futures/market/kline"


async def fetch_klines_range(symbol: str, interval: str = "1m",
                             start_ms: int = None, end_ms: int = None,
                             limit: int = 200) -> List[Dict]:
    """On-demand Kline-Fetch für den Trade-Chart (öffentlich, kein Key).
    Kein Cache, keine DB – zieht nur Leistung, wenn der Chart aktiv geladen
    wird. `time` wird in Sekunden geliefert (lightweight-charts-Format)."""
    params = {"symbol": symbol, "interval": interval, "limit": limit}
    if start_ms:
        params["startTime"] = int(start_ms)
    if end_ms:
        params["endTime"] = int(end_ms)
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(REST_KLINE_URL, params=params,
                                   timeout=aiohttp.ClientTimeout(total=20)) as resp:
                payload = await resp.json()
    except Exception as e:
        logger.error(f"fetch_klines_range {symbol} failed: {e}")
        return []
    if not payload or payload.get("code") != 0 or not payload.get("data"):
        logger.warning(f"fetch_klines_range {symbol}: keine Daten "
                       f"({str(payload)[:150]})")
        return []
    candles = []
    for k in payload["data"]:
        try:
            candles.append({"time": int(k["time"]) // 1000,
                            "open": float(k["open"]), "high": float(k["high"]),
                            "low": float(k["low"]), "close": float(k["close"])})
        except (KeyError, ValueError, TypeError):
            continue
    candles.sort(key=lambda c: c["time"])
    return candles


