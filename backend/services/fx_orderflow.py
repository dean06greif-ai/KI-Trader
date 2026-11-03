"""FX-/Rohstoff-Orderflow-Näherung aus ECHTEM CME-Futures-Volumen (Yahoo).

Spot-Forex hat kein zentrales Orderbuch – die beste frei verfügbare Näherung
ist das reale Handelsvolumen der CME-Währungs-Futures (6E/6B/6J/…), das Yahoo
Finance minütlich liefert. Statt des bisherigen synthetischen Aktivitäts-
Proxys (Spot-FX hat Volumen 0) werden hier aus echten Futures-Volumina
berechnet:
  * Volumen-Delta (Up- vs. Down-Kerzen-Volumen) über 15/60 Minuten
  * CVD-Trend (letzte 30 min vs. davor)
  * Volumen-Spikes (aktuelle Aktivität vs. Stunden-Basis)

Invertierte Kontrakte (6J = JPY/USD, 6C = CAD/USD, 6S = CHF/USD) werden auf
die Chart-Richtung des Symbols (USDJPY usw.) umgerechnet. Gold/Silber dienen
als Fallback, falls der echte Bitunix-Tick-Stream (XAUUSDT/XAGUSDT) zu wenige
Trades liefert. Läuft als Hintergrund-Loop mit Cache – die Snapshot-Abfrage
ist synchron und kostenlos.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# symbol -> (Yahoo-Futures-Kontrakt, invertiert zur Chart-Richtung?)
FUTURES_MAP = {
    "EURUSD": ("6E=F", False),
    "GBPUSD": ("6B=F", False),
    "USDJPY": ("6J=F", True),
    "AUDUSD": ("6A=F", False),
    "USDCAD": ("6C=F", True),
    "USDCHF": ("6S=F", True),
    "NZDUSD": ("6N=F", False),
    "GOLD": ("GC=F", False),
    "SILVER": ("SI=F", False),
}

REFRESH_SEC = 180
CACHE_MAX_AGE_S = 15 * 60
MIN_CANDLES = 20


def futures_flow_stats(candles: List[Dict], inverted: bool = False) -> Optional[Dict]:
    """Reine Berechnung (testbar): Volumen-Delta/CVD/Spike aus 1m-Futures-Kerzen.

    candles: oldest-first, mit echtem 'volume'. inverted=True dreht die
    Delta-Richtung (Kontrakt notiert gegenläufig zum Chart-Symbol)."""
    rows = [c for c in (candles or []) if float(c.get("volume") or 0) > 0]
    if len(rows) < MIN_CANDLES:
        return None

    def _delta(cs) -> float:
        buy = sum(float(c["volume"]) for c in cs if c["close"] >= c["open"])
        sell = sum(float(c["volume"]) for c in cs if c["close"] < c["open"])
        tot = buy + sell
        d = (buy - sell) / tot if tot else 0.0
        return -d if inverted else d

    d15 = _delta(rows[-15:])
    d60 = _delta(rows[-60:])
    cvd_now = _delta(rows[-30:])
    cvd_prev = _delta(rows[-60:-30]) if len(rows) >= 45 else cvd_now
    vols = [float(c["volume"]) for c in rows]
    v_recent = sum(vols[-5:]) / 5
    v_base = (sum(vols[-60:]) / min(60, len(vols))) or 1.0
    return {
        "candles": len(rows),
        "delta_15m": round(d15, 3),
        "delta_60m": round(d60, 3),
        "cvd_now": round(cvd_now, 3),
        "cvd_prev": round(cvd_prev, 3),
        "vol_ratio": round(v_recent / v_base, 2),
    }


def flow_text(symbol: str, contract: str, stats: Dict) -> str:
    side = ("Käufer" if stats["delta_15m"] > 0.05
            else "Verkäufer" if stats["delta_15m"] < -0.05 else "ausgeglichen")
    cvd = ("steigend" if stats["cvd_now"] > stats["cvd_prev"] + 0.05
           else "fallend" if stats["cvd_now"] < stats["cvd_prev"] - 0.05 else "neutral")
    parts = [f"Orderflow (CME-Futures {contract}, ECHTES Futures-Volumen): "
             f"Delta 15m {stats['delta_15m']:+.2f} ({side}) / 60m {stats['delta_60m']:+.2f}, "
             f"CVD-Trend {cvd}, Volumen x{stats['vol_ratio']:.2f}"]
    if stats["vol_ratio"] >= 2.5:
        parts.append("⚠ Volumen-Spike im Futures-Markt (institutionelle Aktivität)")
    return " | ".join(parts)


class FxOrderflowProxy:
    def __init__(self):
        self._cache: Dict[str, Dict] = {}   # symbol -> {ts, stats, text}
        self.running = False
        self.status = {"last_run": None, "last_error": None, "symbols_ok": []}

    def snapshot_text(self, symbol: str) -> Optional[str]:
        entry = self._cache.get(str(symbol).upper())
        if not entry or (time.time() - entry["ts"]) > CACHE_MAX_AGE_S:
            return None
        return entry["text"]

    def stats(self, symbol: str) -> Optional[Dict]:
        entry = self._cache.get(str(symbol).upper())
        if not entry or (time.time() - entry["ts"]) > CACHE_MAX_AGE_S:
            return None
        return entry["stats"]

    async def _fetch_candles(self, session, ysym: str) -> List[Dict]:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}"
        async with session.get(url, params={"interval": "1m", "range": "1d"},
                               timeout=20) as resp:
            data = await resp.json(content_type=None)
        try:
            res = data["chart"]["result"][0]
            ts = res["timestamp"]
            q = res["indicators"]["quote"][0]
        except (KeyError, IndexError, TypeError):
            return []
        out = []
        for i in range(len(ts)):
            c, o = q["close"][i], q["open"][i]
            if c is None or o is None:
                continue
            out.append({"timestamp": int(ts[i]) * 1000, "open": float(o),
                        "close": float(c), "volume": float(q["volume"][i] or 0)})
        out.sort(key=lambda x: x["timestamp"])
        return out

    async def refresh(self):
        import aiohttp
        from core import market_hours
        ok = []
        headers = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                                  "Chrome/120.0.0.0 Safari/537.36"),
                   "Accept": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as session:
            for symbol, (contract, inverted) in FUTURES_MAP.items():
                closed, _ = market_hours.is_weekend_closed(symbol)
                if closed:
                    continue
                try:
                    candles = await self._fetch_candles(session, contract)
                    stats = futures_flow_stats(candles, inverted)
                    if stats:
                        self._cache[symbol] = {
                            "ts": time.time(), "stats": stats,
                            "text": flow_text(symbol, contract, stats)}
                        ok.append(symbol)
                except Exception as e:
                    logger.debug(f"FX-Orderflow {symbol} ({contract}): {str(e)[:120]}")
                await asyncio.sleep(0.4)
        self.status["symbols_ok"] = ok
        self.status["last_run"] = time.time()

    async def run_loop(self):
        self.running = True
        logger.info(f"FX-Orderflow-Loop gestartet (CME-Futures-Volumen, "
                    f"{len(FUTURES_MAP)} Symbole)")
        while self.running:
            try:
                await self.refresh()
                self.status["last_error"] = None
            except Exception as e:
                self.status["last_error"] = str(e)[:200]
                logger.warning(f"FX-Orderflow-Loop: {e}")
            await asyncio.sleep(REFRESH_SEC)

    async def stop(self):
        self.running = False


fx_orderflow = FxOrderflowProxy()
