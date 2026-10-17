"""Dynamische Setup-Gewichtung (rein & testbar) – Setups als gewichteter
Faktor neben der Eigenanalyse der KI, keine Fessel.

Idee (User-Wunsch 06/2026): Die KI bleibt autonom und tradet primär auf Basis
ihrer eigenen Analyse/Tiefenanalyse. Die manuell und von der KI eingepflegten
Setups fließen als GELERNTER Gewichtungsfaktor ein: Die echte Erfolgsbilanz
eines Setups (Klasse + Asset, aus dem Playbook-Cache) hebt oder senkt die
Konfidenz der Entscheidung automatisch und in engen Grenzen (max. ±10 Punkte).
Bewährte Setups werden so bevorzugt, schwache gedämpft – ohne harte Filter,
und das Gewicht entwickelt sich mit jeder neuen Trade-Statistik weiter.

Bayes-Shrinkage: Bei wenigen Trades zieht ein Prior (50% WR, Stärke 8 Trades)
das Gewicht Richtung neutral 1.0 – ein einzelner Glückstreffer macht kein
Setup 'bewährt', ein einzelner Fehltrade keines 'schwach'.
"""
import logging
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

PRIOR_TRADES = 8          # Prior-Stärke der Shrinkage (Pseudo-Trades mit WR 50%)
W_MIN, W_MAX = 0.75, 1.25  # harte Grenzen des Gewichts
MIN_TRADES_SIGNIFICANT = 5  # ab so vielen Trades zählt PnL-Tilt / Asset-Bilanz
MAX_CONF_DELTA = 10        # maximale Konfidenz-Anpassung in Punkten
CONF_SCALE = 40            # Gewicht->Punkte (0.25 Abweichung = 10 Punkte)
# ---- Erwartungswert-Gewichtung (Audit 2.6) ----
EV_CLAMP = 1.0             # |geschrumpftes R-Mittel| über 1 zählt nicht mehr
EV_SCALE = 0.25            # R-Mittel ±1 (geschrumpft) = volle Spanne ±0.25
WR_TIEBREAK_SCALE = 0.1    # Trefferquote nur Tiebreaker (max. ±0.05)


def _clamp(w: float) -> float:
    return round(min(W_MAX, max(W_MIN, w)), 3)


def weight_for(stats: Optional[Dict], ev_mode: bool = True) -> float:
    """Gewicht eines Setups aus seiner echten Bilanz (rein). 1.0 = neutral.

    Audit 2.6 (ev_mode, Default an): Basis ist der geschrumpfte ERWARTUNGSWERT
    je riskiertem USDT (Netto-R-Mittel = pnl / Summe risk_usdt, Shrink gegen 0
    mit PRIOR_TRADES); die Trefferquote ist nur noch Tiebreaker (kleiner Tilt).
    Ein Setup mit 90 % Winrate und negativem PnL wird damit ABgewertet.
    Fallback ohne risk_usdt-Daten (Alt-Trades vor 2.3): bisherige WR-Shrinkage."""
    if not isinstance(stats, dict):
        return 1.0
    n = int(stats.get("trades") or 0)
    if n <= 0:
        return 1.0
    wins = int(stats.get("wins") or 0)
    pnl = float(stats.get("pnl") or 0.0)
    wr_shrunk = (wins + 0.5 * PRIOR_TRADES) / (n + PRIOR_TRADES)
    risk = float(stats.get("risk_usdt") or 0.0)
    if ev_mode and risk > 0:
        ev = max(-EV_CLAMP, min(EV_CLAMP, pnl / risk))  # Clamp VOR dem Shrink
        w = 1.0 + ev * n / (n + PRIOR_TRADES) * EV_SCALE
        w += (wr_shrunk - 0.5) * WR_TIEBREAK_SCALE
        return _clamp(w)
    w = 1.0 + (wr_shrunk - 0.5)
    if n >= MIN_TRADES_SIGNIFICANT and pnl:
        w += 0.05 if pnl > 0 else -0.05
    return _clamp(w)


MIN_LIVE_TRADES = 5  # ab so vielen echten Live-Trades zählt die Live-Bilanz (Audit 2.5)


def judging_stats(live_stats: Optional[Dict], mixed_stats: Optional[Dict],
                  min_live: int = MIN_LIVE_TRADES) -> Tuple[Optional[Dict], bool]:
    """Welche Bilanz zählt fürs Gewicht? (rein, Audit 2.5)
    Live-Bilanz ab min_live echten Live-Trades; sonst die gemischte Statistik
    nur als Prior mit Abzug. Rückgabe (stats, paper_prior) – paper_prior=True
    heißt: darf dämpfen, aber nie über neutral (1.0) aufwerten."""
    if isinstance(live_stats, dict) and int(live_stats.get("trades") or 0) >= min_live:
        return live_stats, False
    return mixed_stats, True


def combined_weight(class_stats: Optional[Dict], asset_stats: Optional[Dict],
                    class_live_stats: Optional[Dict] = None,
                    live_pref: bool = True, ev_mode: bool = True) -> float:
    """Klassen-Bilanz, verfeinert durch die Setup×Asset-Bilanz (rein).
    Audit 2.5 (live_pref, Default an): ab MIN_LIVE_TRADES echten Live-Trades
    zählt die Live-Bilanz der Klasse; ohne genug Live-Daten wirkt die
    Paper-/Misch-Bilanz nur als Prior mit Abzug (kann dämpfen, wertet aber
    nie über 1.0 auf – Paper-Erfolg allein macht kein Setup 'bewährt').
    live_pref=False = altes Verhalten (gemischte Statistik, Schalter)."""
    base, paper_prior = (judging_stats(class_live_stats, class_stats) if live_pref
                         else (class_stats, False))
    w = weight_for(base, ev_mode=ev_mode)
    if isinstance(asset_stats, dict) \
            and int(asset_stats.get("trades") or 0) >= MIN_TRADES_SIGNIFICANT:
        w = (w + weight_for(asset_stats, ev_mode=ev_mode)) / 2.0
    if live_pref and paper_prior:
        w = min(w, 1.0)
    return _clamp(w)


def adjust_confidence(confidence: int, weight: float) -> Tuple[int, Optional[str]]:
    """Konfidenz mit dem gelernten Gewicht justieren (rein, max. ±10 Punkte).
    Liefert (neue Konfidenz, Notiz|None). Keine Anpassung bei conf<=0."""
    conf = int(confidence or 0)
    delta = int(round((float(weight) - 1.0) * CONF_SCALE))
    delta = max(-MAX_CONF_DELTA, min(MAX_CONF_DELTA, delta))
    if conf <= 0 or delta == 0:
        return conf, None
    new = max(0, min(100, conf + delta))
    if new == conf:
        return conf, None
    return new, f"Setup-Gewicht ×{weight:g}: Konfidenz {conf}→{new}"


def context_line(class_label: str, stats_map: Optional[Dict[str, Dict]]) -> Optional[str]:
    """Kompakte Prompt-Zeile der gelernten Gewichte einer Klasse (rein).
    Nur Setups mit genug Trades und spürbarer Abweichung – spart Tokens."""
    parts = []
    for sid, st in sorted((stats_map or {}).items()):
        if int((st or {}).get("trades") or 0) < MIN_TRADES_SIGNIFICANT:
            continue
        w = weight_for(st)
        if abs(w - 1.0) >= 0.03:
            parts.append(f"{sid} ×{w:g}")
    if not parts:
        return None
    return (f"GELERNTE SETUP-GEWICHTE {class_label} (justieren deine Konfidenz "
            f"automatisch, 1.0=neutral): " + ", ".join(parts))
