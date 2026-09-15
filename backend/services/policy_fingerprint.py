"""Policy-Fingerprint (Audit 3.1): eindeutige Versions-Kennung der Entscheidungs-Policy.

Jede ai_decision und jeder KI-Trade trägt `policy_version` =
{prompt_hash, lessons_hash, playbook_version, model, gate_version, sizing_hash, combined}.
Auswertungen (Audit 3.4) gruppieren nach `combined` statt nach Zeitfenstern –
so ist je Trade nachweisbar, unter WELCHEM Policy-Stand er entstand.
Reine Funktionen (unit-testbar ohne Netzwerk); Datenbeschaffung beim Aufrufer (ai_engine).
"""
import hashlib
import json
from typing import Dict, List, Optional

# Sizing-relevante Engine-Settings (Positionsgröße/Hebel) – Änderung = neue Policy
SIZING_KEYS = (
    "sizing_mode", "risk_per_trade_pct", "risk_max_margin_pct", "risk_max_leverage",
    "risk_conviction_floor", "risk_stack_reductions", "risk_collection_scale",
    "max_capital_per_trade", "lev_mode", "lev_fixed", "lev_auto_max",
    "swing_max_leverage",
)

# Inhaltstragende Lektions-Felder (Zeitstempel/Zähler ändern die Policy nicht)
_LESSON_KEYS = ("title", "detail", "weight")

# AP09/T05: entscheidungsrelevante NICHT-Sizing-Configs – Änderung = neue Policy
# (eigener Config-Hash, gehört ausdrücklich NICHT in den Sizing-Hash).
POLICY_CONFIG_KEYS = (
    "min_confidence", "collection_min_confidence", "fee_guard_enabled",
    "fee_guard_mult", "fee_guard_atr_mult", "fee_guard_crv_relax",
    "live_gate_bypass_enabled",
)

# Einzelteile des Fingerprints (Reihenfolge = Anzeige-/Hash-Reihenfolge)
PART_KEYS = ("prompt_hash", "lessons_hash", "playbook_version",
             "model", "gate_version", "sizing_hash")
# Schema 2 (AP09): zusätzlich Policy-Config-Hash + Regime-Artifact
PART_KEYS_V2 = PART_KEYS + ("config_hash", "regime_artifact")


def short_hash(obj) -> str:
    """Stabiler 10-Zeichen-Hash über die kanonische JSON-Form (Key-sortiert)."""
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]


def lessons_hash(lessons: Optional[List[Dict]]) -> str:
    """Hash über den INHALT der Lektionen (title/detail/weight), sortiert nach Titel."""
    rows = sorted(
        [{k: l.get(k) for k in _LESSON_KEYS}
         for l in (lessons or []) if isinstance(l, dict)],
        key=lambda r: str(r.get("title") or ""))
    return short_hash(rows)


def sizing_hash(config: Optional[Dict]) -> str:
    """Hash über die sizing-relevanten Engine-Settings (SIZING_KEYS)."""
    cfg = config or {}
    return short_hash({k: cfg.get(k) for k in SIZING_KEYS})


def policy_config_hash(config: Optional[Dict]) -> str:
    """AP09/T05: Hash über entscheidungsrelevante Nicht-Sizing-Configs."""
    cfg = config or {}
    return short_hash({k: cfg.get(k) for k in POLICY_CONFIG_KEYS})


def build(prompt_hash: str = "", lessons_h: str = "", playbook_version: str = "",
          model: Optional[str] = None, gate_version=None, sizing_h: str = "",
          policy_config_h: str = "", regime_artifact: Optional[str] = None) -> Dict:
    """Fingerprint-Dokument bauen; `combined` = Gruppierungs-Schlüssel.

    AP09/T05 additiv: mit policy_config_h/regime_artifact entsteht
    `schema=2` (erweiterter combined). Alt-Aufrufer ohne die neuen Teile
    erhalten unverändert den Schema-1-Fingerprint – gespeicherte Alttrades
    bleiben dadurch stabil gruppierbar."""
    try:
        gv = int(gate_version) if gate_version is not None else None
    except (TypeError, ValueError):
        gv = None
    fp = {
        "prompt_hash": str(prompt_hash or ""),
        "lessons_hash": str(lessons_h or ""),
        "playbook_version": str(playbook_version or ""),
        "model": (str(model or "") or None),
        "gate_version": gv,
        "sizing_hash": str(sizing_h or ""),
    }
    if policy_config_h or regime_artifact:
        fp["config_hash"] = str(policy_config_h or "")
        fp["regime_artifact"] = (str(regime_artifact or "") or None)
        fp["schema"] = 2
        fp["combined"] = short_hash({k: fp.get(k) for k in PART_KEYS_V2})
    else:
        fp["combined"] = short_hash({k: fp[k] for k in PART_KEYS})
    return fp


def group_key(fp: Optional[Dict]) -> str:
    """Gruppierungs-Schlüssel für Auswertungen ('' = Policy unbekannt/Alt-Trade)."""
    return str((fp or {}).get("combined") or "")
