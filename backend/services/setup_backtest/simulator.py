"""Konservative Trade-Simulation für Setup-Signale (rein & testbar).

Regeln:
  * Simulation ab der Kerze NACH dem Signal (kein Look-Ahead).
  * SL und TP in derselben Kerze -> SL zählt.
  * TP1: halbe Position, danach SL auf Einstieg (Break-Even).
  * Zeit-Exit nach max_bars Kerzen zum Schlusskurs.
  * PnL auf fixem Notional (MARGIN USDT), Gebühren je Klasse (Round-Trip).
  * Klassen-Grenzen für SL/TP (setup_asset_class.clamp_levels) werden VOR der
    Simulation erzwungen – wie im Live-Pfad.
"""
from typing import Dict, Optional

from services import setup_asset_class as ac
from services.candles import CandleArray
from services.setup_backtest.detectors import Signal

MARGIN = 100.0           # fixes Notional je Backtest-Trade (USDT)
MAX_BARS = 288           # 24 h auf 5m
TP1_CLOSE = 0.5

# Round-Trip-Gebühr/Spread in % des Notionals je Anlageklasse
FEES_RT_PCT: Dict[str, float] = {ac.CRYPTO: 0.06, ac.INDICES: 0.03,
                                 ac.RESOURCES: 0.03, ac.FOREX: 0.01}


def clamp_signal(asset_class: str, sig: Signal) -> Optional[Signal]:
    """SL/TP-Abstände in die Klassen-Grenzen zwingen (Preise neu ableiten)."""
    if sig.entry <= 0:
        return None
    d = 1 if sig.side == "LONG" else -1
    sl_pct = abs(sig.entry - sig.sl) / sig.entry * 100
    tp1_pct = max(0.0, d * (sig.tp1 - sig.entry)) / sig.entry * 100
    tpf_pct = max(0.0, d * (sig.tpf - sig.entry)) / sig.entry * 100
    if sl_pct <= 0 or tpf_pct <= 0:
        return None
    lv = ac.clamp_levels(asset_class, sl_pct, tp1_pct, tpf_pct)
    if lv["tpf_pct"] <= 0:
        return None
    tp1_pct = lv["tp1_pct"] if lv["tp1_pct"] > 0 else lv["tpf_pct"] / 2
    return Signal(sig.idx, sig.side, sig.entry,
                  sig.entry * (1 - d * lv["sl_pct"] / 100),
                  sig.entry * (1 + d * min(tp1_pct, lv["tpf_pct"]) / 100),
                  sig.entry * (1 + d * lv["tpf_pct"] / 100), sig.note)


def simulate(c: CandleArray, sig: Signal, fee_rt_pct: float,
             max_bars: int = MAX_BARS) -> Optional[Dict]:
    start, end = sig.idx + 1, min(len(c), sig.idx + 1 + max_bars)
    if start >= len(c) or sig.entry <= 0:
        return None
    d = 1 if sig.side == "LONG" else -1
    entry, sl, tp1, tpf = sig.entry, sig.sl, sig.tp1, sig.tpf
    open_part, realized, tp1_done = 1.0, 0.0, False
    peak, trough = entry, entry
    exit_price, exit_idx, reason = None, end - 1, "time"

    def hit_sl(i) -> bool:
        return c.lo[i] <= sl if d == 1 else c.hi[i] >= sl

    def hit(level, i) -> bool:
        return c.hi[i] >= level if d == 1 else c.lo[i] <= level

    for i in range(start, end):
        peak, trough = max(peak, float(c.hi[i])), min(trough, float(c.lo[i]))
        if hit_sl(i):
            realized += open_part * d * (sl - entry) / entry
            exit_price, exit_idx, reason = sl, i, ("be" if tp1_done else "sl")
            open_part = 0.0
            break
        if not tp1_done and hit(tp1, i):
            realized += TP1_CLOSE * d * (tp1 - entry) / entry
            open_part -= TP1_CLOSE
            tp1_done, sl = True, entry
            continue  # konservativ: TPf frühestens in der Folgekerze
        if hit(tpf, i):
            realized += open_part * d * (tpf - entry) / entry
            exit_price, exit_idx, reason = tpf, i, "tp"
            open_part = 0.0
            break
    if open_part > 0:
        exit_price = float(c.cl[exit_idx])
        realized += open_part * d * (exit_price - entry) / entry
    pnl = realized * MARGIN - fee_rt_pct / 100 * MARGIN
    return {"exit_idx": exit_idx, "exit_price": float(exit_price), "reason": reason,
            "pnl": round(pnl, 4), "result": "win" if pnl > 0 else "loss",
            "peak_price": peak, "trough_price": trough, "tp1_done": tp1_done,
            "sl": float(sl), "initial_sl": float(sig.sl), "tp1": float(tp1), "tpf": float(tpf)}
