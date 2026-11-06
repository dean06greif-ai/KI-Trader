"""Timeframe-Kette für den Regime-Autopilot (optional, Standard AUS).

Ist die Kette aktiv, bewertet der Autopilot dieselbe Erkennung auf einer festen,
kleinen Leiter benachbarter Timeframes (Scoring) und sucht zuerst auf dem
besten weiter. Bringt der aktive Timeframe längere Zeit keine Verbesserung,
wechselt die Suche auf den nächsten Timeframe der Rangliste (Fallback) und
testet dort zuerst die bisher beste Erkennung. Ergebnis ist die beste
Kombination aus Erkennung UND Timeframe.

Alles hier ist rein (keine I/O) und damit isoliert testbar; die Steuerung
liegt in services/regime_autopilot.run_autopilot. Ohne Kette (tf_chain=False,
Standard) bleibt das Verhalten des Autopiloten bitgenau unverändert.

Vergleichbarkeit: der Auswahl-Score besteht aus Prozent-Kennzahlen und einer
Phasen-Strafe in TAGEN (nicht in Kerzen) – damit ist er über Timeframes
vergleichbar. Gegen Rauschen schützen eine Hysterese (der gewählte Timeframe
bzw. der Timeframe des Bestwerts wird nur bei deutlichem Vorsprung verlassen)
und der Beweislage-Filter (zu wenig Holdout-Kerzen -> Timeframe nicht nutzbar).
"""
from typing import Dict, List, Optional

from services import research_validation
from services.timeframes import tf_minutes

# Feste, smarte Leiter (keine Nutzer-Einstellung): unter 15m wird die Erkennung
# zu verrauscht (und die Kerzenmengen zu groß), über 1d fehlen Holdout-Kerzen.
LADDER = ("15m", "30m", "1h", "2h", "4h", "8h", "1d")
STEPS_DOWN = 1                # ein Schritt feiner
STEPS_UP = 2                  # zwei Schritte gröber
MAX_CHAIN = 1 + STEPS_DOWN + STEPS_UP
SWITCH_AFTER_STALE = 60       # Runden ohne Verbesserung auf dem aktiven TF -> Wechsel
CROSS_TF_MARGIN = 1.0         # Score-Vorsprung, um den Timeframe zu wechseln (Hysterese)

_ALIASES = {"24h": "1d", "60m": "1h", "120m": "2h", "240m": "4h", "480m": "8h"}


def enabled(body: Optional[Dict]) -> bool:
    """Standard AUS: nur ein ausdrückliches True schaltet die Kette ein."""
    return (body or {}).get("tf_chain") is True


def build_chain(timeframe: str) -> List[str]:
    """Kette für den gewählten Timeframe (rein): gewählter TF zuerst, dann die
    Nachbarn auf der Leiter (feiner/gröber). TFs außerhalb der Leiter (z.B. 5m)
    starten auf ihrem eigenen TF und ergänzen die nächstgröberen Stufen."""
    tf = str(timeframe or "")
    canon = _ALIASES.get(tf, tf)
    if canon in LADDER:
        i = LADDER.index(canon)
        lo, hi = max(i - STEPS_DOWN, 0), min(i + STEPS_UP, len(LADDER) - 1)
        others = [t for t in LADDER[lo:hi + 1] if t != canon]
        return [tf] + others
    mins = tf_minutes(tf)
    if not mins:
        return [tf]
    upper = [t for t in LADDER if tf_minutes(t) > mins][:MAX_CHAIN - 1]
    return [tf] + upper


def viable(metrics: Optional[Dict]) -> bool:
    """Timeframe nutzbar? Modell vorhanden UND genug Holdout-Kerzen (Beweislage)."""
    if not metrics:
        return False
    return research_validation.evidence_verdict(metrics.get("holdout_bars")) == "ok"


def rank(scores: Dict[str, Optional[float]], selected: str,
         viable_tfs: Optional[List[str]] = None) -> List[str]:
    """Rangliste der Timeframes (rein): höchster Score zuerst; der gewählte TF
    erhält die Hysterese als Bonus (wird nur bei deutlichem Vorsprung
    verdrängt). TFs ohne Score fallen weg; nicht nutzbare (Beweislage) auch –
    außer dem gewählten TF, der immer als Ausgangslage bleibt."""
    ok = set(viable_tfs) if viable_tfs is not None else set(scores)
    cands = [tf for tf, s in scores.items()
             if s is not None and (tf in ok or tf == selected)]
    order = {tf: i for i, tf in enumerate(scores)}

    def key(tf):
        bonus = CROSS_TF_MARGIN if tf == selected else 0.0
        return (-(float(scores[tf]) + bonus), order[tf])
    return sorted(cands, key=key)


def accepts(cand_score: float, cand_tf: str, best_score: Optional[float],
            best_tf: str, min_gain: float) -> bool:
    """Neuer Bestwert? Gleicher TF: wie bisher (MIN_GAIN). Anderer TF: erst ab
    deutlichem Vorsprung (Hysterese gegen Timeframe-Flackern)."""
    ref = best_score if best_score is not None else -1e9
    gain = min_gain if cand_tf == best_tf else max(min_gain, CROSS_TF_MARGIN)
    return cand_score > ref + gain


def should_switch(tf_stale: int, order: List[str]) -> bool:
    return len(order) > 1 and tf_stale >= SWITCH_AFTER_STALE


def next_tf(order: List[str], current: str) -> str:
    """Nächster TF der Rangliste (rundum) – Fallback, wenn der aktive stagniert."""
    if current not in order:
        return order[0]
    return order[(order.index(current) + 1) % len(order)]


def seen_key(tf: str, cfg_key: str, chain_on: bool) -> str:
    """Duplikat-Schlüssel: mit Kette ist dieselbe Erkennung auf einem anderen
    TF eine neue Bewertung; ohne Kette bleibt der Schlüssel unverändert."""
    return f"{tf}|{cfg_key}" if chain_on else cfg_key


def new_state(chain: List[str], selected: str) -> Dict:
    """Laufzeit-Zustand der Kette (wird live im Job und im Ergebnis gezeigt)."""
    return {"enabled": True, "chain": list(chain), "selected": selected,
            "order": [], "active": selected, "switches": 0, "tf_stale": 0,
            "per_tf": {tf: {"start_score": None, "best_score": None, "tested": 0,
                            "viable": False, "status": "pending", "note": None}
                       for tf in chain}}


def record(state: Dict, tf: str, score: Optional[float]) -> None:
    """Eine bewertete Variante auf `tf` zählen (rein, mutiert den Zustand)."""
    row = state["per_tf"].setdefault(tf, {"tested": 0, "best_score": None})
    row["tested"] = int(row.get("tested") or 0) + 1
    if score is not None and (row.get("best_score") is None or score > row["best_score"]):
        row["best_score"] = round(float(score), 3)


def summary(state: Dict, best_tf: str) -> Dict:
    """Kompakte Ergebnis-Zusammenfassung für Verlauf/Oberfläche."""
    return {"enabled": True, "chain": state["chain"], "order": state["order"],
            "selected_timeframe": state["selected"], "best_timeframe": best_tf,
            "changed_timeframe": best_tf != state["selected"],
            "switches": state["switches"], "per_tf": state["per_tf"],
            "switch_after_stale": SWITCH_AFTER_STALE, "cross_tf_margin": CROSS_TF_MARGIN}
