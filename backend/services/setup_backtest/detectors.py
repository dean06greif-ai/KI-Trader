"""Regelbasierte Detektoren der Phase-1-Setups (rein & testbar, kein Look-Ahead).

Jeder Detektor bekommt vorberechnete Features (5m-Basis, 15m/1h nur aus bereits
GESCHLOSSENEN Kerzen) und einen Parameter-Satz (Variante) und liefert Signale
mit Entry = Schlusskurs der Signalkerze, SL/TP als Preise. Die Simulation
beginnt erst mit der Folgekerze (simulator.py).

Varianten (VARIANTS): pro Setup 3 kleine Parameter-Sätze – der Runner testet sie
nacheinander (In-Sample wählt, Out-of-Sample bestätigt). Bewusst wenige
Varianten = Overfitting-Bremse.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np

from services import vec
from services.candles import CandleArray
from services.timeframes import aggregate_candles

BERLIN = ZoneInfo("Europe/Berlin")
M5 = 5 * 60_000
M15 = 15 * 60_000
H1 = 60 * 60_000


@dataclass
class Signal:
    idx: int          # Index der Signalkerze (5m); Simulation ab idx+1
    side: str         # LONG | SHORT
    entry: float
    sl: float
    tp1: float
    tpf: float
    note: str = ""


def _roll(fn, arr: np.ndarray, n: int) -> np.ndarray:
    """Rollierende Statistik über die VORHERIGEN n Kerzen (ohne aktuelle)."""
    out = np.full(arr.shape[0], np.nan)
    if arr.shape[0] <= n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(arr[:-1], n)
    out[n:] = fn(w, axis=1)
    return out


def _shift(arr: np.ndarray, k: int) -> np.ndarray:
    out = np.full(arr.shape[0], np.nan)
    if k < arr.shape[0]:
        out[k:] = arr[:-k] if k else arr
    return out


class Features:
    """Indikatoren auf 5m + zugeordnete GESCHLOSSENE 15m/1h-Kerzen.

    `asset_class` aktiviert den Handelszeit-Filter der Klasse (ACTIVE_HOURS) und
    den ATR-Boden: In dünnen Übernacht-Phasen (Index-/Rohstoff-Perps) kollabiert
    der 5m-ATR, jede Mini-Bewegung sähe sonst wie ein Signal aus."""

    def __init__(self, ca1: CandleArray, asset_class: Optional[str] = None):
        self.c5: CandleArray = aggregate_candles(ca1, "5m", drop_partial=True)
        self.c15: CandleArray = aggregate_candles(ca1, "15m", drop_partial=True)
        self.c60: CandleArray = aggregate_candles(ca1, "1h", drop_partial=True)
        c = self.c5
        self.n = len(c)
        self.ema20 = vec.ema(c.cl, 20)
        self.ema50 = vec.ema(c.cl, 50)
        self.rsi = vec.rsi(c.cl, 14)
        self.atr = vec.atr(c.hi, c.lo, c.cl, 14)
        self.atr_fast = vec.atr(c.hi, c.lo, c.cl, 15)
        self.atr_slow = vec.atr(c.hi, c.lo, c.cl, 96)
        self.vol_avg = _roll(np.mean, c.vol, 20)
        self.atr60 = vec.atr(self.c60.hi, self.c60.lo, self.c60.cl, 14) if len(self.c60) > 15 \
            else np.full(len(self.c60), np.nan)
        close_ts = c.ts + M5
        # letzte 15m-/1h-Kerze, die zum Schluss der 5m-Kerze bereits GESCHLOSSEN ist
        self.i15 = np.searchsorted(self.c15.ts, close_ts - M15, side="right") - 1
        self.i60 = np.searchsorted(self.c60.ts, close_ts - H1, side="right") - 1
        self._berlin: Optional[List[datetime]] = None
        atr_med = _roll(np.nanmedian, np.nan_to_num(self.atr, nan=0.0), 288)
        self.active = (~np.isnan(self.atr)) & (self.atr > 0) \
            & (np.isnan(atr_med) | (self.atr >= ATR_FLOOR * atr_med))
        hours = ACTIVE_HOURS.get(asset_class or "")
        if hours:
            mask = np.zeros(self.n, dtype=bool)
            for k, d in enumerate(self.berlin()):
                hm = d.hour * 60 + d.minute
                mask[k] = d.weekday() < 5 and hours[0] <= hm < hours[1]
            self.active &= mask

    def berlin(self) -> List[datetime]:
        if self._berlin is None:
            self._berlin = [datetime.fromtimestamp(int(t) / 1000, BERLIN) for t in self.c5.ts]
        return self._berlin

    def ok(self, i: int) -> bool:
        return i >= 100 and bool(self.active[i])


# Handelszeiten je Klasse (Berlin, Minuten seit Mitternacht) – analog zu den
# Klassen-Hinweisen in setup_asset_class.HINTS. Krypto: 24/7 (kein Filter).
ACTIVE_HOURS: Dict[str, tuple] = {
    "indices": (15 * 60 + 30, 22 * 60),      # US-Cash-Session
    "resources": (9 * 60, 22 * 60),          # London + US
    "forex": (8 * 60, 21 * 60),              # London + NY-Überlappung
}
ATR_FLOOR = 0.35                             # ATR < 35 % des 24h-Medians = zu dünn


def _levels(side: str, entry: float, risk: float, tp_r: float, tp1_r: float = 1.0):
    d = 1 if side == "LONG" else -1
    return entry - d * risk, entry + d * risk * tp1_r, entry + d * risk * tp_r


# --------------------------------------------------------------------------
# Detektoren
# --------------------------------------------------------------------------
def detect_breakout(f: Features, p: Dict) -> List[Signal]:
    c, n = f.c5, p["lookback"]
    hi_prev, lo_prev = _roll(np.max, c.hi, n), _roll(np.min, c.lo, n)
    body = c.cl - c.op
    out = []
    for i in range(f.n):
        if not f.ok(i) or np.isnan(hi_prev[i]) or np.isnan(f.vol_avg[i]):
            continue
        # echte Range: die Vor-Range darf nicht selbst schon ein Trend sein
        if hi_prev[i] - lo_prev[i] > p["range_atr"] * f.atr[i]:
            continue
        strong_vol = f.vol_avg[i] <= 0 or c.vol[i] >= p["vol_mult"] * f.vol_avg[i]
        if c.cl[i] > hi_prev[i] and body[i] > 0.5 * f.atr[i] and strong_vol:
            side = "LONG"
        elif c.cl[i] < lo_prev[i] and -body[i] > 0.5 * f.atr[i] and strong_vol:
            side = "SHORT"
        else:
            continue
        risk = p["sl_atr"] * f.atr[i]
        sl, tp1, tpf = _levels(side, c.cl[i], risk, p["tp_r"])
        out.append(Signal(i, side, float(c.cl[i]), sl, tp1, tpf, f"Range {n} Kerzen"))
    return out


def detect_squeeze_breakout(f: Features, p: Dict) -> List[Signal]:
    c = f.c5
    sma = _roll(np.mean, c.cl, 20)
    std = _roll(np.std, c.cl, 20)
    width = np.where(sma > 0, 4 * std / sma, np.nan)
    width_avg = _roll(np.nanmean, np.nan_to_num(width, nan=0.0), 96)
    upper, lower = sma + 2 * std, sma - 2 * std
    squeezed = (width < p["squeeze"] * width_avg).astype(float)
    # Kompression muss min_bars Kerzen bestanden haben (bis zur Vorkerze)
    squeezed_prev = _roll(np.min, squeezed, p["min_bars"]) == 1
    out = []
    for i in range(f.n):
        if not f.ok(i) or not squeezed_prev[i] or np.isnan(upper[i]):
            continue
        if c.cl[i] > upper[i]:
            side = "LONG"
        elif c.cl[i] < lower[i]:
            side = "SHORT"
        else:
            continue
        risk = p["sl_atr"] * f.atr[i]
        sl, tp1, tpf = _levels(side, c.cl[i], risk, p["tp_r"])
        out.append(Signal(i, side, float(c.cl[i]), sl, tp1, tpf, "BB-Squeeze"))
    return out


def _range_touches(hi: np.ndarray, lo: np.ndarray, top: float, bot: float, tol: float):
    return int(np.sum(hi >= top - tol)), int(np.sum(lo <= bot + tol))


def detect_range_fade(f: Features, p: Dict) -> List[Signal]:
    c, n = f.c5, p["lookback"]
    rh, rl = _roll(np.max, c.hi, n), _roll(np.min, c.lo, n)
    ema_prev = _shift(f.ema20, 12)
    out = []
    for i in range(f.n):
        if not f.ok(i) or np.isnan(rh[i]) or np.isnan(ema_prev[i]):
            continue
        width = rh[i] - rl[i]
        if width <= 0 or width > p["width_atr"] * f.atr[i] \
                or abs(f.ema20[i] - ema_prev[i]) > 0.5 * f.atr[i]:
            continue
        top_t, bot_t = _range_touches(c.hi[i - n:i], c.lo[i - n:i], rh[i], rl[i], 0.15 * width)
        if top_t < 2 or bot_t < 2:
            continue
        mid = (rh[i] + rl[i]) / 2
        if c.hi[i] >= rh[i] - 0.1 * width and c.cl[i] < rh[i] - 0.2 * width and c.cl[i] < c.op[i]:
            entry = float(c.cl[i])
            sl = rh[i] + p["sl_atr"] * f.atr[i]
            out.append(Signal(i, "SHORT", entry, sl, entry - (entry - mid) / 2, mid, "Range-Oberkante"))
        elif c.lo[i] <= rl[i] + 0.1 * width and c.cl[i] > rl[i] + 0.2 * width and c.cl[i] > c.op[i]:
            entry = float(c.cl[i])
            sl = rl[i] - p["sl_atr"] * f.atr[i]
            out.append(Signal(i, "LONG", entry, sl, entry + (mid - entry) / 2, mid, "Range-Unterkante"))
    return out


def detect_mean_reversion(f: Features, p: Dict) -> List[Signal]:
    c = f.c5
    out = []
    for i in range(f.n):
        if not f.ok(i) or np.isnan(f.rsi[i]) or np.isnan(f.ema20[i]):
            continue
        dist = f.ema20[i] - c.cl[i]
        if f.rsi[i] < p["rsi_lo"] and dist > p["dist_atr"] * f.atr[i] and c.cl[i] > c.op[i]:
            side, tpf = "LONG", float(f.ema20[i])
        elif f.rsi[i] > p["rsi_hi"] and -dist > p["dist_atr"] * f.atr[i] and c.cl[i] < c.op[i]:
            side, tpf = "SHORT", float(f.ema20[i])
        else:
            continue
        entry = float(c.cl[i])
        if abs(tpf - entry) < 0.3 * f.atr[i]:
            continue
        d = 1 if side == "LONG" else -1
        sl = entry - d * p["sl_atr"] * f.atr[i]
        out.append(Signal(i, side, entry, sl, entry + (tpf - entry) / 2, tpf, f"RSI {f.rsi[i]:.0f}"))
    return out


def detect_htf_range(f: Features, p: Dict) -> List[Signal]:
    c, c15, c60, n = f.c5, f.c15, f.c60, p["lookback_h"]
    if len(c60) <= n + 1 or len(c15) < 2:
        return []
    rh, rl = _roll(np.max, c60.hi, n), _roll(np.min, c60.lo, n)
    out = []
    last_j15 = -1
    for i in range(f.n):
        j15, j60 = int(f.i15[i]), int(f.i60[i])
        if not f.ok(i) or j15 <= 0 or j60 <= 0 or j15 == last_j15:
            continue
        last_j15 = j15
        if np.isnan(rh[j60]) or np.isnan(f.atr60[j60]):
            continue
        width = rh[j60] - rl[j60]
        if width <= 0 or width > p["width_atr"] * f.atr60[j60]:
            continue
        top_t, bot_t = _range_touches(c60.hi[j60 - n:j60], c60.lo[j60 - n:j60], rh[j60], rl[j60], 0.15 * width)
        if top_t < 2 or bot_t < 2:
            continue
        mid = (rh[j60] + rl[j60]) / 2
        entry = float(c.cl[i])
        if c15.hi[j15] >= rh[j60] - 0.1 * width and c15.cl[j15] < rh[j60] - 0.15 * width and entry < rh[j60]:
            out.append(Signal(i, "SHORT", entry, rh[j60] + p["sl_atr"] * f.atr60[j60], mid, rl[j60], "1h-Range oben"))
        elif c15.lo[j15] <= rl[j60] + 0.1 * width and c15.cl[j15] > rl[j60] + 0.15 * width and entry > rl[j60]:
            out.append(Signal(i, "LONG", entry, rl[j60] - p["sl_atr"] * f.atr60[j60], mid, rh[j60], "1h-Range unten"))
    return out


_SESSIONS = ((9, 0, "London"), (15, 30, "US"))


def detect_session_open(f: Features, p: Dict) -> List[Signal]:
    c = f.c5
    bl = f.berlin()
    out = []
    i = 0
    while i < f.n:
        d = bl[i]
        if d.weekday() >= 5 or not f.ok(i):
            i += 1
            continue
        sess = next((s for s in _SESSIONS if d.hour == s[0] and d.minute == s[1]), None)
        if sess is None or i + 3 >= f.n:
            i += 1
            continue
        or_hi, or_lo = float(np.max(c.hi[i:i + 3])), float(np.min(c.lo[i:i + 3]))
        k = i + 2
        if or_hi <= or_lo or np.isnan(f.atr_slow[k]) or f.atr_slow[k] <= 0:
            i += 3
            continue
        ratio = f.atr_fast[k] / f.atr_slow[k]
        mode = "breakout" if ratio >= p["vol_high"] else "fade" if ratio <= p["vol_low"] else None
        end = min(f.n, i + 18)  # 90 Minuten Fenster
        sig = None
        if mode:
            for j in range(i + 3, end):
                if mode == "breakout":
                    if c.cl[j] > or_hi:
                        sig = Signal(j, "LONG", float(c.cl[j]), (or_hi + or_lo) / 2,
                                     0, 0, f"{sess[2]}-Open Breakout")
                    elif c.cl[j] < or_lo:
                        sig = Signal(j, "SHORT", float(c.cl[j]), (or_hi + or_lo) / 2,
                                     0, 0, f"{sess[2]}-Open Breakout")
                    if sig:
                        risk = abs(sig.entry - sig.sl)
                        if risk <= 0:
                            sig = None
                            continue
                        sig.sl, sig.tp1, sig.tpf = _levels(sig.side, sig.entry, risk, p["tp_r"])
                        break
                else:
                    if c.hi[j] > or_hi and c.cl[j] < or_hi:
                        sig = Signal(j, "SHORT", float(c.cl[j]), float(c.hi[j]) + 0.2 * f.atr[j],
                                     (or_hi + or_lo) / 2, or_lo, f"{sess[2]}-Open Fade")
                    elif c.lo[j] < or_lo and c.cl[j] > or_lo:
                        sig = Signal(j, "LONG", float(c.cl[j]), float(c.lo[j]) - 0.2 * f.atr[j],
                                     (or_hi + or_lo) / 2, or_hi, f"{sess[2]}-Open Fade")
                    if sig:
                        break
        if sig:
            out.append(sig)
        i = end
    return out


def detect_trend_follow(f: Features, p: Dict) -> List[Signal]:
    c = f.c5
    slope_prev = _shift(f.ema50, p["slope_bars"])
    out = []
    for i in range(f.n):
        if not f.ok(i) or np.isnan(f.ema50[i]) or np.isnan(slope_prev[i]):
            continue
        tol = p["touch_atr"] * f.atr[i]
        # Pullback = Vorkerze hat die EMA20 berührt/unterschritten, Signalkerze erobert sie zurück
        if f.ema20[i] > f.ema50[i] and f.ema50[i] > slope_prev[i] \
                and c.lo[i - 1] <= f.ema20[i - 1] + tol and c.cl[i - 1] <= f.ema20[i - 1] + tol \
                and c.cl[i] > f.ema20[i] and c.cl[i] > c.op[i]:
            side, ext = "LONG", float(np.min(c.lo[max(0, i - 2):i + 1]))
        elif f.ema20[i] < f.ema50[i] and f.ema50[i] < slope_prev[i] \
                and c.hi[i - 1] >= f.ema20[i - 1] - tol and c.cl[i - 1] >= f.ema20[i - 1] - tol \
                and c.cl[i] < f.ema20[i] and c.cl[i] < c.op[i]:
            side, ext = "SHORT", float(np.max(c.hi[max(0, i - 2):i + 1]))
        else:
            continue
        entry = float(c.cl[i])
        d = 1 if side == "LONG" else -1
        sl = ext - d * 0.3 * f.atr[i]
        risk = abs(entry - sl)
        if risk < 0.2 * f.atr[i]:
            continue
        _, tp1, tpf = _levels(side, entry, risk, p["tp_r"])
        out.append(Signal(i, side, entry, sl, tp1, tpf, "EMA20-Pullback im Trend"))
    return out


def _pivots(c: CandleArray, k: int = 3):
    """15m-Pivots (Hoch/Tief); Level erst k Kerzen nach dem Pivot verfügbar."""
    highs, lows = [], []
    for j in range(k, len(c) - k):
        seg_h, seg_l = c.hi[j - k:j + k + 1], c.lo[j - k:j + k + 1]
        if c.hi[j] >= seg_h.max():
            highs.append((int(c.ts[j + k]), float(c.hi[j])))
        if c.lo[j] <= seg_l.min():
            lows.append((int(c.ts[j + k]), float(c.lo[j])))
    return highs, lows


def detect_pullback(f: Features, p: Dict) -> List[Signal]:
    c = f.c5
    highs, lows = _pivots(f.c15, p["pivot_k"])
    if not highs and not lows:
        return []
    h_ts = np.array([t for t, _ in highs] or [0]); h_px = np.array([x for _, x in highs] or [np.nan])
    l_ts = np.array([t for t, _ in lows] or [0]); l_px = np.array([x for _, x in lows] or [np.nan])
    max_age, min_age = 200 * M15, 4 * M15
    out = []
    for i in range(f.n):
        if not f.ok(i):
            continue
        ts, tol = int(c.ts[i]), p["touch_atr"] * f.atr[i]
        sup = l_px[(l_ts + min_age <= ts) & (l_ts >= ts - max_age)]
        res = h_px[(h_ts + min_age <= ts) & (h_ts >= ts - max_age)]
        entry = float(c.cl[i])
        if sup.size and c.cl[i] > c.op[i]:
            near = sup[(c.lo[i] <= sup + tol) & (c.lo[i] >= sup - 1.5 * tol) & (entry > sup)]
            if near.size:
                lvl = float(near.max())
                sl = lvl - p["sl_atr"] * f.atr[i]
                _, tp1, tpf = _levels("LONG", entry, entry - sl, p["tp_r"])
                out.append(Signal(i, "LONG", entry, sl, tp1, tpf, "Support-Pullback"))
                continue
        if res.size and c.cl[i] < c.op[i]:
            near = res[(c.hi[i] >= res - tol) & (c.hi[i] <= res + 1.5 * tol) & (entry < res)]
            if near.size:
                lvl = float(near.min())
                sl = lvl + p["sl_atr"] * f.atr[i]
                _, tp1, tpf = _levels("SHORT", entry, sl - entry, p["tp_r"])
                out.append(Signal(i, "SHORT", entry, sl, tp1, tpf, "Resistance-Pullback"))
    return out


def detect_divergence(f: Features, p: Dict) -> List[Signal]:
    c, n = f.c5, p["lookback"]
    out = []
    for i in range(n + 6, f.n):
        if not f.ok(i) or np.isnan(f.rsi[i - 1]):
            continue
        a, b = i - n, i - 5
        seg_lo, seg_hi = c.lo[a:b], c.hi[a:b]
        k_lo, k_hi = a + int(np.argmin(seg_lo)), a + int(np.argmax(seg_hi))
        if c.lo[i - 1] < c.lo[k_lo] and f.rsi[i - 1] > f.rsi[k_lo] + p["rsi_gap"] \
                and f.rsi[i - 1] < 40 and c.cl[i] > c.op[i] and c.cl[i] > c.cl[i - 1]:
            entry = float(c.cl[i])
            sl = float(c.lo[i - 1]) - 0.3 * f.atr[i]
            _, tp1, tpf = _levels("LONG", entry, entry - sl, p["tp_r"])
            out.append(Signal(i, "LONG", entry, sl, tp1, tpf, "Bullische RSI-Divergenz"))
        elif c.hi[i - 1] > c.hi[k_hi] and f.rsi[i - 1] < f.rsi[k_hi] - p["rsi_gap"] \
                and f.rsi[i - 1] > 60 and c.cl[i] < c.op[i] and c.cl[i] < c.cl[i - 1]:
            entry = float(c.cl[i])
            sl = float(c.hi[i - 1]) + 0.3 * f.atr[i]
            _, tp1, tpf = _levels("SHORT", entry, sl - entry, p["tp_r"])
            out.append(Signal(i, "SHORT", entry, sl, tp1, tpf, "Bärische RSI-Divergenz"))
    return out


# --------------------------------------------------------------------------
# Registry: Detektor + Varianten je Setup (Reihenfolge = Test-Reihenfolge)
# --------------------------------------------------------------------------
DETECTORS: Dict[str, Callable[[Features, Dict], List[Signal]]] = {
    "breakout": detect_breakout,
    "squeeze_breakout": detect_squeeze_breakout,
    "range_fade": detect_range_fade,
    "mean_reversion": detect_mean_reversion,
    "htf_range": detect_htf_range,
    "session_open": detect_session_open,
    "trend_follow": detect_trend_follow,
    "pullback": detect_pullback,
    "divergence": detect_divergence,
}

VARIANTS: Dict[str, List[Dict]] = {
    "breakout": [
        {"name": "standard", "lookback": 36, "range_atr": 4.0, "sl_atr": 1.2, "tp_r": 2.0, "vol_mult": 1.3},
        {"name": "konservativ", "lookback": 48, "range_atr": 3.0, "sl_atr": 1.5, "tp_r": 1.5, "vol_mult": 1.5},
        {"name": "aggressiv", "lookback": 24, "range_atr": 5.0, "sl_atr": 1.0, "tp_r": 2.5, "vol_mult": 1.1},
    ],
    "squeeze_breakout": [
        {"name": "standard", "squeeze": 0.6, "min_bars": 6, "sl_atr": 1.5, "tp_r": 3.0},
        {"name": "konservativ", "squeeze": 0.5, "min_bars": 9, "sl_atr": 1.8, "tp_r": 2.0},
        {"name": "aggressiv", "squeeze": 0.7, "min_bars": 4, "sl_atr": 1.2, "tp_r": 3.0},
    ],
    "range_fade": [
        {"name": "standard", "lookback": 48, "width_atr": 3.0, "sl_atr": 0.3},
        {"name": "eng", "lookback": 36, "width_atr": 2.5, "sl_atr": 0.4},
        {"name": "weit", "lookback": 72, "width_atr": 4.0, "sl_atr": 0.3},
    ],
    "mean_reversion": [
        {"name": "standard", "rsi_lo": 25, "rsi_hi": 75, "dist_atr": 2.0, "sl_atr": 1.5},
        {"name": "konservativ", "rsi_lo": 20, "rsi_hi": 80, "dist_atr": 2.5, "sl_atr": 1.5},
        {"name": "aggressiv", "rsi_lo": 30, "rsi_hi": 70, "dist_atr": 1.5, "sl_atr": 1.2},
    ],
    "htf_range": [
        {"name": "standard", "lookback_h": 48, "width_atr": 4.0, "sl_atr": 0.5},
        {"name": "eng", "lookback_h": 24, "width_atr": 3.0, "sl_atr": 0.5},
        {"name": "weit", "lookback_h": 72, "width_atr": 5.0, "sl_atr": 0.6},
    ],
    "session_open": [
        {"name": "standard", "vol_high": 1.3, "vol_low": 0.85, "tp_r": 2.0},
        {"name": "konservativ", "vol_high": 1.5, "vol_low": 0.8, "tp_r": 1.5},
        {"name": "aggressiv", "vol_high": 1.2, "vol_low": 0.9, "tp_r": 2.5},
    ],
    "trend_follow": [
        {"name": "standard", "slope_bars": 12, "touch_atr": 0.2, "tp_r": 2.0},
        {"name": "konservativ", "slope_bars": 24, "touch_atr": 0.1, "tp_r": 1.5},
        {"name": "aggressiv", "slope_bars": 6, "touch_atr": 0.3, "tp_r": 2.5},
    ],
    "pullback": [
        {"name": "standard", "pivot_k": 5, "touch_atr": 0.3, "sl_atr": 0.5, "tp_r": 2.0},
        {"name": "konservativ", "pivot_k": 8, "touch_atr": 0.2, "sl_atr": 0.7, "tp_r": 1.5},
        {"name": "aggressiv", "pivot_k": 4, "touch_atr": 0.4, "sl_atr": 0.4, "tp_r": 2.5},
    ],
    "divergence": [
        {"name": "standard", "lookback": 30, "rsi_gap": 3.0, "tp_r": 1.5},
        {"name": "konservativ", "lookback": 40, "rsi_gap": 5.0, "tp_r": 1.2},
        {"name": "aggressiv", "lookback": 24, "rsi_gap": 2.0, "tp_r": 2.0},
    ],
}

# Nicht formalisierbar in Phase 1 (siehe Plan) – nur informativ für UI/API
NOT_BACKTESTABLE: Dict[str, str] = {
    "momentum_news": "braucht News-/Volumen-Impulse (kein Kursmuster)",
    "hedge": "Gegenposition zur Exposure – kein eigenständiges Signal",
    "funding_fade": "braucht Funding-Historie (Phase 2)",
    "order_block": "SMC-Zonen (Phase 2)",
    "fvg_fill": "SMC-Zonen (Phase 2)",
    "liquidity_sweep": "Sweep-Erkennung (Phase 2)",
    "swing_trend": "Swing-Horizont, Runner-Management – nicht 5m-simulierbar",
}


def run_detector(setup: str, f: Features, variant_idx: int) -> List[Signal]:
    fn = DETECTORS[setup]
    variants = VARIANTS[setup]
    return fn(f, variants[variant_idx % len(variants)])


def run_detector_params(setup: str, f: Features, params: Dict) -> List[Signal]:
    """Detektor mit freiem Parameter-Satz (Feintuning, siehe tune_candidates)."""
    return DETECTORS[setup](f, params)


# Feintuning nach erfolglosen Varianten: nur die Risiko-/Ziel-Parameter werden
# in kleinen Schritten um die Basis-Variante variiert (Overfitting-Bremse:
# max. 4 Kandidaten, Signal-Logik selbst bleibt unangetastet).
TUNE_KEYS = ("sl_atr", "tp_r")
TUNE_FACTORS = (0.8, 1.25)


def tune_candidates(setup: str, base: Dict) -> List[Dict]:
    keys = [k for k in TUNE_KEYS if k in base]
    if not keys:
        return []
    out: List[Dict] = []
    if len(keys) == 1:
        k = keys[0]
        for fac in TUNE_FACTORS:
            out.append({**base, k: round(base[k] * fac, 3),
                        "name": f"{base.get('name', 'v')}·{k}×{fac:g}"})
        return out
    for f1 in TUNE_FACTORS:
        for f2 in TUNE_FACTORS:
            out.append({**base, keys[0]: round(base[keys[0]] * f1, 3),
                        keys[1]: round(base[keys[1]] * f2, 3),
                        "name": f"{base.get('name', 'v')}·{keys[0]}×{f1:g}·{keys[1]}×{f2:g}"})
    return out
