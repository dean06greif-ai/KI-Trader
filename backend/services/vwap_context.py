"""Tages-VWAP-Kontext je Asset für den KI-Prompt (rein & testbar).

Liefert dem KI-Trader für jedes Asset den Abstand zum Tages-VWAP (Reset 00:00 UTC,
identisch zum vwap_reclaim-Detektor in services/setup_backtest/detectors.py) plus
den 5m-Seitenwechsel-Status, damit das Setup `vwap_reclaim` erkannt werden kann:

  VWAP(Tag) 93.52: Preis +0.42% (+0.8 ATR5m) darüber | 5m: 2 Kerzen darüber nach
  9 darunter -> frischer VWAP-RECLAIM

Ohne Volumen (Spot-Forex) oder ohne Kerzen seit Tagesbeginn -> None (kein Token-Ballast).
"""
from typing import Dict, List, Optional

DAY_MS = 86_400_000
MIN_SESSION_BARS_1M = 15      # mind. 15 min seit 00:00 UTC, sonst ist der VWAP zu instabil
FRESH_MAX_BARS = 3            # Seitenwechsel gilt bis 3 5m-Kerzen als "frisch"
PRIOR_MIN_BARS = 3            # vorher mind. 3 5m-Schlusskurse auf der Gegenseite
STRETCHED_ATR = 2.0           # > 2 ATR vom VWAP = überdehnt (kein Reclaim-Entry)


def _atr(rows: List[Dict], n: int = 14) -> float:
    if len(rows) < 2:
        return 0.0
    trs = []
    for prev, cur in zip(rows[:-1], rows[1:]):
        h, lo, pc = float(cur["high"]), float(cur["low"]), float(prev["close"])
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    tail = trs[-n:]
    return sum(tail) / len(tail) if tail else 0.0


def _five_min_rows(candles: List[Dict]) -> List[Dict]:
    """1m -> 5m (nur vollständige 5m-Blöcke, am 5-Minuten-Raster ausgerichtet)."""
    out: List[Dict] = []
    bucket: List[Dict] = []
    cur_key = None
    for c in candles:
        key = int(c["timestamp"]) // 300_000
        if cur_key is not None and key != cur_key:
            if len(bucket) == 5:
                out.append(bucket[-1] | {"high": max(float(b["high"]) for b in bucket),
                                         "low": min(float(b["low"]) for b in bucket),
                                         "open": float(bucket[0]["open"])})
            bucket = []
        cur_key = key
        bucket.append(c)
    return out


def compute(candles: List[Dict], price: Optional[float] = None) -> Optional[Dict]:
    """VWAP-Kennzahlen aus 1m-Kerzen (dicts mit timestamp[ms], open/high/low/close/volume)."""
    if not candles:
        return None
    try:
        last_ts = int(candles[-1]["timestamp"])
    except (KeyError, TypeError, ValueError):
        return None
    day_start = (last_ts // DAY_MS) * DAY_MS
    if int(candles[0].get("timestamp") or 0) > day_start + 5 * 60_000:
        return None   # Tag nur angeschnitten -> VWAP nicht tagesverankert
    session = [c for c in candles if int(c.get("timestamp") or 0) >= day_start]
    if len(session) < MIN_SESSION_BARS_1M:
        return None
    cum_pv = cum_v = 0.0
    running: Dict[int, float] = {}
    for c in session:
        vol = float(c.get("volume") or 0)
        tp = (float(c["high"]) + float(c["low"]) + float(c["close"])) / 3.0
        cum_pv += tp * vol
        cum_v += vol
        if cum_v > 0:
            running[int(c["timestamp"])] = cum_pv / cum_v
    if cum_v <= 0:
        return None
    vwap = cum_pv / cum_v
    px = float(price or session[-1]["close"])
    if vwap <= 0 or px <= 0:
        return None
    dist_pct = (px - vwap) / vwap * 100.0
    rows5 = _five_min_rows(candles[-400:])
    atr5 = _atr(rows5[-15:])
    dist_atr = (px - vwap) / atr5 if atr5 > 0 else None
    # 5m-Schlusskurse der Session relativ zum laufenden VWAP (ohne Look-Ahead)
    sides: List[int] = []
    for r in rows5:
        ts = int(r["timestamp"])
        if ts < day_start or ts not in running:
            continue
        sides.append(1 if float(r["close"]) > running[ts] else -1)
    cur_side = 1 if px > vwap else -1
    streak = prior = 0
    for s in reversed(sides):
        if s == cur_side and prior == 0:
            streak += 1
        elif s != cur_side:
            prior += 1
        else:
            break
    state = None
    if prior >= PRIOR_MIN_BARS and 1 <= streak <= FRESH_MAX_BARS:
        state = "reclaim" if cur_side > 0 else "loss"
    elif dist_atr is not None and abs(dist_atr) > STRETCHED_ATR:
        state = "stretched"
    return {"vwap": round(vwap, 8), "dist_pct": round(dist_pct, 3),
            "dist_atr": None if dist_atr is None else round(dist_atr, 2),
            "side": "above" if cur_side > 0 else "below",
            "streak_5m": streak, "prior_5m": prior, "state": state}


def context_line(candles: List[Dict], price: Optional[float] = None) -> Optional[str]:
    """Kompakte Prompt-Zeile (None = kein VWAP berechenbar)."""
    v = compute(candles, price)
    if not v:
        return None
    where = "darüber" if v["side"] == "above" else "darunter"
    atr_txt = f" ({v['dist_atr']:+.1f} ATR5m)" if v["dist_atr"] is not None else ""
    line = f"VWAP(Tag) {v['vwap']:g}: Preis {v['dist_pct']:+.2f}%{atr_txt} {where}"
    if v["streak_5m"]:
        line += f" | 5m: {v['streak_5m']} Kerzen {where}"
        if v["prior_5m"]:
            line += f" nach {v['prior_5m']} auf der Gegenseite"
    if v["state"] == "reclaim":
        line += " -> frischer VWAP-RECLAIM (vwap_reclaim LONG prüfen, nur mit 1h-Trend)"
    elif v["state"] == "loss":
        line += " -> frischer VWAP-VERLUST (vwap_reclaim SHORT prüfen, nur mit 1h-Trend)"
    elif v["state"] == "stretched":
        line += " -> überdehnt (kein Reclaim-Entry, eher mean_reversion)"
    return line
