"""TradeX VWAP Scalping (Horst v2): Mean-Reversion am Tages-VWAP mit Positionierungs-Filter.

Basis: Horst VWAP + OBV-RSI (horst_vwap_obv). Änderungen nach TradeX/Tim:
  1. Nur Long (Short optional spiegelbar: trade_short=1)
  2. 1m-Kerze SCHLIESST unter dem unteren VWAP-Deviation-Band (Tages-VWAP, Reset 00:00 UTC,
     volumengewichtete Standardabweichung wie TradingView-VWAP-Bänder)
  3. RSI auf dem On-Balance-Volume <= 30 (überverkauft)
  4. Setup-Filter: Funding im unteren Perzentil der letzten 90 Tage ODER Long/Short-Ratio
     (Konten) im unteren Perzentil der letzten Tage (= Masse short, Shorts zahlen)
  5. Limit-Order statt Market (Maker-Fee), nur 1 aktives Setup
  6. TP 0,6 % · Notfall-SL 3,5 % · Zeit-Exit nach 6 h (Trade-Einstellungen, siehe
     RECOMMENDED_TRADE_CFG)

Mikro-Anpassungen nach eigenem Backtest (BTCUSDT 1m, 01/2022–08/2026, echte Fees):
  * Band 2,5σ statt 2,0σ (weniger fallende Messer, PF 0,92 -> 1,07)
  * TP als Limit-Order (Maker-Fee) und TP 0,8 % statt 0,6 % (PF -> 1,16)
  Original-Werte bleiben per Parameter einstellbar (band_mult=2.0, TP 0.6).

Live und Backtest nutzen dieselben reinen numpy-Funktionen (vwap_bands, obv_rsi,
_signals) -> identische Signale. Funding/L/S kommen aus services.market_positioning.
"""
from typing import Dict, List, Optional

import numpy as np

from strategies.base_strategy import BaseStrategy

DAY_MS = 86_400_000


def vwap_bands(ts, high, low, close, vol, mult: float, session: bool = True,
               window: int = 0, min_session_bars: int = 0):
    """VWAP + Deviation-Bänder. session=True: Reset 00:00 UTC (erster, angeschnittener
    Tag = NaN). Sonst rollierend über `window` Kerzen. Bänder = VWAP ± mult·σ(vol-gewichtet)."""
    n = len(close)
    tp = (high + low + close) / 3.0
    v = np.where(np.isfinite(vol) & (vol > 0), vol, 0.0)
    c1 = np.concatenate([[0.0], np.cumsum(tp * v)])
    c2 = np.concatenate([[0.0], np.cumsum(tp * tp * v)])
    cv = np.concatenate([[0.0], np.cumsum(v)])
    idx = np.arange(n)
    bars_in = np.zeros(n)
    if session:
        day = ts // DAY_MS
        new = np.concatenate([[True], day[1:] != day[:-1]])
        start = np.maximum.accumulate(np.where(new, idx, 0))
        bars_in = idx - start + 1
    else:
        start = np.maximum(idx - max(int(window), 2) + 1, 0)
    sv = cv[idx + 1] - cv[start]
    with np.errstate(invalid="ignore", divide="ignore"):
        vw = (c1[idx + 1] - c1[start]) / sv
        var = (c2[idx + 1] - c2[start]) / sv - vw * vw
    sd = np.sqrt(np.clip(var, 0.0, None))
    bad = ~(sv > 0)
    if session:
        first_day = ts // DAY_MS == ts[0] // DAY_MS
        partial = first_day & ((ts[0] % DAY_MS) != 0)
        bad |= partial | (bars_in < max(int(min_session_bars), 1))
    else:
        bad |= idx < int(window) - 1
    vw = np.where(bad, np.nan, vw)
    sd = np.where(bad, np.nan, sd)
    return vw, vw - mult * sd, vw + mult * sd


def obv_rsi(close, vol, period: int):
    from services import vec
    diff = np.diff(close, prepend=close[:1])
    obv = np.cumsum(np.sign(diff) * np.where(np.isfinite(vol), vol, 0.0))
    return vec.rsi(obv, int(period))


def _signals(symbol: str, ts, high, low, close, vol, p: Dict, base_ms: int = 60000) -> Dict:
    from services import market_positioning as mp
    vw, lower, upper = vwap_bands(ts, high, low, close, vol, float(p["band_mult"]),
                                  session=int(p["vwap_session"]) == 1,
                                  window=int(p["band_window"]),
                                  min_session_bars=int(p["session_min_bars"]))
    rsi = obv_rsi(close, vol, int(p["obv_rsi_period"]))
    ts_close = ts + int(base_ms)          # Wert muss zum Kerzenschluss bekannt sein
    pos = mp.setup_arrays(symbol, ts_close, float(p["funding_lookback_days"]),
                          float(p["ls_lookback_days"]))
    f_pct, l_pct = pos["funding_pct"] * 100, pos["ls_pct"] * 100
    fp, lp = float(p["funding_pct_max"]), float(p["ls_pct_max"])
    with np.errstate(invalid="ignore"):
        f_long, f_short = f_pct <= fp, f_pct >= 100 - fp
        l_long, l_short = l_pct <= lp, l_pct >= 100 - lp
        mode = int(p["setup_mode"])
        if mode == 0:
            set_l = set_s = np.ones(len(close), dtype=bool)
        elif mode == 2:
            set_l, set_s = f_long & l_long, f_short & l_short
        else:
            set_l, set_s = f_long | l_long, f_short | l_short
        zone_l, zone_s = close < lower, close > upper
        rsi_l = rsi <= float(p["obv_rsi_long_max"])
        rsi_s = rsi >= float(p["obv_rsi_short_min"])
    # Crash-Guard: kein Long nach > x % Abverkauf in 24 h (kein Short nach Pump) –
    # dort greift sonst der 3,5-%-Notfall-SL (fallendes Messer statt Rücklauf)
    cg = float(p.get("crash_guard_pct", 0) or 0)
    guard_l = guard_s = np.ones(len(close), dtype=bool)
    if cg > 0:
        lag = max(int(DAY_MS // max(int(base_ms), 1)), 1)
        chg = np.full(len(close), np.nan)
        chg[lag:] = (close[lag:] / close[:-lag] - 1) * 100
        with np.errstate(invalid="ignore"):
            guard_l, guard_s = ~(chg < -cg), ~(chg > cg)
    valid = np.isfinite(vw) & np.isfinite(rsi)
    long_ok = valid & zone_l & rsi_l & set_l & guard_l & (int(p["trade_long"]) == 1)
    short_ok = valid & zone_s & rsi_s & set_s & guard_s & (int(p["trade_short"]) == 1)
    off = float(p["limit_offset_pct"]) / 100.0
    return {"vwap": vw, "lower": lower, "upper": upper, "rsi": rsi,
            "long": long_ok, "short": short_ok,
            "limit_long": close * (1 - off), "limit_short": close * (1 + off),
            "f_long": f_long, "f_short": f_short, "l_long": l_long, "l_short": l_short,
            "set_l": set_l, "set_s": set_s, "zone_l": zone_l, "zone_s": zone_s,
            "rsi_l": rsi_l, "rsi_s": rsi_s, "guard_l": guard_l, "guard_s": guard_s, "pos": pos}


class TradeXVWAPScalpingStrategy(BaseStrategy):
    STRATEGY_ID = "tradex_vwap_scalping"
    STRATEGY_NAME = "TradeX VWAP Scalping (Horst v2)"
    STRATEGY_DESCRIPTION = ("Verbesserte Horst-Strategie (TradeX): 1m-Schluss unter dem unteren "
                            "Tages-VWAP-Band + OBV-RSI ≤ 30 + Masse short (Funding niedrig ODER "
                            "L/S-Ratio niedrig). Nur Long, Limit-Entry + Limit-TP (Maker), TP 0,8 %, "
                            "Notfall-SL 3,5 %, Zeit-Exit 6 h, Band 2,5σ. Short spiegelbar (trade_short=1).")
    STRATEGY_TIMEFRAME = "1m"
    NEEDS_SYMBOL = True
    REQUIRES_POSITIONING = True
    MIN_BUFFER_BARS = 1600               # Tages-VWAP braucht die Kerzen seit 00:00 UTC

    RECOMMENDED_TRADE_CFG = {
        "tp_mode": "fixed_pct", "tp1_percent": 0.8, "tp_full_percent": 0.8, "tp_order_type": "limit",
        "tp1_close_percent": 100, "sl_mode": "fixed", "sl_fixed_percent": 3.5,
        "be_mode": "off", "trail_after_tp1": False, "profit_secure_enabled": False,
        "max_hold_minutes": 360, "entry_order_type": "limit", "limit_expiry_bars": 5,
        "maker_fee_percent": 0.02, "leverage": 5, "min_risk_percent": 0.1,
    }

    DEFAULT_PARAMS = {
        "vwap_session": {"value": 1, "min": 0, "max": 1, "step": 1, "label": "Tages-VWAP (1) / rollierend (0)",
                         "description": "1 = VWAP mit Reset 00:00 UTC (TradeX), 0 = rollierendes Fenster"},
        "band_window": {"value": 240, "min": 60, "max": 1440, "step": 60, "label": "Rollier-Fenster",
                        "description": "Nur bei vwap_session=0: Kerzen für VWAP/σ"},
        "band_mult": {"value": 2.5, "min": 1.0, "max": 3.5, "step": 0.1, "label": "Band-Multiplikator",
                      "description": "σ-Vielfaches des Signalbands (TradeX-Original 2.0; Backtest 2022-26: 2.5 robuster)"},
        "session_min_bars": {"value": 30, "min": 0, "max": 180, "step": 10, "label": "Mindest-Kerzen je Tag",
                             "description": "Keine Signale in den ersten n Minuten nach 00:00 UTC (σ noch instabil)"},
        "obv_rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1, "label": "OBV-RSI Periode",
                           "description": "RSI auf dem On-Balance-Volume"},
        "obv_rsi_long_max": {"value": 30, "min": 10, "max": 45, "step": 1, "label": "OBV-RSI Long Max",
                             "description": "Überverkauft-Schwelle (Long)"},
        "obv_rsi_short_min": {"value": 70, "min": 55, "max": 90, "step": 1, "label": "OBV-RSI Short Min",
                              "description": "Überkauft-Schwelle (Short)"},
        "setup_mode": {"value": 1, "min": 0, "max": 2, "step": 1, "label": "Setup-Filter",
                       "description": "0 = aus (Horst-Original), 1 = Funding ODER L/S (TradeX), 2 = beide"},
        "funding_lookback_days": {"value": 90, "min": 14, "max": 180, "step": 7, "label": "Funding-Lookback (Tage)",
                                  "description": "Perzentil der Funding-Rate gegen die letzten n Tage"},
        "funding_pct_max": {"value": 20, "min": 5, "max": 50, "step": 5, "label": "Funding-Perzentil max %",
                            "description": "Long nur, wenn Funding im unteren x % liegt (Short: oberen x %)"},
        "ls_lookback_days": {"value": 7, "min": 1, "max": 30, "step": 1, "label": "L/S-Lookback (Tage)",
                             "description": "Perzentil der Long/Short-Ratio (Konten) gegen die letzten n Tage"},
        "ls_pct_max": {"value": 20, "min": 5, "max": 50, "step": 5, "label": "L/S-Perzentil max %",
                       "description": "Long nur, wenn die Masse ungewöhnlich short ist (unteres x %)"},
        "trade_long": {"value": 1, "min": 0, "max": 1, "step": 1, "label": "Long handeln",
                       "description": "1 = Long-Signale erzeugen"},
        "trade_short": {"value": 0, "min": 0, "max": 1, "step": 1, "label": "Short handeln",
                        "description": "1 = gespiegelte Short-Signale (TradeX: aus)"},
        "crash_guard_pct": {"value": 0.0, "min": 0.0, "max": 10.0, "step": 0.5, "label": "Crash-Guard 24h %",
                            "description": "Kein Long nach > x % Minus in 24 h (Short: Plus). 0 = aus"},
        "limit_offset_pct": {"value": 0.0, "min": 0.0, "max": 0.3, "step": 0.02, "label": "Limit-Abstand %",
                             "description": "Limit-Preis = Schlusskurs − x % (Long) / + x % (Short)"},
    }

    def _prepare(self, symbol: str, ts, hi, lo, cl, vol, params: Dict, base_ms: int):
        from services import market_positioning as mp
        mp.ensure_for_candles(symbol, ts, float(params["funding_lookback_days"]),
                              float(params["ls_lookback_days"]))
        return _signals(symbol, ts, hi, lo, cl, vol, params, base_ms)

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        if len(candles) < 60:
            return None
        ts = np.array([c["timestamp"] for c in candles], dtype=np.int64)
        hi = np.array([c["high"] for c in candles], dtype=float)
        lo = np.array([c["low"] for c in candles], dtype=float)
        cl = np.array([c["close"] for c in candles], dtype=float)
        vol = np.array([c.get("volume", 0) or 0 for c in candles], dtype=float)
        base_ms = int(np.min(np.diff(ts)[np.diff(ts) > 0])) if len(ts) > 1 else 60000
        s = _signals(symbol, ts, hi, lo, cl, vol, params, base_ms)   # Live: nur Cache lesen
        i = len(cl) - 1
        price, vw = float(cl[i]), s["vwap"][i]
        if not np.isfinite(vw):
            return None
        pos = s["pos"]

        def b(a):
            return bool(a[i])

        def fmt(x, d=4):
            return None if not np.isfinite(x) else round(float(x), d)
        rules = [
            {"id": "funding", "label": "Funding-Rate", "long": b(s["f_long"]), "short": b(s["f_short"]),
             "description": f"Perzentil {fmt(pos['funding_pct'][i] * 100, 0)} % der letzten "
                            f"{params['funding_lookback_days']} T (Ziel ≤ {params['funding_pct_max']} %)"},
            {"id": "long_short", "label": "Long/Short-Ratio", "long": b(s["l_long"]), "short": b(s["l_short"]),
             "description": f"L/S {fmt(pos['ls'][i], 3)} · Perzentil {fmt(pos['ls_pct'][i] * 100, 0)} % der "
                            f"letzten {params['ls_lookback_days']} T (Ziel ≤ {params['ls_pct_max']} %)"},
            {"id": "setup", "label": "Setup (Funding/L/S)", "long": b(s["set_l"]), "short": b(s["set_s"]),
             "description": ["aus", "Funding ODER L/S", "Funding UND L/S"][int(params["setup_mode"])]},
            {"id": "vwap_zone", "label": "Kerzenschluss unter Band", "long": b(s["zone_l"]),
             "short": b(s["zone_s"]), "description": "1m-Schluss unter dem unteren (Long) / über dem "
                                                     "oberen (Short) VWAP-Band"},
            {"id": "obv_rsi", "label": "RSI (OBV)", "long": b(s["rsi_l"]), "short": b(s["rsi_s"]),
             "description": f"OBV-RSI {fmt(s['rsi'][i], 1)} (Long ≤ {params['obv_rsi_long_max']} / "
                            f"Short ≥ {params['obv_rsi_short_min']})"},
        ]
        # "setup" fasst Funding/L/S zusammen -> nur die harten Regeln zählen
        hard = [r for r in rules if r["id"] not in ("funding", "long_short")]
        signal_type = "LONG" if b(s["long"]) else ("SHORT" if b(s["short"]) else None)
        levels = None
        if signal_type:
            limit = float(s["limit_long"][i] if signal_type == "LONG" else s["limit_short"][i])
            levels = {"entry": round(limit, 6), "limit_price": round(limit, 6),
                      "stop_loss": round(limit * (0.965 if signal_type == "LONG" else 1.035), 6),
                      "take_profit_1": round(limit * (1.006 if signal_type == "LONG" else 0.994), 6),
                      "take_profit_full": round(limit * (1.006 if signal_type == "LONG" else 0.994), 6),
                      "crv": round(0.6 / 3.5, 2)}
        return {
            "indicators": {"rsi": fmt(s["rsi"][i], 2), "obv_rsi": fmt(s["rsi"][i], 2),
                           "ema_fast": fmt(vw, 6), "ema_slow": fmt(vw, 6), "price": round(price, 6),
                           "vwap": fmt(vw, 6), "vwap_lower": fmt(s["lower"][i], 6),
                           "vwap_upper": fmt(s["upper"][i], 6),
                           "funding": fmt(pos["funding"][i], 6), "funding_pct": fmt(pos["funding_pct"][i] * 100, 1),
                           "ls_ratio": fmt(pos["ls"][i], 4), "ls_pct": fmt(pos["ls_pct"][i] * 100, 1),
                           "positioning_source": f"{pos['funding_source']}/{pos['ls_source']}"},
            "rules": rules, "bias": "LONG" if b(s["set_l"]) else ("SHORT" if b(s["set_s"]) else None),
            "long_count": sum(1 for r in hard if r["long"]),
            "short_count": sum(1 for r in hard if r["short"]),
            "rules_total": len(hard), "signal_type": signal_type, "is_pre_signal": False,
            "levels": levels,
        }

    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        sig = super().check_signal(candles, symbol, settings)
        if sig:
            sig["limit_price"] = sig.get("entry_price")
        return sig

    @staticmethod
    def vectorized_signals(fs, params: Dict, symbol: str = None) -> Optional[Dict]:
        if not symbol:
            return None
        base_ms = fs.base_tf_ms()
        s = TradeXVWAPScalpingStrategy()._prepare(symbol, fs.ts, fs.high, fs.low, fs.close,
                                                  fs.vol, params, base_ms)
        return {"long": s["long"], "short": s["short"], "warmup": 60, "rules_total": 3,
                "rsi": s["rsi"], "entry_long": s["limit_long"], "entry_short": s["limit_short"]}
