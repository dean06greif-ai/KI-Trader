"""Realistische Limit-Order-Simulation (rein & testbar) – Backtester UND Optimizer
(beide nutzen services.backtester.simulate_pair).

Vorher: Limit = Signalpreis (Schlusskurs), Fill sobald die Folgekerze den Preis
nur BERÜHRT – das passiert fast immer, Ergebnis war praktisch "Market mit
Maker-Gebühr". Realistisch ist dagegen:

  * Fill erst, wenn der Kurs das Limit DURCHBRICHT (penetration, Standard 1 Tick
    ≈ 0,01 %): bei reiner Berührung steht die eigene Order in der Warteschlange
    meist noch hinter anderen und wird nicht ausgeführt.
  * Kurs-Lücke über das Limit hinaus -> Fill zum (besseren) Eröffnungskurs.
  * Optionaler Abstand (limit_offset_pct): Limit unter (Long) / über (Short) dem
    Signalpreis – besserer Einstieg, dafür mehr verpasste Trades.
  * Fill-Kerze wird mitbewertet: läuft die Kerze nach dem Fill bis zum Stop,
    zählt der Stop (konservativ, Reihenfolge innerhalb der Kerze unbekannt).
    Gewinnziele in der Fill-Kerze werden NICHT gutgeschrieben.
  * Adverse Selection entsteht dadurch automatisch: gefüllt wird vor allem, wenn
    der Kurs gegen den Trade läuft – Durchstarter werden verpasst.

`limit_fill_mode="touch"` stellt das alte (optimistische) Verhalten wieder her.
"""
from typing import Dict, Optional

DEFAULT_PENETRATION_PCT = 0.01
FILL_MODES = ("realistic", "touch")


def params(cfg: Dict) -> Dict:
    mode = str(cfg.get("limit_fill_mode") or "realistic").lower()
    if mode not in FILL_MODES:
        mode = "realistic"
    pen = cfg.get("limit_penetration_pct")
    pen = DEFAULT_PENETRATION_PCT if pen in (None, "") else max(float(pen), 0.0)
    return {"mode": mode,
            "offset_pct": max(float(cfg.get("limit_offset_pct") or 0.0), 0.0),
            "penetration": (pen if mode == "realistic" else 0.0) / 100.0}


def limit_price(side: str, ref_price: float, offset_pct: float) -> float:
    """Limit-Preis aus dem Signalpreis: Long darunter, Short darüber."""
    off = max(float(offset_pct or 0.0), 0.0) / 100.0
    return ref_price * (1 - off) if side == "LONG" else ref_price * (1 + off)


def fill_price(side: str, limit: float, c_open: float, c_high: float, c_low: float,
               penetration: float) -> Optional[float]:
    """Fill-Preis der Kerze oder None (nicht gefüllt)."""
    if side == "LONG":
        if c_open <= limit:              # Lücke unter das Limit -> sofort zum Open
            return c_open
        return limit if c_low < limit * (1 - penetration) or (penetration == 0 and c_low <= limit) else None
    if c_open >= limit:
        return c_open
    return limit if c_high > limit * (1 + penetration) or (penetration == 0 and c_high >= limit) else None


def stop_in_fill_bar(side: str, sl: float, c_high: float, c_low: float) -> bool:
    """Wird der Stop noch in der Fill-Kerze erreicht? (konservativ)"""
    return c_low <= sl if side == "LONG" else c_high >= sl


def tp_limit_hit(side: str, level: float, c_high: float, c_low: float, penetration: float) -> bool:
    """TP als Limit-Order: wie der Entry erst bei Durchbruch gefüllt."""
    if side == "LONG":
        return c_high > level * (1 + penetration) or (penetration == 0 and c_high >= level)
    return c_low < level * (1 - penetration) or (penetration == 0 and c_low <= level)
