"""Keltner-Channel Fade – Mean Reversion mit Stochastik-Erschoepfung.

Kernidee: Der Keltner-Channel (EMA-Basis ± ATR-Vielfaches) passt sich der
Volatilitaet an – ein Close AUSSERHALB des Kanals ist ein volatilitaets-
adjustiertes Extrem (robuster als starre %-Baender). Gefadet wird das Extrem
nur mit doppelter Erschoepfungs-Bestaetigung:

  * Close ausserhalb des Keltner-Channels (ATR-Extrem)
  * Stochastik-Extrem MIT Drehung (K kreuzt zurueck ueber/unter D)
  * Reversal-Kerze (Close dreht Richtung Kanal-Mitte)
  * Volumen-Klimax optional (rel. Volumen) – Kapitulation statt Trendstart
  * Trend-Sperre: nie gegen starke Impulse (EMA-Regime + Steigung)

Stops hinter dem Extrem + ATR-Puffer, TP1 = Kanal-Mitte (EMA-Basis),
TP voll = R-Vielfaches. Timeframe 15m, universell fuer alle Assets.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class KeltnerFadeStrategy(BaseStrategy):
    STRATEGY_ID = "mr_keltner_fade"
    STRATEGY_NAME = "Keltner-Channel Fade (Stoch-Erschoepfung)"
    STRATEGY_DESCRIPTION = ("Volatilitaets-adjustiertes Extrem (Close ausserhalb Keltner-Channel) "
                            "+ Stochastik-Drehung + Reversal-Kerze. Fadet Uebertreibungen zurueck "
                            "zur Kanal-Mitte – universell fuer alle Anlageklassen.")
    STRATEGY_TIMEFRAME = "15m"

    DEFAULT_PARAMS = {
        "kc_period": {"value": 20, "min": 10, "max": 60, "step": 1,
                      "label": "Keltner Periode", "description": "EMA-Periode der Kanal-Basis"},
        "kc_mult": {"value": 2.3, "min": 1.2, "max": 4.0, "step": 0.1,
                    "label": "Keltner ATR-Mult", "description": "Kanal-Breite in ATR-Vielfachen"},
        "stoch_k": {"value": 14, "min": 5, "max": 30, "step": 1,
                    "label": "Stochastik %K", "description": "K-Periode der Stochastik"},
        "stoch_d": {"value": 3, "min": 2, "max": 9, "step": 1,
                    "label": "Stochastik %D", "description": "Glaettung der Stochastik"},
        "stoch_low": {"value": 22, "min": 5, "max": 40, "step": 1,
                      "label": "Stoch Oversold", "description": "Extrem-Zone fuer Longs"},
        "stoch_high": {"value": 78, "min": 60, "max": 95, "step": 1,
                       "label": "Stoch Overbought", "description": "Extrem-Zone fuer Shorts"},
        "require_cross": {"value": 1, "min": 0, "max": 1, "step": 1,
                          "label": "K/D-Drehung noetig", "description": "1 = %K muss %D zurueckkreuzen"},
        "require_reversal": {"value": 1, "min": 0, "max": 1, "step": 1,
                             "label": "Reversal-Kerze noetig", "description": "1 = Close dreht Richtung Kanal-Mitte"},
        "vol_climax_min": {"value": 1.2, "min": 0.5, "max": 4.0, "step": 0.1,
                           "label": "Min. Rel. Volumen", "description": "Kapitulations-Volumen ggue. Durchschnitt"},
        "use_vol_filter": {"value": 1, "min": 0, "max": 1, "step": 1,
                           "label": "Volumen-Filter", "description": "1 = Volumen-Klimax verlangen"},
        "ema_trend_period": {"value": 80, "min": 30, "max": 300, "step": 10,
                             "label": "Trend-EMA", "description": "EMA fuer die Trend-Sperre"},
        "trend_block_pct": {"value": 0.25, "min": 0.05, "max": 1.5, "step": 0.05,
                            "label": "Trend-Sperre %", "description": "EMA-Steigung ab der Gegen-Trades blockiert werden"},
        "block_counter_trend": {"value": 1, "min": 0, "max": 1, "step": 1,
                                "label": "Gegen-Trend sperren", "description": "1 = nicht in starke Impulse reversen"},
        "atr_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "ATR Periode", "description": "Volatilitaet fuer Kanal & Stop-Puffer"},
        "atr_sl_mult": {"value": 1.3, "min": 0.3, "max": 3.5, "step": 0.1,
                        "label": "ATR SL Puffer", "description": "ATR-Puffer hinter dem Extrem"},
        "sl_lookback": {"value": 10, "min": 3, "max": 40, "step": 1,
                        "label": "Struktur Lookback", "description": "Kerzen fuer Swing-Tief/-Hoch"},
        "tp_rr": {"value": 1.8, "min": 1.0, "max": 8.0, "step": 0.1,
                  "label": "TP voll (R-Vielfaches)", "description": "Endziel als Vielfaches des Risikos"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        kc_p = int(params["kc_period"])
        atr_p = int(params["atr_period"])
        ema_p = int(params["ema_trend_period"])
        sl_lb = int(params["sl_lookback"])
        k_p, d_p = int(params["stoch_k"]), int(params["stoch_d"])
        need = max(kc_p, ema_p, atr_p, sl_lb, k_p + d_p) + 12
        if len(candles) < need:
            return None

        ti = self.indicators
        closes = [c["close"] for c in candles]
        price = closes[-1]
        last, prev = candles[-1], candles[-2]

        basis_arr = ti.calculate_ema(closes, kc_p)
        basis = self._last(basis_arr)
        atr = self._last(ti.calculate_atr(candles, atr_p)) or 0.0
        if basis is None or atr <= 0:
            return None
        mult = float(params["kc_mult"])
        upper = basis + mult * atr
        lower = basis - mult * atr

        k_arr, d_arr = ti.calculate_stochastic(candles, k_p, d_p)
        k_now = self._last(k_arr)
        d_now = self._last(d_arr)
        k_prev = k_arr[-2] if len(k_arr) >= 2 else None
        d_prev = d_arr[-2] if len(d_arr) >= 2 else None
        if k_now is None or d_now is None:
            return None

        ema_arr = ti.calculate_ema(closes, ema_p)
        ema_t = self._last(ema_arr)
        prev_e = ema_arr[-11] if len(ema_arr) >= 11 and ema_arr[-11] else None
        slope = (ema_t - prev_e) / prev_e if (ema_t and prev_e) else 0.0
        rel_vol = ti.relative_volume(candles, 20) or 0.0

        thr = float(params["trend_block_pct"]) / 100.0
        strong_bull = ema_t is not None and price > ema_t and slope > thr
        strong_bear = ema_t is not None and price < ema_t and slope < -thr
        block = int(params["block_counter_trend"]) == 1
        trend_ok_long = (not strong_bear) if block else True
        trend_ok_short = (not strong_bull) if block else True

        kc_long = (last["close"] <= lower) or (prev["close"] <= lower)
        kc_short = (last["close"] >= upper) or (prev["close"] >= upper)

        s_low, s_high = float(params["stoch_low"]), float(params["stoch_high"])
        in_low = min(k_now, k_prev if k_prev is not None else k_now) <= s_low
        in_high = max(k_now, k_prev if k_prev is not None else k_now) >= s_high
        cross_up = (k_prev is not None and d_prev is not None
                    and k_prev <= d_prev and k_now > d_now)
        cross_dn = (k_prev is not None and d_prev is not None
                    and k_prev >= d_prev and k_now < d_now)
        if int(params["require_cross"]) == 1:
            stoch_long = in_low and cross_up
            stoch_short = in_high and cross_dn
        else:
            stoch_long = in_low
            stoch_short = in_high

        rev_long = last["close"] > prev["close"] or last["close"] > lower
        rev_short = last["close"] < prev["close"] or last["close"] < upper
        if int(params["require_reversal"]) != 1:
            rev_long = rev_short = True

        vol_ok = True
        if int(params["use_vol_filter"]) == 1:
            vol_ok = rel_vol >= float(params["vol_climax_min"])

        signal_long = kc_long and stoch_long and rev_long and trend_ok_long and vol_ok
        signal_short = kc_short and stoch_short and rev_short and trend_ok_short and vol_ok
        signal_type = "LONG" if signal_long else ("SHORT" if signal_short else None)
        bias = "LONG" if (kc_long and stoch_long) else ("SHORT" if (kc_short and stoch_short) else None)

        rules = [
            {"id": "kc_extreme", "label": "Keltner Extrem",
             "description": f"Close ausserhalb des Kanals (EMA ± {mult}×ATR)",
             "long": bool(kc_long), "short": bool(kc_short)},
            {"id": "stoch_turn", "label": "Stochastik-Drehung",
             "description": f"Stoch <= {s_low:g} (Long) / >= {s_high:g} (Short) mit K/D-Drehung",
             "long": bool(stoch_long), "short": bool(stoch_short)},
            {"id": "reversal", "label": "Reversal-Kerze",
             "description": "Close dreht Richtung Kanal-Mitte",
             "long": bool(rev_long), "short": bool(rev_short)},
            {"id": "vol_climax", "label": "Volumen-Klimax",
             "description": "Erhoehtes Volumen = Kapitulation statt Trendstart",
             "long": bool(vol_ok), "short": bool(vol_ok)},
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
            tp1 = max(basis, price + risk * 0.8)   # TP1 = Kanal-Mitte
            levels = self._lv(price, sl, tp1, price + risk * tp_rr)
        elif signal_type == "SHORT":
            struct = ti.get_recent_high(candles, sl_lb)
            struct = max(struct, last["high"]) if struct is not None else last["high"]
            sl = struct + atr_buf
            risk = sl - price
            if risk <= 0:
                risk = price * 0.003
                sl = price + risk
            tp1 = min(basis, price - risk * 0.8)
            levels = self._lv(price, sl, tp1, price - risk * tp_rr)

        return {
            "indicators": {"kc_upper": round(upper, 6), "kc_basis": round(basis, 6),
                           "kc_lower": round(lower, 6), "stoch_k": round(k_now, 2),
                           "stoch_d": round(d_now, 2), "rel_vol": round(rel_vol, 2),
                           "price": round(price, 6), "atr": round(atr, 6),
                           "slope_pct": round(slope * 100, 3)},
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
