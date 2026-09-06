"""Kapital-Zuweisung des KI-Traders INNERHALB der Anlageklasse (rein & testbar).

Das Max-Kapital je Asset (Trade-Einstellungen) bleibt die harte Obergrenze.
Darunter wird die Positionsgröße regelbasiert (ohne LLM) skaliert – Faktor
0..1 aus drei Komponenten, der als `setup_asset_scale` am Signal hängt und in
bitunix_trade / limit_live_sync / position_sizing wie ml_risk_scale wirkt:

  1. Setup-Qualität in der Anlageklasse   (Winrate + PnL der Historie)
  2. Setup-Qualität auf DIESEM Asset       (Setup × Symbol)
  3. Einstiegsqualität                     (Konfidenz, ML-Gate-Wahrscheinlichkeit)

Eskalation bei einem Asset, das ein Setup "ins Minus zieht": erst Kapital
reduzieren (Faktor 0.5), als letzte Instanz Live-Trades für genau dieses
Setup × Asset AUSSETZEN (Faktor 0 -> der Trade läuft nur noch als Paper-
Datensammlung weiter). Das Setup selbst wird in der Klasse erst dann zurück-
gestuft, wenn das Gesamtbild schlecht ist (setup_lifecycle.breadth_ok:
mind. 1/3 der gehandelten Assets negativ).
"""
from typing import Dict, Optional, Tuple

# Setup-Faktor nach Urteil (ai_playbook.verdict_for)
SETUP_FACTOR = {"bewährt": 1.0, "neutral": 0.85, "test": 0.6, "schwach": 0.5}
# Asset-Ebene (Setup × Symbol)
ASSET_MIN_TRADES = 4          # darunter: keine Aussage -> Faktor 1.0
ASSET_REDUCE_FACTOR = 0.5     # PnL < 0 und WR < 45 % -> Kapital halbieren
ASSET_SUSPEND_TRADES = 6      # ab hier darf ausgesetzt werden ...
ASSET_SUSPEND_WINRATE = 30.0  # ... bei WR <= 30 % ODER
ASSET_SUSPEND_PNL_PCT = -5.0  # ... PnL <= -5 % der eingesetzten Margin
# Einstiegsqualität
ENTRY_FLOOR = 0.7             # Konfidenz = min_confidence -> 0.7, 100 -> 1.0
SCALE_FLOOR = 0.25            # Untergrenze, sofern nicht ausgesetzt


def _wr(st: Optional[Dict]) -> float:
    n = int((st or {}).get("trades") or 0)
    return (int((st or {}).get("wins") or 0) / n * 100.0) if n else 0.0


def setup_factor(stats: Optional[Dict]) -> Tuple[float, str]:
    """Faktor aus der Setup-Statistik der Anlageklasse."""
    n = int((stats or {}).get("trades") or 0)
    if not n:
        return SETUP_FACTOR["test"], "Setup ohne Daten in dieser Klasse"
    v = str((stats or {}).get("verdict") or "test")
    return SETUP_FACTOR.get(v, 0.85), f"Setup-Urteil {v} ({n} Trades, WR {_wr(stats):.0f}%)"


def asset_factor(stats: Optional[Dict]) -> Tuple[float, str]:
    """Faktor aus Setup × Symbol: 1.0 (ok/keine Daten), 0.5 (reduziert),
    0.0 (ausgesetzt)."""
    n = int((stats or {}).get("trades") or 0)
    if n < ASSET_MIN_TRADES:
        return 1.0, "Asset: zu wenig Daten für Setup × Asset"
    wr = _wr(stats)
    pnl = float(stats.get("pnl") or 0)
    margin = float(stats.get("margin") or 0)
    pnl_pct = (pnl / margin * 100.0) if margin > 0 else None
    if n >= ASSET_SUSPEND_TRADES and (
            wr <= ASSET_SUSPEND_WINRATE
            or (pnl_pct is not None and pnl_pct <= ASSET_SUSPEND_PNL_PCT)):
        pct = f", {pnl_pct:+.1f}% der Margin" if pnl_pct is not None else ""
        return 0.0, f"Asset AUSGESETZT: {n} Trades, WR {wr:.0f}%, PnL {pnl:+.2f}{pct}"
    if pnl < 0 and wr < 45:
        return ASSET_REDUCE_FACTOR, f"Asset reduziert: {n} Trades, WR {wr:.0f}%, PnL {pnl:+.2f}"
    return 1.0, f"Asset ok: {n} Trades, WR {wr:.0f}%, PnL {pnl:+.2f}"


def entry_factor(confidence, min_confidence, p_win: Optional[float] = None) -> Tuple[float, str]:
    """Einstiegsqualität: Konfidenz linear min_confidence..100 -> 0.7..1.0;
    liegt eine ML-Gate-Wahrscheinlichkeit vor, zählt der Mittelwert beider."""
    try:
        conf = float(confidence or 0)
        mc = float(min_confidence or 0)
    except (TypeError, ValueError):
        return 1.0, "Einstieg: Konfidenz unbekannt"
    span = max(1.0, 100.0 - mc)
    f = ENTRY_FLOOR + (1.0 - ENTRY_FLOOR) * max(0.0, min(1.0, (conf - mc) / span))
    note = f"Konfidenz {conf:.0f}"
    if p_win is not None:
        try:
            p = max(0.0, min(1.0, float(p_win)))
            f = (f + (ENTRY_FLOOR + (1.0 - ENTRY_FLOOR) * p)) / 2.0
            note += f", ML p_win {p:.2f}"
        except (TypeError, ValueError):
            pass
    return round(f, 4), f"Einstieg: {note}"


def allocation(setup_stats: Optional[Dict], asset_stats: Optional[Dict],
               confidence, min_confidence, p_win: Optional[float] = None) -> Dict:
    """Gesamtfaktor 0..1 (rein). suspended=True -> kein Live-Einstieg."""
    fs, ns = setup_factor(setup_stats)
    fa, na = asset_factor(asset_stats)
    fe, ne = entry_factor(confidence, min_confidence, p_win)
    if fa <= 0:
        return {"scale": 0.0, "suspended": True, "setup_factor": fs, "asset_factor": 0.0,
                "entry_factor": fe, "note": f"{na} | {ns}"}
    scale = max(SCALE_FLOOR, min(1.0, fs * fa * fe))
    return {"scale": round(scale, 4), "suspended": False, "setup_factor": fs,
            "asset_factor": fa, "entry_factor": fe, "note": f"{ns} | {na} | {ne}"}


def gate_p_win(dec: Dict) -> Optional[float]:
    gs = dec.get("gate_shadow")
    if isinstance(gs, dict) and gs.get("p_win") is not None:
        try:
            return float(gs["p_win"])
        except (TypeError, ValueError):
            return None
    return None
