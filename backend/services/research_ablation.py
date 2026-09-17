"""AP13 (Zielbild §6 Schritt B): Indikator-Ablation für Regime-Detektoren.

„Derselbe Plan ohne den Indikator, mit einfacher Alternative" – je Detektor
werden die abschaltbaren Bestätigungs-Komponenten einzeln entfernt und eine
bewusst EINFACHE Alternative mitgerechnet. Bewertung/Auswahl folgt AP07:
innere Validierung entscheidet, der Holdout bleibt finaler Test.
Reine Funktionen, keine DB/IO – der Job lebt in regime_lab.run_ablation.
"""
from typing import Dict, List, Optional

FULL_KEY = "full"
VERDICT_MARGIN_PP = 1.0  # ab dieser Differenz (innere Val.) gilt "trägt bei"


def ablation_variants(engine_config: Optional[Dict]) -> List[Dict]:
    """Varianten-Liste für den konfigurierten Detektor: volle Konfiguration,
    je entfernbarer Komponente eine Variante, plus einfache Alternative."""
    ec = dict(engine_config or {})
    det = str(ec.get("detector") or "reactive").lower()
    variants = [{"key": FULL_KEY, "name": f"{det} (voll)",
                 "removed": None, "engine_config": ec}]

    def add(key: str, name: str, removed: str, **over):
        variants.append({"key": key, "name": name, "removed": removed,
                         "engine_config": {**ec, **over}})

    if det == "reactive":
        if ec.get("mtf_confirm", True):
            add("no_mtf", "ohne MTF-Bestätigung", "mtf_confirm",
                mtf_confirm=False)
        if ec.get("use_volume_confirm", True):
            add("no_volume", "ohne Volumen-Bestätigung", "use_volume_confirm",
                use_volume_confirm=False)
        if ec.get("use_ema_confirm", True):
            add("no_ema_confirm", "ohne EMA-Bestätigung", "use_ema_confirm",
                use_ema_confirm=False)
        add("alt_ema", "einfache Alternative: Detektor 'ema'",
            "detector:reactive", detector="ema")
    elif det == "kombi":
        if ec.get("kombi_pivot_accel", True):
            add("no_pivot", "ohne Umkehrpunkt-Beschleuniger",
                "kombi_pivot_accel", kombi_pivot_accel=False)
        if float(ec.get("kombi_dominance_days", 3.0) or 0) > 0:
            add("no_dominance", "ohne Trend-Dominanz", "kombi_dominance_days",
                kombi_dominance_days=0.0)
        add("alt_ema", "einfache Alternative: Detektor 'ema'",
            "detector:kombi", detector="ema")
    elif det == "ema":
        add("alt_regression", "einfache Alternative: Detektor 'regression'",
            "detector:ema", detector="regression")
    else:  # regression
        add("alt_ema", "einfache Alternative: Detektor 'ema'",
            "detector:regression", detector="ema")
    return variants[:8]


def component_verdicts(rows: List[Dict], metric: str = "inner_direction_pct",
                       fallback: str = "direction_pct",
                       margin: float = VERDICT_MARGIN_PP) -> Dict[str, Dict]:
    """Beitrag je entfernter Komponente: Differenz volle Konfiguration minus
    Ablations-Variante auf der INNEREN Validierung (positiv = Komponente
    trägt bei). Kein Holdout-Bezug – der bleibt finaler Test (AP07)."""
    full = next((r for r in rows or [] if r.get("variant_key") == FULL_KEY), None)

    def val(r):
        v = (r or {}).get(metric)
        return v if v is not None else (r or {}).get(fallback)

    out = {}
    fv = val(full)
    for r in rows or []:
        key = r.get("variant_key")
        if not key or key == FULL_KEY:
            continue
        rv = val(r)
        if fv is None or rv is None:
            out[key] = {"delta_pp": None, "verdict": "unbewertet"}
            continue
        delta = round(float(fv) - float(rv), 1)
        if delta >= margin:
            verdict = "traegt_bei"
        elif delta <= -margin:
            verdict = "schadet"
        else:
            verdict = "redundant"
        out[key] = {"delta_pp": delta, "verdict": verdict,
                    "removed": r.get("removed")}
    return out
