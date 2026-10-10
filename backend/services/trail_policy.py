"""Trailing-Stop-Regeln – EINE Quelle für Backtester (simulate_pair) und Live
(bitunix_trade._manage_trade), damit Optimizer-Ergebnisse live identisch wirken.

Konfiguration (alle optional, Default = bisheriges Verhalten):
  trail_after_tp1    An/Aus (historischer Name, gilt für alle Modi)
  trail_atr_mult     Abstand = ATR × Faktor
  trail_mode         "tp1" (Start erst nach TP1, Standard) | "profit_pct" (Start
                     ab X % Gewinn auf die Marge, wie be_trigger_profit_pct) |
                     "crv" (Start ab X R) – TP1 startet den Trail in jedem Modus
  trail_trigger_pct  Schwelle für profit_pct
  trail_trigger_crv  Schwelle für crv
  trail_start_be     bei Start vor TP1 (profit_pct/crv) den SL zuerst auf echtes
                     Break-Even ziehen (nur wenn BE noch unter/über dem Kurs liegt)

Der SL wird nur je Richtung VERBESSERT (Ratsche) – ein Trail unterhalb eines
bereits gesetzten Break-Even/Gewinnschutz-SL wird ignoriert, nie zurückgesetzt.
"""
from typing import Dict, Optional

TRAIL_MODES = ("tp1", "profit_pct", "crv")


def params(cfg: Dict) -> Dict:
    mode = cfg.get("trail_mode")
    return {"enabled": bool(cfg.get("trail_after_tp1", True)),
            "mode": mode if mode in TRAIL_MODES else "tp1",
            "mult": float(cfg.get("trail_atr_mult", 1.5) or 1.5),
            "trigger_pct": float(cfg.get("trail_trigger_pct", 10.0) or 10.0),
            "trigger_crv": float(cfg.get("trail_trigger_crv", 1.0) or 1.0),
            "start_be": bool(cfg.get("trail_start_be", True))}


def should_activate(p: Dict, side: str, entry: float, risk: float, price: float,
                    tp1_done: bool, unreal: float, capital: float) -> bool:
    """Trail starten? TP1 immer; zusätzlich je Modus Gewinn-% bzw. CRV (rein)."""
    if not p["enabled"]:
        return False
    if tp1_done:
        return True
    if p["mode"] == "profit_pct":
        return capital > 0 and p["trigger_pct"] > 0 and unreal / capital * 100 >= p["trigger_pct"]
    if p["mode"] == "crv" and risk > 0:
        target = entry + risk * p["trigger_crv"] if side == "LONG" else entry - risk * p["trigger_crv"]
        return price >= target if side == "LONG" else price <= target
    return False


def improves(side: str, cur_sl: float, new_sl: float) -> bool:
    return new_sl > cur_sl if side == "LONG" else new_sl < cur_sl


def trail_level(p: Dict, side: str, price: float, atr: float) -> float:
    return price - atr * p["mult"] if side == "LONG" else price + atr * p["mult"]


def start_be_level(p: Dict, side: str, price: float, be: float, tp1_done: bool) -> Optional[float]:
    """BE-Stufe beim vorzeitigen Trail-Start (nicht im tp1-Modus – dort regelt be_mode).
    Nur wenn BE auf der Schutzseite des Kurses liegt (sonst sofortiger Stop)."""
    if not p["start_be"] or p["mode"] == "tp1" or tp1_done:
        return None
    ok = be < price if side == "LONG" else be > price
    return be if ok else None
