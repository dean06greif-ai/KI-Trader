"""Volatility Squeeze Breakout (1h): Trendfolge nach Volatilitäts-Kompression.

Idee: Krypto ist auf Stunden- bis Tagesebene trendlastig, Mean-Reversion-Scalps werden
von Gebühren aufgefressen. Diese Strategie handelt deshalb selten, aber mit großem
Gewinn/Verlust-Verhältnis:
  1. Squeeze: Bollinger-Bandbreite lag in den letzten `squeeze_recent` Kerzen im
     unteren `squeeze_pct` %-Perzentil der letzten `squeeze_lookback` Kerzen
  2. Ausbruch: Schluss über dem Donchian-Hoch (Long) / unter dem Donchian-Tief (Short)
     der vorherigen `dc_len` Kerzen
  3. Trendfilter: Schluss über EMA(`trend_ema`) und EMA steigend (Short gespiegelt)
  4. Volumen: Volumen > `vol_mult` × Ø-Volumen(20)  (0 = aus)
  5. Optional Crowding-Filter: kein Long, wenn Funding im oberen x-Perzentil der letzten
     90 Tage liegt (Short gespiegelt) – services.market_positioning (0 = aus)
Exits über die Trade-Einstellungen: 3×ATR-Stop, TP1 bei 1,5R (50 %), Rest mit ATR-Trailing,
Zeit-Exit (RECOMMENDED_TRADE_CFG). Live und Backtest nutzen dieselbe Funktion `_signals`.
"""
from typing import Dict, List, Optional

import numpy as np

from strategies.base_strategy import BaseStrategy


def _rolling(x: np.ndarray, n: int, fn) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if n < 1 or len(x) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(x, n)
    out[n - 1:] = fn(w, axis=1)
    return out


def _rolling_pct_rank(x: np.ndarray, n: int) -> np.ndarray:
    """Perzentil (0..1) jedes Werts in den letzten n Werten (inkl. sich selbst)."""
    out = np.full(len(x), np.nan)
    if len(x) < n:
        return out
    w = np.lib.stride_tricks.sliding_window_view(x, n)
    with np.errstate(invalid="ignore"):
        out[n - 1:] = (w <= w[:, -1:]).sum(axis=1) / n
    out[~np.isfinite(x)] = np.nan
    return out


def _signals(ts, op, hi, lo, cl, vol, p: Dict, symbol: Optional[str] = None,
             base_ms: int = 3_600_000, ensure: bool = False) -> Dict:
    from services import vec
    n = len(cl)
    bb_n = int(p["bb_len"])
    ma = _rolling(cl, bb_n, np.mean)
    sd = _rolling(cl, bb_n, np.std)
    with np.errstate(invalid="ignore", divide="ignore"):
        bw = 4 * sd / ma
    bw_pct = _rolling_pct_rank(bw, int(p["squeeze_lookback"])) * 100
    sq_now = bw_pct <= float(p["squeeze_pct"])
    rec = max(int(p["squeeze_recent"]), 1)
    squeeze = _rolling(sq_now.astype(float), rec, np.max) > 0
    dc = int(p["dc_len"])
    hh = np.full(n, np.nan)
    ll = np.full(n, np.nan)
    hh[1:] = _rolling(hi, dc, np.max)[:-1]          # vorherige dc Kerzen (ohne aktuelle)
    ll[1:] = _rolling(lo, dc, np.min)[:-1]
    ema = vec.ema(cl, int(p["trend_ema"]))
    sb = int(p["trend_slope_bars"])
    slope = np.full(n, np.nan)
    slope[sb:] = ema[sb:] - ema[:-sb]
    vma = _rolling(vol, 20, np.mean)
    vm = float(p["vol_mult"])
    with np.errstate(invalid="ignore"):
        vol_ok = np.ones(n, dtype=bool) if vm <= 0 else vol > vm * vma
        brk_l, brk_s = cl > hh, cl < ll
        if int(p["trend_filter"]) == 1:
            tr_l, tr_s = (cl > ema) & (slope > 0), (cl < ema) & (slope < 0)
        else:
            tr_l = tr_s = np.ones(n, dtype=bool)
    crowd_l = crowd_s = np.ones(n, dtype=bool)
    fund_pct = np.full(n, np.nan)
    cp = float(p.get("crowd_pct", 0) or 0)
    if cp > 0 and symbol:
        from services import market_positioning as mp
        if ensure:
            mp.ensure_for_candles(symbol, ts, 90.0, 7.0)
        pos = mp.setup_arrays(symbol, ts + int(base_ms), 90.0, 7.0)
        fund_pct = pos["funding_pct"] * 100
        with np.errstate(invalid="ignore"):
            crowd_l, crowd_s = ~(fund_pct >= 100 - cp), ~(fund_pct <= cp)
    valid = np.isfinite(hh) & np.isfinite(bw_pct) & np.isfinite(ema) & np.isfinite(slope)
    long_ok = valid & squeeze & brk_l & tr_l & vol_ok & crowd_l & (int(p["trade_long"]) == 1)
    short_ok = valid & squeeze & brk_s & tr_s & vol_ok & crowd_s & (int(p["trade_short"]) == 1)
    return {"long": long_ok, "short": short_ok, "squeeze": squeeze, "bw_pct": bw_pct,
            "brk_l": brk_l, "brk_s": brk_s, "tr_l": tr_l, "tr_s": tr_s, "vol_ok": vol_ok,
            "crowd_l": crowd_l, "crowd_s": crowd_s, "hh": hh, "ll": ll, "ema": ema,
            "fund_pct": fund_pct,
            "warmup": max(int(p["squeeze_lookback"]) + bb_n, int(p["trend_ema"]) + sb, dc + 1)}


class VolSqueezeBreakoutStrategy(BaseStrategy):
    STRATEGY_ID = "vol_squeeze_breakout"
    STRATEGY_NAME = "Volatility Squeeze Breakout (1h Trend)"
    STRATEGY_DESCRIPTION = ("Trendfolge nach Volatilitäts-Kompression: Bollinger-Squeeze, dann "
                            "Donchian-Ausbruch in Trendrichtung (EMA 200 steigend/fallend) mit "
                            "Volumen-Bestätigung. Wenige Trades, 3×ATR-Stop, TP1 1,5R + ATR-Trailing, "
                            "Zeit-Exit 72 h. Long und Short.")
    STRATEGY_TIMEFRAME = "1h"
    NEEDS_SYMBOL = True
    MIN_BUFFER_BARS = 400

    RECOMMENDED_TRADE_CFG = {
        "sl_mode": "atr", "atr_sl_multiplier": 3.0, "atr_period": 14,
        "tp_mode": "crv", "tp1_crv": 1.5, "tp_full_crv": 6.0, "tp1_close_percent": 50,
        "be_mode": "tp1", "trail_after_tp1": True, "trail_atr_mult": 2.5,
        "profit_secure_enabled": False, "max_hold_minutes": 4320,
        "entry_order_type": "market", "leverage": 3, "min_risk_percent": 0.3,
    }

    DEFAULT_PARAMS = {
        "bb_len": {"value": 20, "min": 10, "max": 40, "step": 2, "label": "Bollinger-Länge",
                   "description": "Kerzen für die Bandbreite"},
        "squeeze_lookback": {"value": 120, "min": 48, "max": 360, "step": 24, "label": "Squeeze-Lookback",
                             "description": "Bandbreiten-Perzentil gegen die letzten n Kerzen"},
        "squeeze_pct": {"value": 20, "min": 5, "max": 50, "step": 5, "label": "Squeeze-Perzentil %",
                        "description": "Bandbreite im unteren x % = Kompression"},
        "squeeze_recent": {"value": 6, "min": 1, "max": 24, "step": 1, "label": "Squeeze vor ≤ n Kerzen",
                           "description": "Die Kompression darf höchstens n Kerzen zurückliegen"},
        "dc_len": {"value": 20, "min": 10, "max": 60, "step": 5, "label": "Donchian-Länge",
                   "description": "Ausbruch über das Hoch/Tief der vorherigen n Kerzen"},
        "trend_filter": {"value": 1, "min": 0, "max": 1, "step": 1, "label": "Trendfilter",
                         "description": "1 = nur in Richtung EMA-Trend handeln"},
        "trend_ema": {"value": 200, "min": 50, "max": 400, "step": 25, "label": "Trend-EMA",
                      "description": "EMA-Länge für den Trendfilter"},
        "trend_slope_bars": {"value": 24, "min": 6, "max": 72, "step": 6, "label": "EMA-Steigung (Kerzen)",
                             "description": "EMA muss gegenüber vor n Kerzen steigen (Long) / fallen (Short)"},
        "vol_mult": {"value": 1.2, "min": 0.0, "max": 3.0, "step": 0.1, "label": "Volumen-Faktor",
                     "description": "Ausbruchs-Volumen > x × Ø20 (0 = aus)"},
        "crowd_pct": {"value": 0, "min": 0, "max": 30, "step": 5, "label": "Crowding-Filter %",
                      "description": "Kein Long wenn Funding im oberen x % (90 T), Short gespiegelt. 0 = aus"},
        "trade_long": {"value": 1, "min": 0, "max": 1, "step": 1, "label": "Long handeln",
                       "description": "1 = Long-Signale"},
        "trade_short": {"value": 1, "min": 0, "max": 1, "step": 1, "label": "Short handeln",
                        "description": "1 = Short-Signale"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        if len(candles) < 60:
            return None
        a = {k: np.array([c.get(k, 0) or 0 for c in candles], dtype=float)
             for k in ("open", "high", "low", "close", "volume")}
        ts = np.array([c["timestamp"] for c in candles], dtype=np.int64)
        s = _signals(ts, a["open"], a["high"], a["low"], a["close"], a["volume"], params, symbol,
                     int(np.median(np.diff(ts))) if len(ts) > 1 else 3_600_000)
        i = len(ts) - 1
        if i < s["warmup"]:
            return None

        def b(x):
            return bool(x[i])

        def f(x, d=4):
            return None if not np.isfinite(x) else round(float(x), d)
        rules = [
            {"id": "squeeze", "label": "Volatilitäts-Squeeze", "long": b(s["squeeze"]), "short": b(s["squeeze"]),
             "description": f"Bandbreite Perzentil {f(s['bw_pct'][i], 0)} % (Ziel ≤ {params['squeeze_pct']} % "
                            f"in den letzten {params['squeeze_recent']} Kerzen)"},
            {"id": "breakout", "label": "Donchian-Ausbruch", "long": b(s["brk_l"]), "short": b(s["brk_s"]),
             "description": f"Hoch {f(s['hh'][i], 6)} / Tief {f(s['ll'][i], 6)} ({params['dc_len']} Kerzen)"},
            {"id": "trend", "label": "Trend (EMA)", "long": b(s["tr_l"]), "short": b(s["tr_s"]),
             "description": f"EMA{params['trend_ema']} {f(s['ema'][i], 6)}"},
            {"id": "volume", "label": "Volumen", "long": b(s["vol_ok"]), "short": b(s["vol_ok"]),
             "description": f"> {params['vol_mult']} × Ø20"},
            {"id": "crowding", "label": "Crowding (Funding)", "long": b(s["crowd_l"]), "short": b(s["crowd_s"]),
             "description": "aus" if not params.get("crowd_pct") else
                            f"Funding-Perzentil {f(s['fund_pct'][i], 0)} %"},
        ]
        sig = "LONG" if b(s["long"]) else ("SHORT" if b(s["short"]) else None)
        levels = None
        if sig:
            from services import vec
            atr = float(vec.atr(a["high"], a["low"], a["close"], 14)[i])
            e = float(a["close"][i])
            r = 3.0 * atr if np.isfinite(atr) and atr > 0 else e * 0.01
            d = 1 if sig == "LONG" else -1
            levels = {"entry": round(e, 6), "stop_loss": round(e - d * r, 6),
                      "take_profit_1": round(e + d * 1.5 * r, 6),
                      "take_profit_full": round(e + d * 6.0 * r, 6), "crv": 1.5}
        return {"indicators": {"price": round(float(a["close"][i]), 6), "ema_fast": f(s["ema"][i], 6),
                               "ema_slow": f(s["ema"][i], 6), "rsi": None,
                               "bandwidth_pct": f(s["bw_pct"][i], 1)},
                "rules": rules, "bias": "LONG" if b(s["tr_l"]) else ("SHORT" if b(s["tr_s"]) else None),
                "long_count": sum(1 for r in rules if r["long"]),
                "short_count": sum(1 for r in rules if r["short"]),
                "rules_total": len(rules), "signal_type": sig, "is_pre_signal": False, "levels": levels}

    @staticmethod
    def vectorized_signals(fs, params: Dict, symbol: str = None) -> Optional[Dict]:
        s = _signals(fs.ts, fs.open, fs.high, fs.low, fs.close, fs.vol, params, symbol,
                     fs.base_tf_ms(), ensure=True)
        return {"long": s["long"], "short": s["short"], "warmup": s["warmup"], "rules_total": 5}
