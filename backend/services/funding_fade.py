"""Funding-Fade-Setup: Kontext-Radar für extrem überhitzte Funding-Raten.

Datenquelle ist der bestehende Makro-Kontext (services/macro_context.py,
funding_and_oi – OKX public, bereits im Prompt als FUNDING/OI-Zeilen). Dieses
Modul verdichtet daraus ein handelbares Signal für das Playbook-Setup
'funding_fade' (services/ai_playbook.py):

  * Funding-Rate >= +0.05 %/8h  -> Longs überhitzt  -> Fade-Richtung SHORT
  * Funding-Rate <= -0.05 %/8h  -> Shorts überhitzt -> Fade-Richtung LONG
  * |Rate| >= 0.10 %/8h         -> 'extrem' (Danger-Zone)

Schwellen nach gängiger Praxis (0.01 %/8h ist der neutrale Basiswert; ab dem
~5-fachen gilt die Positionierung als crowded). Wichtig: NIE auf Funding allein
einsteigen – Funding kann in starken Trends wochenlang extrem bleiben. Die
Regeln (Bestätigung über Struktur, OI-Lesart) stehen in den Kontext-Zeilen und
werden von der KI im Rahmen des normalen Reife-Gates (Paper zuerst) gehandelt.

Alle Funktionen sind rein & testbar.
"""
from typing import Dict, List, Optional

# neutraler Basiswert ist 0.0001 (0.01 %/8h); ab dem 5-fachen crowded
EXTREME_RATE_8H = 0.0005      # 0.05 %/8h -> überhitzt
DANGER_RATE_8H = 0.001        # 0.10 %/8h -> extrem


def classify(rate) -> Optional[Dict]:
    """Funding-Rate (pro 8h, z.B. 0.0007) -> Fade-Einstufung oder None."""
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return None
    if abs(r) < EXTREME_RATE_8H:
        return None
    return {"side": "SHORT" if r > 0 else "LONG",
            "level": "extrem" if abs(r) >= DANGER_RATE_8H else "überhitzt",
            "rate": r,
            "annualized_pct": round(r * 3 * 365 * 100, 1)}


def _oi_read(oi_delta_1h) -> str:
    if not isinstance(oi_delta_1h, (int, float)):
        return ""
    if oi_delta_1h > 0:
        return (f", OI-Δ 1h {oi_delta_1h:+.2f}% (Crowd baut weiter auf – "
                f"Squeeze-Potenzial steigt)")
    return (f", OI-Δ 1h {oi_delta_1h:+.2f}% (Crowd hebelt bereits ab – "
            f"Flush-Potenzial sinkt)")


def context_lines(funding_oi: Dict) -> List[str]:
    """Prompt-Zeilen für das Setup 'funding_fade' aus dem funding_oi-Kontext
    (macro_context) – leer, wenn nirgends extremes Funding vorliegt."""
    hits: List[str] = []
    for sym, f in (funding_oi or {}).items():
        c = classify((f or {}).get("funding_rate"))
        if not c:
            continue
        hits.append(f"- {sym}: Funding {c['rate'] * 100:+.4f}%/8h "
                    f"(ann. {c['annualized_pct']:+.1f}%) = {c['level'].upper()} "
                    f"→ Fade-Richtung {c['side']}"
                    + _oi_read((f or {}).get("oi_delta_1h_pct")))
    if not hits:
        return []
    return (["FUNDING-FADE-RADAR (Setup 'funding_fade' – nur bei diesen Meldungen handeln):"]
            + hits
            + ["Regeln funding_fade: NIE auf Funding allein einsteigen – erst Bestätigung "
               "abwarten (tieferes Hoch bei Long-Crowd bzw. höheres Tief bei Short-Crowd "
               "oder 15m-Strukturbruch gegen die Crowd). SL hinter dem letzten Extrem, "
               "Ziel Mean-Reversion-Zone (VWAP/POC). Konservative Größe (capital_pct "
               "niedrig): Trends können Funding lange extrem halten."])
