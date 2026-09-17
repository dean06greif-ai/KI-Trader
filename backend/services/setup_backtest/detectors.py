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
        # MACD(12/26/9) für trend_follow2 (dieselben Indikatoren wie die Website-
        # Strategie „TrendFolge2"; Live-Pendant: services/tf2_signal.py)
        self.macd_line, self.macd_sig = vec.macd(c.cl)
        self.atr60 = vec.atr(self.c60.hi, self.c60.lo, self.c60.cl, 14) if len(self.c60) > 15 \
            else np.full(len(self.c60), np.nan)
        # 1h-Trend (EMA20 vs. EMA50 der GESCHLOSSENEN Stundenkerzen) für den optionalen
        # Regime-Filter `htf_trend` (apply_filters)
        if len(self.c60) > 50:
            e20, e50 = vec.ema(self.c60.cl, 20), vec.ema(self.c60.cl, 50)
            self.trend60 = np.where(np.isnan(e20) | np.isnan(e50), 0, np.sign(e20 - e50)).astype(int)
        else:
            self.trend60 = np.zeros(len(self.c60), dtype=int)
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
        body_min = p.get("body_atr", 0.5) * f.atr[i]
        if c.cl[i] > hi_prev[i] and body[i] > body_min and strong_vol:
            side = "LONG"
        elif c.cl[i] < lo_prev[i] and -body[i] > body_min and strong_vol:
            side = "SHORT"
        else:
            continue
        risk = p["sl_atr"] * f.atr[i]
        sl, tp1, tpf = _levels(side, c.cl[i], risk, p["tp_r"], p.get("tp1_r", 1.0))
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
        sl, tp1, tpf = _levels(side, c.cl[i], risk, p["tp_r"], p.get("tp1_r", 1.0))
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
                        sig.sl, sig.tp1, sig.tpf = _levels(sig.side, sig.entry, risk, p["tp_r"],
                                                           p.get("tp1_r", 1.0))
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
        sl = ext - d * p.get("sl_atr", 0.3) * f.atr[i]
        risk = abs(entry - sl)
        if risk < 0.2 * f.atr[i]:
            continue
        _, tp1, tpf = _levels(side, entry, risk, p["tp_r"], p.get("tp1_r", 1.0))
        out.append(Signal(i, side, entry, sl, tp1, tpf, "EMA20-Pullback im Trend"))
    return out


def detect_trend_follow2(f: Features, p: Dict) -> List[Signal]:
    """Trendfolge 2 (Indikatoren der Website-Strategie „TrendFolge2"): Preis-
    änderung über `lookback` Kerzen + MACD-Kreuz + rel. Volumen; SL an der
    Struktur (Tief/Hoch der letzten `sl_lookback` Kerzen), Ziel in R."""
    c, n, k = f.c5, int(p["lookback"]), int(p["sl_lookback"])
    ref = _shift(c.cl, n)
    chg = np.where(ref > 0, (c.cl - ref) / np.where(ref > 0, ref, 1) * 100, np.nan)
    line, sig = f.macd_line, f.macd_sig
    out = []
    for i in range(max(n, k) + 1, f.n):
        if not f.ok(i) or np.isnan(chg[i]) or np.isnan(sig[i - 1]) or np.isnan(sig[i]) \
                or np.isnan(f.vol_avg[i]) or f.vol_avg[i] <= 0:
            continue
        if c.vol[i] < p["vol_mult"] * f.vol_avg[i]:
            continue
        if line[i - 1] <= sig[i - 1] and line[i] > sig[i] and chg[i] > p["chg_pct"]:
            side, ext, d = "LONG", float(np.min(c.lo[i - k:i + 1])), 1
        elif line[i - 1] >= sig[i - 1] and line[i] < sig[i] and chg[i] < -p["chg_pct"]:
            side, ext, d = "SHORT", float(np.max(c.hi[i - k:i + 1])), -1
        else:
            continue
        entry = float(c.cl[i])
        sl = ext - d * p.get("sl_atr", 0.2) * f.atr[i]
        risk = abs(entry - sl)
        if risk < 0.2 * f.atr[i] or risk > 3.0 * f.atr[i]:
            continue
        _, tp1, tpf = _levels(side, entry, risk, p["tp_r"], p.get("tp1_r", 1.0))
        out.append(Signal(i, side, entry, sl, tp1, tpf, f"TF2 MACD-Kreuz Δ{chg[i]:+.1f}%"))
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
                _, tp1, tpf = _levels("LONG", entry, entry - sl, p["tp_r"], p.get("tp1_r", 1.0))
                out.append(Signal(i, "LONG", entry, sl, tp1, tpf, "Support-Pullback"))
                continue
        if res.size and c.cl[i] < c.op[i]:
            near = res[(c.hi[i] >= res - tol) & (c.hi[i] <= res + 1.5 * tol) & (entry < res)]
            if near.size:
                lvl = float(near.min())
                sl = lvl + p["sl_atr"] * f.atr[i]
                _, tp1, tpf = _levels("SHORT", entry, sl - entry, p["tp_r"], p.get("tp1_r", 1.0))
                out.append(Signal(i, "SHORT", entry, sl, tp1, tpf, "Resistance-Pullback"))
    return out


def detect_divergence(f: Features, p: Dict) -> List[Signal]:
    c, n = f.c5, p["lookback"]
    rsi_lo, rsi_hi, sl_atr, tp1_r = p.get("rsi_lo", 40), p.get("rsi_hi", 60), p.get("sl_atr", 0.3), p.get("tp1_r", 1.0)
    out = []
    for i in range(n + 6, f.n):
        if not f.ok(i) or np.isnan(f.rsi[i - 1]):
            continue
        a, b = i - n, i - 5
        seg_lo, seg_hi = c.lo[a:b], c.hi[a:b]
        k_lo, k_hi = a + int(np.argmin(seg_lo)), a + int(np.argmax(seg_hi))
        if c.lo[i - 1] < c.lo[k_lo] and f.rsi[i - 1] > f.rsi[k_lo] + p["rsi_gap"] \
                and f.rsi[i - 1] < rsi_lo and c.cl[i] > c.op[i] and c.cl[i] > c.cl[i - 1]:
            entry = float(c.cl[i])
            sl = float(c.lo[i - 1]) - sl_atr * f.atr[i]
            _, tp1, tpf = _levels("LONG", entry, entry - sl, p["tp_r"], tp1_r)
            out.append(Signal(i, "LONG", entry, sl, tp1, tpf, "Bullische RSI-Divergenz"))
        elif c.hi[i - 1] > c.hi[k_hi] and f.rsi[i - 1] < f.rsi[k_hi] - p["rsi_gap"] \
                and f.rsi[i - 1] > rsi_hi and c.cl[i] < c.op[i] and c.cl[i] < c.cl[i - 1]:
            entry = float(c.cl[i])
            sl = float(c.hi[i - 1]) + sl_atr * f.atr[i]
            _, tp1, tpf = _levels("SHORT", entry, sl - entry, p["tp_r"], tp1_r)
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
    "trend_follow2": detect_trend_follow2,
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
    "trend_follow2": [
        # lookback 5 = Default "Preisänderung Lookback" der Website-Strategie (custom_strategy)
        {"name": "standard", "lookback": 5, "chg_pct": 0.56, "vol_mult": 1.2, "sl_lookback": 10, "tp_r": 2.0},
        {"name": "konservativ", "lookback": 8, "chg_pct": 0.8, "vol_mult": 1.5, "sl_lookback": 12, "tp_r": 1.5},
        {"name": "aggressiv", "lookback": 4, "chg_pct": 0.4, "vol_mult": 1.0, "sl_lookback": 8, "tp_r": 2.5},
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
    """Detektor mit freiem Parameter-Satz (Feintuning/KI-Revision) inkl. der
    optionalen Regime-/Zeit-/Seiten-Filter (apply_filters)."""
    return apply_filters(f, DETECTORS[setup](f, params), params)


# --------------------------------------------------------------------------
# Optionale Stellschrauben der KI-Revision (Basis-Varianten bleiben unverändert:
# jeder Schlüssel hat den Default, der dem bisherigen Verhalten entspricht).
#   Schlüssel -> (Default, Minimum, Maximum, Kurzbeschreibung für den Prompt)
# --------------------------------------------------------------------------
COMMON_PARAMS: Dict[str, tuple] = {
    "tp1_r": (1.0, 0.5, 2.5, "Teilgewinn (halbe Position) in R; danach SL auf Einstieg"),
    "max_bars": (288, 24, 576, "Zeit-Exit nach n 5m-Kerzen (288 = 24 h)"),
    "sides": (0, -1, 1, "0 = Long+Short, 1 = nur Long, -1 = nur Short"),
    "vol_min": (0.0, 0.0, 2.0, "nur wenn ATR(15)/ATR(96) >= Wert (0 = aus; >1 = nur bei steigender Volatilität)"),
    "vol_max": (0.0, 0.0, 3.0, "nur wenn ATR(15)/ATR(96) <= Wert (0 = aus; <1 = nur in ruhigen Phasen)"),
    "hour_from": (0, 0, 23, "Signale erst ab dieser Stunde (Berlin)"),
    "hour_to": (24, 1, 24, "Signale nur bis vor dieser Stunde (Berlin)"),
    "htf_trend": (0, -1, 1, "1 = nur mit dem 1h-Trend (EMA20>EMA50), -1 = nur gegen ihn, 0 = aus"),
    "min_trades_factor": (1.0, 0.7, 1.5, "Faktor auf die Mindest-Trade-Anzahl (IS/OOS) dieses Setups – "
                                         "<1 nur bei begründet seltenen Mustern, >1 wenn das Setup häufig feuert"),
}
# tp1_r gilt nur, wo TP1 aus R abgeleitet wird (nicht bei Range-Mitte/EMA-Zielen)
NO_TP1R = frozenset({"range_fade", "mean_reversion", "htf_range"})
SETUP_EXTRA_PARAMS: Dict[str, Dict[str, tuple]] = {
    "breakout": {"body_atr": (0.5, 0.2, 1.5, "Mindest-Kerzenkörper der Ausbruchskerze in ATR")},
    "trend_follow": {"sl_atr": (0.3, 0.1, 1.0, "SL-Puffer hinter dem Pullback-Extrem in ATR")},
    "trend_follow2": {"sl_atr": (0.2, 0.0, 1.0, "SL-Puffer hinter dem Struktur-Tief/-Hoch in ATR")},
    "divergence": {"sl_atr": (0.3, 0.1, 1.0, "SL-Puffer hinter dem Divergenz-Extrem in ATR"),
                   "rsi_lo": (40, 25, 50, "bullische Divergenz nur bei RSI unter diesem Wert"),
                   "rsi_hi": (60, 50, 75, "bärische Divergenz nur bei RSI über diesem Wert")},
}
BASE_PARAM_HELP: Dict[str, str] = {
    "lookback": "Rückblick-Kerzen (5m) der Range/des Vergleichs", "lookback_h": "Rückblick in 1h-Kerzen",
    "range_atr": "max. Breite der Vor-Range in ATR (kleiner = echte Range)",
    "sl_atr": "Stop-Abstand in ATR", "tp_r": "Ziel in R (Vielfaches des Risikos)",
    "vol_mult": "Volumen der Signalkerze mind. x-faches des 20er-Schnitts",
    "squeeze": "Bollinger-Breite unter x-fachem des 96er-Schnitts", "min_bars": "Kompression muss n Kerzen bestehen",
    "width_atr": "max. Range-Breite in ATR", "rsi_lo": "RSI-Schwelle Long", "rsi_hi": "RSI-Schwelle Short",
    "dist_atr": "Mindestabstand zur EMA20 in ATR", "vol_high": "ATR-Verhältnis ab dem Breakout gehandelt wird",
    "vol_low": "ATR-Verhältnis bis zu dem gefadet wird", "slope_bars": "EMA50 muss über n Kerzen steigen/fallen",
    "touch_atr": "Toleranz der Level-Berührung in ATR", "pivot_k": "Pivot-Stärke (Kerzen je Seite, 15m)",
    "rsi_gap": "Mindest-RSI-Differenz der Divergenz",
    "chg_pct": "Mindest-Preisänderung in % über lookback Kerzen (Impuls-Filter, TF2)",
    "sl_lookback": "Struktur-SL: Tief/Hoch der letzten n 5m-Kerzen (TF2)",
}


def optional_params(setup: str) -> Dict[str, tuple]:
    """Optionale Schlüssel eines Setups (Default, Min, Max, Beschreibung)."""
    out = {k: v for k, v in COMMON_PARAMS.items() if not (k == "tp1_r" and setup in NO_TP1R)}
    out.update(SETUP_EXTRA_PARAMS.get(setup, {}))
    return out


def param_defaults(setup: str) -> Dict[str, float]:
    return {k: v[0] for k, v in optional_params(setup).items()}


def param_help(setup: str) -> Dict[str, str]:
    base = {k: BASE_PARAM_HELP.get(k, "") for v in VARIANTS.get(setup, []) for k in v if k != "name"}
    base.update({k: v[3] for k, v in optional_params(setup).items()})
    return base


def _filters_active(p: Dict) -> bool:
    return any(p.get(k) not in (None, d[0]) for k, d in COMMON_PARAMS.items()
               if k in ("sides", "vol_min", "vol_max", "hour_from", "hour_to", "htf_trend"))


def apply_filters(f: Features, sigs: List[Signal], p: Dict) -> List[Signal]:
    """Generische Signal-Filter (rein): Seite, Volatilitäts-Regime, Uhrzeit,
    1h-Trend. Ohne gesetzte Filter wird die Liste unverändert zurückgegeben."""
    if not sigs or not _filters_active(p):
        return sigs
    sides = int(p.get("sides") or 0)
    vmin, vmax = float(p.get("vol_min") or 0), float(p.get("vol_max") or 0)
    h_from, h_to = int(p.get("hour_from") or 0), int(p.get("hour_to") or 24)
    trend = int(p.get("htf_trend") or 0)
    bl = f.berlin() if (h_from, h_to) != (0, 24) else None
    out = []
    for s in sigs:
        d = 1 if s.side == "LONG" else -1
        if sides and d != sides:
            continue
        if vmin or vmax:
            slow = f.atr_slow[s.idx]
            ratio = f.atr_fast[s.idx] / slow if slow and not np.isnan(slow) and slow > 0 else None
            if ratio is None or (vmin and ratio < vmin) or (vmax and ratio > vmax):
                continue
        # Uhrzeit-Fenster (Audit 2.9): hour_from > hour_to = über Mitternacht
        # (z. B. 22–6 -> 22:00 bis 05:59 Berlin), sonst normales Fenster.
        if bl is not None:
            hr = bl[s.idx].hour
            ok_hour = (h_from <= hr < h_to) if h_from < h_to \
                else (hr >= h_from or hr < h_to)
            if not ok_hour:
                continue
        if trend:
            j = int(f.i60[s.idx])
            t60 = int(f.trend60[j]) if 0 <= j < len(f.trend60) else 0
            if t60 == 0 or t60 * d != trend:
                continue
        out.append(s)
    return out


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
