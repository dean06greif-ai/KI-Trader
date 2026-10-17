"""AP08 (Befund R15): Gemeinsamer MarketContext-Vertrag statt zufälliger
Regimegleichheit.

Taxonomie (versioniert): drei getrennte EBENEN, die nie stillschweigend
gleichgesetzt werden dürfen:
  - structural:    langfristiges Marktregime (Regime-Lab/Gate, Kerzenmodell)
  - setup_context: kurzfristiger Marktzustand des KI-Observers (1m-Features)
  - risk_overlay:  Risiko-Zustände (Kill-Switch, Safety) – reserviert

Richtungs-IDENTITÄT kommt aus den Regime-IDs (regime_engine.split_id),
nicht aus deutschen Label-Substrings. Für Legacy-KMeans-Modelle (Cluster-IDs
ohne Richtungssemantik) gibt es einen EXPLIZIT benannten Label-Adapter.
`unknown`/`stale` sind echte Zustände; Konfidenzen sind heuristische Scores,
keine kalibrierten Gewinnwahrscheinlichkeiten (confidence_kind).
"""
import hashlib
import json
from typing import Dict, Optional

TAXONOMY_VERSION = 1
LAYERS = ("structural", "setup_context", "risk_overlay")
DIRECTIONS = ("down", "sideways", "up")
# Adapter: Richtungs-Taxonomie -> historische Gate-Phasen-Namen (Config-Werte
# `regime_block_phases` bleiben unverändert gültig).
PHASE_BY_DIRECTION = {"down": "bär", "sideways": "seitwärts", "up": "bulle"}

# Benannter Adapter: Kurzfrist-Regime des Observers -> setup_context-Zustand.
# BEWUSST eigene Namen – ein Observer-"trend_up" ist KEIN strukturelles
# Aufwärtsregime (anderer Horizont, anderes Modell, andere Datenbasis).
OBSERVER_STATES = {
    "trend_up": "short_term_up", "trend_down": "short_term_down",
    "range": "short_term_range", "breakout_up": "short_term_breakout_up",
    "breakout_down": "short_term_breakout_down",
    "drift_up": "short_term_drift_up", "drift_down": "short_term_drift_down",
}


def direction_from_regime_id(rid, mode) -> Optional[str]:
    """Richtung aus der Regime-ID (v2-Taxonomie) – keine Label-Substrings."""
    if rid is None:
        return None
    from services import regime_engine as eng
    t, _ = eng.split_id(int(rid), eng.norm_mode(mode))
    return DIRECTIONS[t] if 0 <= t <= 2 else None


def legacy_phase_from_label(label: Optional[str]) -> Optional[str]:
    """EXPLIZITER Legacy-Adapter für KMeans-Modelle: deren Cluster-IDs tragen
    keine Richtungssemantik, nur das Label. Nur hier ist die Substring-
    Zuordnung erlaubt – als benannter Adapter, nicht als Identität."""
    low = (label or "").strip().lower()
    if not low:
        return None
    if "seitwärts" in low:
        return "seitwärts"
    if "aufwärts" in low:
        return "bulle"
    if "abwärts" in low:
        return "bär"
    return None


def direction_from_phase(phase: Optional[str]) -> Optional[str]:
    return {v: k for k, v in PHASE_BY_DIRECTION.items()}.get(phase or "")


def model_fingerprint(model: Dict) -> Optional[str]:
    """Stabiler Kurz-Hash des Regime-Artifacts (Provenienz: WELCHER Modell-
    Stand hat diesen Kontext geliefert)."""
    if not isinstance(model, dict):
        return None
    keys = ("engine", "config", "regime_mode", "norm_mean", "norm_std",
            "centroids", "lookback_bars", "timeframe")
    payload = {k: model.get(k) for k in keys if k in model}
    raw = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


def context_state(direction: Optional[str], age_sec: Optional[float],
                  ttl_sec: Optional[float]) -> str:
    if direction not in DIRECTIONS:
        return "unknown"
    if age_sec is not None and ttl_sec is not None and age_sec > ttl_sec:
        return "stale"
    return "ok"


def structural_context(source: str, direction: Optional[str] = None,
                       label: Optional[str] = None,
                       confidence: Optional[float] = None,
                       model_fp: Optional[str] = None,
                       age_sec: Optional[float] = None,
                       ttl_sec: Optional[float] = None) -> Dict:
    """Struktureller MarketContext-Eintrag (rein)."""
    return {"layer": "structural", "taxonomy_version": TAXONOMY_VERSION,
            "source": source, "direction": direction, "label": label,
            "phase": PHASE_BY_DIRECTION.get(direction or ""),
            "state": context_state(direction, age_sec, ttl_sec),
            "confidence": confidence, "confidence_kind": "heuristic",
            "model_fingerprint": model_fp}


def observer_context(observer_regime: Optional[str]) -> Dict:
    """setup_context-Eintrag aus dem Kurzfrist-Regime des Observers – über
    den benannten Adapter, ohne strukturelle Richtungs-Identität."""
    state = OBSERVER_STATES.get(str(observer_regime or "")) or None
    return {"layer": "setup_context", "taxonomy_version": TAXONOMY_VERSION,
            "source": "ai_market_observer", "observer_regime": observer_regime,
            "context_state": state,
            "state": "ok" if state else "unknown",
            "confidence_kind": "heuristic"}
