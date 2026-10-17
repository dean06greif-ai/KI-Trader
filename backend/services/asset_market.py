"""Marktdaten für den Asset-Vorschlag: Kurs-Korrelation und Volatilität je Regime.

Aus vorhandenen Kerzen (kein Börsen-Download) werden Schlusskurse auf den
Analyse-Timeframe (mind. 1h) gebündelt und Log-Renditen gebildet. Je Regime
zählen nur Zeitpunkte, in denen das Asset laut gemeinsamer Erkennung in diesem
Regime war:
  * corr  = mittlere Korrelation der Renditen mit den anderen Assets (beide im Regime)
  * vol   = Standardabweichung der Renditen in % je Balken, relativ zum Gruppen-Median
Rechenteil (`regime_market_stats`) ist rein & testbar.
"""
import asyncio
import bisect
import math
import time
from typing import Dict, List, Optional

import numpy as np

MARKET_DAYS = 120          # Fenster: letzte 120 Tage (aktuelle Marktstruktur)
MIN_OVERLAP = 30           # mind. gemeinsame Balken für eine Korrelation
MIN_BARS_VOL = 20
TF_MS = {"1h": 3_600_000, "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000,
         "12h": 43_200_000, "1d": 86_400_000}
TIMEOUT_S = 20


def bucket_closes(ts: np.ndarray, cl: np.ndarray, bucket_ms: int, since_ms: int) -> Dict[int, float]:
    """Letzter Schlusskurs je Zeit-Bucket (rein)."""
    m = ts >= since_ms
    ts, cl = ts[m], cl[m]
    if not len(ts):
        return {}
    b = ts // bucket_ms
    rev_unique, rev_idx = np.unique(b[::-1], return_index=True)
    last = len(b) - 1 - rev_idx
    return {int(k) * bucket_ms: float(cl[i]) for k, i in zip(rev_unique, last)}


def log_returns(closes: Dict[int, float], bucket_ms: int) -> Dict[int, float]:
    out = {}
    for t, c in closes.items():
        p = closes.get(t - bucket_ms)
        if p and c and p > 0 and c > 0:
            out[t] = math.log(c / p)
    return out


def regime_at(segments: List[Dict]):
    """Funktion ts -> Regime-ID aus den Segmenten der gemeinsamen Erkennung (rein)."""
    segs = sorted(((int(s.get("from_ts") or 0), int(s.get("to_ts") or 0), int(s.get("regime", -1)))
                   for s in segments or []), key=lambda x: x[0])
    starts = [s[0] for s in segs]

    def at(ts: int) -> Optional[int]:
        i = bisect.bisect_right(starts, ts) - 1
        if i < 0:
            return None
        a, b, r = segs[i]
        return r if ts <= b else None
    return at


def regime_market_stats(returns: Dict[str, Dict[int, float]], segments: Dict[str, List[Dict]],
                        regime_ids: List[int]) -> Dict[str, Dict[str, Dict]]:
    """{regime: {symbol: {corr, vol_pct, vol_rel, n}}} (rein & testbar)."""
    labels = {s: regime_at(segments.get(s) or []) for s in returns}
    out: Dict[str, Dict[str, Dict]] = {}
    for rid in regime_ids:
        in_r = {s: {t: v for t, v in rets.items() if labels[s](t) == rid} for s, rets in returns.items()}
        vols = {s: float(np.std(list(v.values()))) * 100 for s, v in in_r.items() if len(v) >= MIN_BARS_VOL}
        med = float(np.median(list(vols.values()))) if vols else 0.0
        row: Dict[str, Dict] = {}
        for s, v in in_r.items():
            corrs = []
            for o, w in in_r.items():
                if o == s:
                    continue
                common = sorted(set(v) & set(w))
                if len(common) >= MIN_OVERLAP:
                    x = np.array([v[t] for t in common])
                    y = np.array([w[t] for t in common])
                    if x.std() > 0 and y.std() > 0:
                        corrs.append(float(np.corrcoef(x, y)[0, 1]))
            row[s] = {"corr": round(sum(corrs) / len(corrs), 3) if corrs else None,
                      "max_corr": round(max(corrs), 3) if corrs else None,
                      "vol_pct": round(vols[s], 3) if s in vols else None,
                      "vol_rel": round(vols[s] / med, 2) if s in vols and med > 0 else None,
                      "n": len(v)}
        out[str(rid)] = row
    return out


async def load_returns(symbols: List[str], timeframe: str) -> Dict[str, Dict[int, float]]:
    from services import candle_cache
    bucket = TF_MS.get(timeframe, TF_MS["1h"])
    since = int(time.time() * 1000) - MARKET_DAYS * 86_400_000
    out = {}
    for s in symbols:
        c = await candle_cache.peek(s)
        if c is None:
            continue
        closes = await asyncio.to_thread(bucket_closes, c.ts, c.cl, bucket, since)
        rets = log_returns(closes, bucket)
        if rets:
            out[s] = rets
    return out


async def market_stats(analysis: Dict, regime_ids: List[int]) -> Dict:
    """Marktdaten je Regime; leer, wenn keine Kerzen im Cache oder Zeitlimit."""
    combined = analysis.get("combined") or {}
    segs = {s: (v or {}).get("segments") or [] for s, v in (combined.get("per_symbol") or {}).items()}
    symbols = list(analysis.get("symbols") or [])

    async def go():
        rets = await load_returns(symbols, analysis.get("timeframe") or "1h")
        stats = await asyncio.to_thread(regime_market_stats, rets, segs, regime_ids)
        return {"stats": stats, "symbols_with_candles": sorted(rets), "days": MARKET_DAYS}
    try:
        return await asyncio.wait_for(go(), TIMEOUT_S)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        return {"stats": {}, "symbols_with_candles": [], "days": MARKET_DAYS}
