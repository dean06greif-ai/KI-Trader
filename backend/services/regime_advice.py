"""Regime-Lab: Plausibilitäts-Hinweise zu Autopilot-Einstellungen & -Ergebnis (rein).

Befund 06/2026 (Prod, nur lesend): Autopilot-Läufe mit 15m · 1080 Tage · 11 Coins,
Min-Phase 1-3 Tage, KEINE Obergrenze -> Sieger mit Ø Live-Phase 21-29 Tagen und
Score ~98-99 %. Ohne detektor-unabhängige Referenz misst der Score nur die
Selbst-Übereinstimmung (Live=Final) – träge Detektoren gewinnen fast automatisch.
Referenz-Treffer derselben Detektoren liegen typischerweise bei ~50-60 %.
"""
from typing import Dict, List, Optional

# Sweet Spot der Ø Live-Phase für Daytrading-Strategien auf Regime-Basis:
# kurz genug für Reaktion (<= ~2 Wochen), lang genug für genug Trades je Phase.
RECOMMENDED_BAND = (4.0, 14.0)
MIN_DAYS_FOR_PHASES = 540          # >= ~1,5 Jahre: Bull, Bär und Seitwärts enthalten
SATURATED_PCT = 97.0


def settings_advice(timeframe: str, days: int, n_symbols: int,
                    min_days: float, max_days: float) -> List[str]:
    """Hinweise zu den Autopilot-Eingaben (leer = passt)."""
    out: List[str] = []
    lo, hi = RECOMMENDED_BAND
    if not max_days:
        out.append(f"Keine Obergrenze der Ø Phase: die Suche driftet zu trägen Detektoren "
                   f"(lange Phasen = hohe Selbst-Übereinstimmung). Empfohlen: {lo:g}-{hi:g} Tage.")
    elif max_days > 21:
        out.append(f"Obergrenze {max_days:g} Tage ist für Daytrading sehr träge – Regimewechsel "
                   f"würden erst nach Wochen erkannt. Empfohlen: <= {hi:g} Tage.")
    if min_days < 2:
        out.append("Min. Ø Phase < 2 Tage: Regime flackert, Strategien bekommen je Phase zu wenige "
                   f"Trades. Empfohlen: >= {lo:g} Tage.")
    if max_days and min_days and max_days < min_days * 2:
        out.append("Sweet Spot sehr eng (Max < 2× Min): kaum Suchraum, Ergebnisse zufällig.")
    if str(timeframe) in ("1m", "3m", "5m", "15m"):
        out.append(f"Timeframe {timeframe}: viel Rauschen, Live=Final sättigt bei ~98 %. "
                   "Für Regime-Erkennung sind 1h (oder 4h) robuster.")
    if int(days or 0) < MIN_DAYS_FOR_PHASES:
        out.append(f"Zeitraum {days} Tage: zu wenige Phasen im Holdout. Empfohlen: >= "
                   f"{MIN_DAYS_FOR_PHASES} Tage (Bull, Bär und Seitwärts enthalten).")
    if int(n_symbols or 0) < 3:
        out.append("Weniger als 3 Coins: Erkennung wird auf Einzelverläufe überangepasst.")
    return out


def result_warnings(best_metrics: Optional[Dict], min_days: float, max_days: float) -> List[str]:
    """Warnungen zum Ergebnis (fehlende Referenz, gesättigter Score, Phase außerhalb)."""
    m = best_metrics or {}
    out: List[str] = []
    if m.get("reference_pct") is None:
        out.append("Keine detektor-unabhängige Referenz im Ergebnis – der Score misst nur die "
                   "Selbst-Übereinstimmung (Live=Final). Lokalen Worker aktualisieren oder in der "
                   "Cloud rechnen, sonst gewinnen träge Detektoren.")
    if float(m.get("direction_pct") or 0) >= SATURATED_PCT and m.get("reference_pct") is None:
        out.append(f"Live=Final >= {SATURATED_PCT:g} % ist gesättigt und trennt Varianten kaum.")
    ph = m.get("avg_live_phase_days")
    if ph is not None:
        if max_days and float(ph) > max_days:
            out.append(f"Ø Live-Phase {float(ph):.1f} Tage liegt über der Obergrenze {max_days:g}.")
        if min_days and float(ph) < min_days:
            out.append(f"Ø Live-Phase {float(ph):.1f} Tage liegt unter der Untergrenze {min_days:g}.")
    return out
