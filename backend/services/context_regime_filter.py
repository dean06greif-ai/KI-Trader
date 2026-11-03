"""Kurzfrist-Regime-Filter je Strategie/Coin (Setup-Kontext des Markt-Beobachters).

Befund Prod 11/2026 (TrendFolge2, 437 Trades): trend_down_volatil +0,27 R,
trend_up_volatil −0,32 R, breakout_volatil −0,36 R, drift_normal −0,59 R – das
Kurzfrist-Regime beim Einstieg trennt gute und schlechte Trades deutlich.
Konfiguration in der Coin-/Strategie-Config: `context_regime_block` = Liste von
Regime-Labels (ai_market_observer.classify_regime_v2). Leer = aus (bisher).
Fail-open, wenn kein Regime bestimmbar ist. Reine Funktion + dünner Lookup.
"""
from typing import Dict, List, Optional, Tuple

BASES = ("trend_up", "trend_down", "range", "drift", "breakout")
VOLS = ("ruhig", "normal", "volatil")
LABELS = tuple(f"{b}_{v}" for b in BASES for v in VOLS)


def normalize(v) -> List[str]:
    return [x for x in (v or []) if x in LABELS] if isinstance(v, list) else []


def check(cfg: Optional[Dict], regime: Optional[str]) -> Tuple[bool, str]:
    blocked = normalize((cfg or {}).get("context_regime_block"))
    if not blocked or not regime:
        return True, ""
    if regime in blocked:
        return False, f"Kurzfrist-Regime-Filter: Markt aktuell '{regime}' – für diese Strategie gesperrt"
    return True, ""


def current_regime(symbol: str, candles=None) -> Optional[str]:
    try:
        from services.ai_market_observer import market_observer, compute_features
        return (market_observer.features_for(symbol) or {}).get("regime") \
            or ((compute_features(candles) or {}).get("regime") if candles else None)
    except Exception:
        return None
