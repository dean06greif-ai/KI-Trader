"""Ausreißer-Assets im Optimizer erkennen (rein & testbar).

Optimiert man eine Strategie über viele Assets, fallen oft nur ein, zwei Assets
komplett aus dem Raster und ziehen das Gesamtergebnis (und damit die gewählten
Parameter) nach unten. Diese Hilfe erkennt solche Assets automatisch und
konservativ:

  * nur bei mind. MIN_ASSETS Assets mit Trades (sonst gibt es kein "Raster")
  * Kandidat nur mit PnL < 0 und genug Trades (MIN_TRADES) – zu wenig Daten
    ist kein Ausreißer, sondern Unsicherheit
  * robuster z-Wert (Median/MAD) <= -Z_MAX ODER Verlust größer als der Median-
    Gewinn der übrigen Assets (klar gegen den Trend der Mehrheit)
  * höchstens ein Drittel der Assets (sonst ist die Strategie selbst schwach)
  * der Rest muss danach insgesamt profitabel sein

Ergebnis dient zwei Optionen in der UI: Parameter für ALLE Assets übernehmen
ODER übernehmen und die Ausreißer-Assets für diese Strategie auf AUS stellen
(bzw. ohne sie neu optimieren).
"""
from statistics import median
from typing import Dict, List

MIN_ASSETS = 3
MIN_TRADES = 5
Z_MAX = 2.0
MAX_SHARE = 1 / 3


def detect(per_symbol: Dict[str, Dict]) -> Dict:
    """per_symbol: {sym: {"pnl", "trades", "win_rate"}} -> {"outliers", "reasons", "kept"}."""
    rows = {s: v for s, v in (per_symbol or {}).items() if int((v or {}).get("trades") or 0) > 0}
    empty = {"outliers": [], "reasons": {}, "kept": sorted(rows)}
    if len(rows) < MIN_ASSETS:
        return empty
    pnls = {s: float(v.get("pnl") or 0) for s, v in rows.items()}
    max_out = max(1, int(len(rows) * MAX_SHARE))
    outliers: List[str] = []
    reasons: Dict[str, str] = {}
    for sym in sorted(pnls, key=pnls.get):   # schlechteste zuerst
        if len(outliers) >= max_out:
            break
        p = pnls[sym]
        if p >= 0 or int(rows[sym].get("trades") or 0) < MIN_TRADES:
            break
        others = [v for s, v in pnls.items() if s != sym and s not in outliers]
        med = median(others)
        mad = median([abs(v - med) for v in others]) * 1.4826
        spread = max(mad, abs(med) * 0.25, 1e-9)
        z = (p - med) / spread
        against_majority = med > 0 and abs(p) > med
        if z > -Z_MAX and not against_majority:
            break
        outliers.append(sym)
        reasons[sym] = (f"PnL {p:+.2f} vs. Median der übrigen {med:+.2f} "
                        f"(robuster z-Wert {z:.1f}, {int(rows[sym].get('trades') or 0)} Trades, "
                        f"WR {float(rows[sym].get('win_rate') or 0):.0f}%)")
    if not outliers:
        return empty
    kept = [s for s in pnls if s not in outliers]
    if sum(pnls[s] for s in kept) <= 0:
        return empty    # Rest ebenfalls schwach -> kein Ausreißer-, sondern Strategie-Problem
    return {"outliers": outliers, "reasons": reasons, "kept": sorted(kept)}


def recommendation(entry: Dict) -> Dict:
    """Ampel für ein Top-Ergebnis: empfohlen (live-tauglich) / brauchbar / nicht empfohlen."""
    m = entry.get("metrics") or {}
    t = entry.get("test_metrics")
    pnl_ok = float(m.get("pnl") or 0) > 0
    test_ok = t is None or float((t or {}).get("pnl") or 0) > 0
    breadth = entry.get("positive_symbols_pct")
    breadth_ok = breadth is None or float(breadth) >= 50
    passed = entry.get("passed") is not False
    if passed and pnl_ok and test_ok and breadth_ok:
        return {"level": "recommended", "label": "Empfohlen · live-tauglich"}
    ov = entry.get("outlier_variant") or {}
    ov_ok = float((ov.get("metrics") or {}).get("pnl") or 0) > 0 and \
        (ov.get("test_metrics") is None or float((ov.get("test_metrics") or {}).get("pnl") or 0) > 0)
    if passed and pnl_ok and test_ok and ov_ok:
        return {"level": "recommended_ex", "label": "Empfohlen ohne Ausreißer-Assets"}
    if pnl_ok and test_ok:
        return {"level": "usable", "label": "Brauchbar – einzelne Checks nicht bestanden"}
    return {"level": "rejected", "label": "Nicht empfohlen"}
