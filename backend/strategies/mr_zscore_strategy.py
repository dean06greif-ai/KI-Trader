"""Z-Score Mean Reversion PRO – statistische Rueckkehr zum Mittelwert.

Kernidee: Der Abstand des Kurses zu seinem gleitenden Mittel, normiert auf die
Standardabweichung (Z-Score), ist stark mean-reverting. Gehandelt wird NUR das
echte statistische Extrem (|z| >= Schwelle) UND erst nach Rueckkehr-
Bestaetigung – nie ins fallende Messer.

Regeln (alle optimierbar im Strategie-Optimierer):
  * |Z-Score| >= Schwelle (Default 2.2)            -> statistisches Extrem
  * RSI(14) < 35 (Long) / > 65 (Short)             -> Momentum-Erschoepfung
  * Reversal-Kerze (Close dreht Richtung Mittel)   -> Bestaetigung
  * Trend-Sperre (EMA-Regime + Steigung)           -> nie gegen starke Impulse
  * Erschoepfungs-Docht (Wick-Rejection, optional) -> Kaeufer/Verkaeufer geben auf

Stops: hinter dem Extrem (Swing) + ATR-Puffer. TP1 = Rueckkehr zum Mittelwert
(SMA-Basis), TP voll = R-Vielfaches. Timeframe 15m: Mean Reversion hat auf
hoeheren TFs mehr Edge (weniger Noise & Gebuehren-Anteil). Universell fuer
alle Assets der Plattform (Krypto, Rohstoffe, Indizes, Forex).
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class ZScoreReversionStrategy(BaseStrategy):
    STRATEGY_ID = "mr_zscore"
    STRATEGY_NAME = "Z-Score Mean Reversion PRO"
    STRATEGY_DESCRIPTION = ("Statistisches Extrem (Z-Score >= 2.2 Std-Abw.) + RSI-Erschoepfung "
                            "+ Reversal-Bestaetigung + Trend-Sperre. Handelt die Rueckkehr zum "
                            "Mittelwert – universell fuer Krypto, Forex, Indizes & Rohstoffe.")
    STRATEGY_TIMEFRAME = "15m"

    DEFAULT_PARAMS = {
        "mean_period": {"value": 40, "min": 15, "max": 120, "step": 5,
                        "label": "Mittelwert-Periode", "description": "SMA-Periode fuer Basis & Z-Score"},
        "z_entry": {"value": 2.2, "min": 1.4, "max": 3.5, "step": 0.1,
                    "label": "Z-Score Schwelle", "description": "Extrem ab |z| >= Schwelle (Std-Abweichungen)"},
        "rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "RSI Periode", "description": "RSI fuer die Erschoepfungs-Bestaetigung"},
        "rsi_long_max": {"value": 35, "min": 10, "max": 45, "step": 1,
                         "label": "RSI LONG max", "description": "RSI muss fuer Long darunter liegen"},
        "rsi_short_min": {"value": 65, "min": 55, "max": 90, "step": 1,
                          "label": "RSI SHORT min", "description": "RSI muss fuer Short darueber liegen"},
        "require_reversal": {"value": 1, "min": 0, "max": 1, "step": 1,
                             "label": "Reversal-Kerze noetig", "description": "1 = Close muss zurueckdrehen"},
        "wick_filter": {"value": 1, "min": 0, "max": 1, "step": 1,
                        "label": "Erschoepfungs-Docht", "description": "1 = Ablehnungs-Docht am Extrem verlangen"},
        "wick_min_ratio": {"value": 0.35, "min": 0.1, "max": 0.8, "step": 0.05,
                           "label": "Min. Docht-Anteil", "description": "Docht-Anteil an der Kerzen-Range"},
        "ema_trend_period": {"value": 100, "min": 30, "max": 300, "step": 10,
                             "label": "Trend-EMA", "description": "EMA fuer die Trend-Sperre"},
        "trend_block_pct": {"value": 0.25, "min": 0.05, "max": 1.5, "step": 0.05,
                            "label": "Trend-Sperre %", "description": "EMA-Steigung (ueber 10 Kerzen) ab der Gegen-Trades blockiert werden"},
        "block_counter_trend": {"value": 1, "min": 0, "max": 1, "step": 1,
                                "label": "Gegen-Trend sperren", "description": "1 = nicht in starke Impulse reversen"},
        "atr_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "ATR Periode", "description": "Volatilitaet fuer Stop-Puffer"},
        "atr_sl_mult": {"value": 1.4, "min": 0.3, "max": 3.5, "step": 0.1,
                        "label": "ATR SL Puffer", "description": "ATR-Puffer hinter dem Extrem (Anti Stop-Hunt)"},
        "sl_lookback": {"value": 12, "min": 3, "max": 40, "step": 1,
                        "label": "Struktur Lookback", "description": "Kerzen fuer Swing-Tief/-Hoch"},
        "tp_rr": {"value": 2.0, "min": 1.0, "max": 8.0, "step": 0.1,
                  "label": "TP voll (R-Vielfaches)", "description": "Endziel als Vielfaches des Risikos"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        mean_p = int(params["mean_period"])
        rsi_p = int(params["rsi_period"])
        ema_p = int(params["ema_trend_period"])
        atr_p = int(params["atr_period"])
        sl_lb = int(params["sl_lookback"])
        need = max(mean_p, ema_p, atr_p, sl_lb, 20) + 12
        if len(candles) < need:
            return None

        ti = self.indicators
        closes = [c["close"] for c in candles]
        price = closes[-1]
        last, prev = candles[-1], candles[-2]

        window = closes[-mean_p:]
        mean = sum(window) / mean_p
        variance = sum((x - mean) ** 2 for x in window) / mean_p
        std = variance ** 0.5
        if std <= 0 or price <= 0:
            return None
        z = (price - mean) / std
        z_thr = float(params["z_entry"])

        rsi = self._last(ti.calculate_rsi(closes, rsi_p))
        ema_arr = ti.calculate_ema(closes, ema_p)
        ema_t = self._last(ema_arr)
        atr = self._last(ti.calculate_atr(candles, atr_p)) or 0.0
        if rsi is None or ema_t is None:
            return None
        prev_e = ema_arr[-11] if len(ema_arr) >= 11 and ema_arr[-11] else None
        slope = (ema_t - prev_e) / prev_e if prev_e else 0.0

        # Trend-Sperre: nie gegen einen starken Impuls reversen
        thr = float(params["trend_block_pct"]) / 100.0
        strong_bull = price > ema_t and slope > thr
        strong_bear = price < ema_t and slope < -thr
        block = int(params["block_counter_trend"]) == 1
        trend_ok_long = (not strong_bear) if block else True
        trend_ok_short = (not strong_bull) if block else True

        z_long = z <= -z_thr
        z_short = z >= z_thr
        rsi_long = rsi < float(params["rsi_long_max"])
        rsi_short = rsi > float(params["rsi_short_min"])

        rev_long = last["close"] > prev["close"]
        rev_short = last["close"] < prev["close"]
        if int(params["require_reversal"]) != 1:
            rev_long = rev_short = True

        # Erschoepfungs-Docht: Ablehnung des Extrems in der aktuellen Kerze
        rng = max(last["high"] - last["low"], 1e-12)
        lower_wick = (min(last["open"], last["close"]) - last["low"]) / rng
        upper_wick = (last["high"] - max(last["open"], last["close"])) / rng
        wick_min = float(params["wick_min_ratio"])
        wick_long = lower_wick >= wick_min
        wick_short = upper_wick >= wick_min
        if int(params["wick_filter"]) != 1:
            wick_long = wick_short = True

        signal_long = z_long and rsi_long and rev_long and trend_ok_long and wick_long
        signal_short = z_short and rsi_short and rev_short and trend_ok_short and wick_short
        signal_type = "LONG" if signal_long else ("SHORT" if signal_short else None)
        bias = "LONG" if (z_long and rsi_long) else ("SHORT" if (z_short and rsi_short) else None)

        rules = [
            {"id": "z_extreme", "label": "Z-Score Extrem",
             "description": f"|z| >= {z_thr} Standardabweichungen vom Mittelwert",
             "long": bool(z_long), "short": bool(z_short)},
            {"id": "rsi_exhaust", "label": "RSI-Erschoepfung",
             "description": f"RSI < {params['rsi_long_max']} (Long) / > {params['rsi_short_min']} (Short)",
             "long": bool(rsi_long), "short": bool(rsi_short)},
            {"id": "reversal", "label": "Reversal-Kerze",
             "description": "Close dreht zurueck Richtung Mittelwert",
             "long": bool(rev_long), "short": bool(rev_short)},
            {"id": "wick_reject", "label": "Erschoepfungs-Docht",
             "description": "Ablehnungs-Docht am Extrem (Kaeufer/Verkaeufer geben auf)",
             "long": bool(wick_long), "short": bool(wick_short)},
            {"id": "trend_filter", "label": "Trend-Sperre",
             "description": "Keine Reversion gegen starke Impulse",
             "long": bool(trend_ok_long), "short": bool(trend_ok_short)},
        ]

        levels = None
        atr_buf = atr * float(params["atr_sl_mult"])
        tp_rr = float(params["tp_rr"])
        if signal_type == "LONG":
            struct = ti.get_recent_low(candles, sl_lb)
            struct = min(struct, last["low"]) if struct is not None else last["low"]
            sl = struct - atr_buf
            risk = price - sl
            if risk <= 0:
                risk = price * 0.003
                sl = price - risk
            tp1 = max(mean, price + risk * 0.8)   # TP1 = Rueckkehr zum Mittelwert
            levels = self._lv(price, sl, tp1, price + risk * tp_rr)
        elif signal_type == "SHORT":
            struct = ti.get_recent_high(candles, sl_lb)
            struct = max(struct, last["high"]) if struct is not None else last["high"]
            sl = struct + atr_buf
            risk = sl - price
            if risk <= 0:
                risk = price * 0.003
                sl = price + risk
            tp1 = min(mean, price - risk * 0.8)
            levels = self._lv(price, sl, tp1, price - risk * tp_rr)

        return {
            "indicators": {"z_score": round(z, 2), "mean": round(mean, 6),
                           "std": round(std, 6), "rsi": round(rsi, 2),
                           "ema_trend": round(ema_t, 6), "price": round(price, 6),
                           "atr": round(atr, 6), "slope_pct": round(slope * 100, 3)},
            "rules": rules, "bias": bias,
            "long_count": sum(1 for r in rules if r["long"]),
            "short_count": sum(1 for r in rules if r["short"]),
            "rules_total": len(rules),
            "signal_type": signal_type, "is_pre_signal": False, "levels": levels,
        }

    @staticmethod
    def _last(arr):
        return arr[-1] if arr else None

    def _lv(self, entry, sl, tp1, tpf):
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
