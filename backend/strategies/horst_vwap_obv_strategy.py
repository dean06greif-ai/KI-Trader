"""Horst 1m Scalping: VWAP-Bänder + OBV-RSI Extrem (verbesserte Variante).

Original-Regeln (Kian Horst): 1m-Kerze schließt in der unteren/oberen
VWAP-Range + OBV-RSI unter 30 (Long) bzw. über 70 (Short), SL/TP 0.6% (1:1),
Exit an der VWAP-Mittellinie.

Verbesserungen (Backtest-Kritik: Gebühren + CRV < 1:1 durch Midline-Exit):
- TP default 0.9% statt 0.6% (CRV 1.5:1); Original via tp_pct=0.6 einstellbar
- Fee-Edge-Filter: Mindestabstand zum VWAP, damit der Weg zurück zur
  Mittellinie die Gebühren übersteigt
- TP1 = VWAP-Mittellinie (Horst Midline-Exit als Teilgewinn statt Voll-Exit)
"""
from typing import Dict, List, Optional
import numpy as np
from strategies.base_strategy import BaseStrategy


class HorstVWAPOBVStrategy(BaseStrategy):
    STRATEGY_ID = "horst_vwap_obv"
    STRATEGY_NAME = "Horst VWAP + OBV-RSI Scalping"
    STRATEGY_DESCRIPTION = ("1m-Scalping nach Kian Horst: Kerze schließt in der unteren/"
                            "oberen VWAP-Range + OBV-RSI Extrem (<30 Long / >70 Short). "
                            "Verbessert: Fee-Edge-Filter, TP 0.9% (CRV 1.5:1) statt 1:1, "
                            "TP1 an der VWAP-Mittellinie. Original: tp_pct=0.6, min_dev_pct=0.")
    STRATEGY_TIMEFRAME = "1m"

    DEFAULT_PARAMS = {
        "band_window": {"value": 50, "min": 20, "max": 200, "step": 5,
                        "label": "Band-Fenster", "description": "Kerzen für die StdDev der VWAP-Bänder"},
        "band_mult": {"value": 2.0, "min": 0.5, "max": 4.0, "step": 0.1,
                      "label": "Band-Multiplikator", "description": "StdDev-Vielfaches für die grüne/rote Range"},
        "obv_rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                           "label": "OBV-RSI Periode", "description": "RSI-Periode auf dem On-Balance-Volume"},
        "obv_rsi_long_max": {"value": 30, "min": 10, "max": 45, "step": 1,
                             "label": "OBV-RSI Long Max", "description": "OBV-RSI unter diesem Wert = überverkauft (Long)"},
        "obv_rsi_short_min": {"value": 70, "min": 55, "max": 90, "step": 1,
                              "label": "OBV-RSI Short Min", "description": "OBV-RSI über diesem Wert = überkauft (Short)"},
        "min_dev_pct": {"value": 0.15, "min": 0.0, "max": 1.0, "step": 0.01,
                        "label": "Fee-Edge Min-Abstand %", "description": "Mindestabstand zum VWAP in % (0 = Horst-Original ohne Gebühren-Puffer)"},
        "sl_pct": {"value": 0.6, "min": 0.2, "max": 2.0, "step": 0.05,
                   "label": "Stop Loss %", "description": "Fixer SL in % (Horst-Original: 0.6)"},
        "tp_pct": {"value": 0.9, "min": 0.3, "max": 3.0, "step": 0.05,
                   "label": "Take Profit %", "description": "Fixer TP in % (Horst-Original: 0.6 = CRV 1:1)"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        band_window = int(params["band_window"])
        obv_period = int(params["obv_rsi_period"])
        need = max(band_window, obv_period + 1, 30) + 10
        if len(candles) < need:
            return None
        ti = self.indicators
        closes = [c["close"] for c in candles]
        price = closes[-1]

        vwap_arr = ti.calculate_vwap(candles)
        vwap = vwap_arr[-1]
        if not vwap:
            return None
        dev_series = [closes[i] - vwap_arr[i] for i in range(len(closes))]
        band = float(np.std(dev_series[-band_window:])) * float(params["band_mult"])
        if band <= 0:
            return None

        obv = self._obv_series(candles)
        obv_rsi_arr = ti.calculate_rsi(obv, obv_period)
        obv_rsi = obv_rsi_arr[-1] if obv_rsi_arr else None
        if obv_rsi is None:
            return None

        dev = price - vwap
        dev_pct = abs(dev) / price * 100 if price else 0.0
        in_lower = price <= vwap - band
        in_upper = price >= vwap + band
        obv_l = obv_rsi <= float(params["obv_rsi_long_max"])
        obv_s = obv_rsi >= float(params["obv_rsi_short_min"])
        edge_ok = dev_pct >= float(params["min_dev_pct"])

        rules = [
            {"id": "vwap_zone", "label": "VWAP-Range",
             "description": "Kerze schließt in der unteren (Long) / oberen (Short) VWAP-Range",
             "long": bool(in_lower), "short": bool(in_upper)},
            {"id": "obv_rsi_extreme", "label": "OBV-RSI Extrem",
             "description": f"OBV-RSI < {params['obv_rsi_long_max']} (Long) / > {params['obv_rsi_short_min']} (Short)",
             "long": bool(obv_l), "short": bool(obv_s)},
            {"id": "fee_edge", "label": "Fee-Edge",
             "description": "Mindestabstand zum VWAP als Gebühren-Puffer",
             "long": bool(edge_ok and in_lower), "short": bool(edge_ok and in_upper)},
        ]
        signal_long = in_lower and obv_l and edge_ok
        signal_short = in_upper and obv_s and edge_ok
        signal_type = "LONG" if signal_long else ("SHORT" if signal_short else None)
        bias = "LONG" if in_lower else ("SHORT" if in_upper else None)
        long_count = sum(1 for r in rules if r["long"])
        short_count = sum(1 for r in rules if r["short"])

        levels = None
        if signal_type:
            sl_pct = float(params["sl_pct"]) / 100.0
            tp_pct = float(params["tp_pct"]) / 100.0
            if signal_type == "LONG":
                sl = price * (1 - sl_pct)
                tp_full = price * (1 + tp_pct)
                tp1 = min(max(vwap, price * (1 + tp_pct / 2)), tp_full)
            else:
                sl = price * (1 + sl_pct)
                tp_full = price * (1 - tp_pct)
                tp1 = max(min(vwap, price * (1 - tp_pct / 2)), tp_full)
            levels = {"entry": round(price, 6), "stop_loss": round(sl, 6),
                      "take_profit_1": round(tp1, 6), "take_profit_full": round(tp_full, 6),
                      "crv": round(ti.calculate_crv(price, sl, tp_full), 2)}

        return {
            "indicators": {"rsi": round(obv_rsi, 2), "ema_fast": round(vwap, 6),
                           "ema_slow": round(vwap, 6), "price": round(price, 6),
                           "vwap": round(vwap, 6), "obv_rsi": round(obv_rsi, 2),
                           "band": round(band, 6), "dev_pct": round(dev_pct, 3)},
            "rules": rules, "bias": bias,
            "long_count": long_count, "short_count": short_count, "rules_total": len(rules),
            "signal_type": signal_type, "is_pre_signal": False, "levels": levels,
        }

    @staticmethod
    def _obv_series(candles: List[Dict]) -> List[float]:
        obv = [0.0]
        for i in range(1, len(candles)):
            v = candles[i].get("volume", 0) or 0
            if candles[i]["close"] > candles[i - 1]["close"]:
                obv.append(obv[-1] + v)
            elif candles[i]["close"] < candles[i - 1]["close"]:
                obv.append(obv[-1] - v)
            else:
                obv.append(obv[-1])
        return obv

    # ----------------------- Vectorized Fast-Path -----------------------
    @staticmethod
    def vectorized_signals(fs, params: Dict) -> Optional[Dict]:
        """VWAP-Band-Zone + OBV-RSI Extrem + Fee-Edge -- vektorisiert."""
        import pandas as pd
        from services import vec

        band_window = int(params["band_window"])
        obv_period = int(params["obv_rsi_period"])
        need = max(band_window, obv_period + 1, 30) + 10

        close = fs.close
        vol = fs.vol
        vwap = fs.get("vwap", {})

        diff = np.diff(close, prepend=close[:1])
        obv = np.cumsum(np.sign(diff) * vol)
        obv_rsi = vec.rsi(obv, obv_period)

        with np.errstate(invalid="ignore", divide="ignore"):
            dev = close - vwap
            band = pd.Series(dev).rolling(band_window, min_periods=band_window) \
                .std(ddof=0).to_numpy() * float(params["band_mult"])
            dev_pct = np.abs(dev) / np.where(close > 0, close, np.nan) * 100

            in_lower = close <= vwap - band
            in_upper = close >= vwap + band
            obv_l = obv_rsi <= float(params["obv_rsi_long_max"])
            obv_s = obv_rsi >= float(params["obv_rsi_short_min"])
            edge_ok = dev_pct >= float(params["min_dev_pct"])

            long_ok = in_lower & obv_l & edge_ok
            short_ok = in_upper & obv_s & edge_ok

        valid = ~np.isnan(obv_rsi) & ~np.isnan(vwap) & ~np.isnan(band) & (band > 0)
        long_ok &= valid
        short_ok &= valid
        return {"long": long_ok, "short": short_ok,
                "warmup": need, "rules_total": 3, "rsi": obv_rsi}
