"""AP04 (Befunde R09/R13/R04): Release-Status, Definitions-Fingerprint und
Zustandsversionen für dynamische Strategien – rein, ohne DB/Netz.

Statusmodell (additiv, alte Dokumente ohne 'release' laufen als 'legacy'
unverändert weiter – keine stillschweigende Rückwirkung auf Bestand):
  draft            Entwurf ohne bestandene Validierung (Build ohne Test ist ok)
  validated        Walkforward-Verdict bestanden UND Fingerprint passt noch
  approved         ausdrückliche menschliche Freigabe (Actor + Zeit + Fingerprint)
  stale            Definition wurde nach Validierung/Freigabe geändert
  legacy           Bestandsdokument von vor AP04 (kein Gate, nur Anzeige)

Live-Aktivierung (Apply/Auto-Apply/Confirm) ist nur für validated/approved/
legacy erlaubt; draft/stale liefern eine klare Begründung.
"""
import hashlib
import json
from typing import Dict, Optional

_SEMANTIC_FIELDS = ("strategy_id", "timeframe", "symbols", "configs",
                    "fallback_config", "regime_strategies", "regime_params",
                    "sub_strategies", "rule_variants")


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


def definition_fingerprint(doc: Dict) -> str:
    """Fingerprint über die ENTSCHEIDUNGSRELEVANTE Definition (nicht über
    settings/last_state); Modell nur über Regime-IDs/-Labels."""
    payload = {k: doc.get(k) for k in _SEMANTIC_FIELDS}
    regimes = ((doc.get("model") or {}).get("regimes")) or []
    payload["model_regimes"] = [{"id": r.get("id"), "label": r.get("label")}
                                for r in regimes]
    return hashlib.sha256(_canonical(payload).encode()).hexdigest()[:16]


def state_version(state: Dict) -> str:
    """Version des beobachteten Regime-Zustands (für CAS-Confirm, R13):
    ändert sich, sobald irgendein Symbol ein anderes Regime sieht."""
    per = {sym: (st or {}).get("regime")
           for sym, st in ((state or {}).get("per_symbol") or {}).items()
           if not (st or {}).get("error")}
    return hashlib.sha256(_canonical(per).encode()).hexdigest()[:16]


def initial_release(doc: Dict, verdict: Optional[Dict],
                    verdict_stale: bool = False) -> Dict:
    """Release-Metadaten beim Erzeugen (Save/Build). Ein bestandener, noch
    gültiger Walkforward-Verdict validiert die Revision; sonst Entwurf."""
    fp = definition_fingerprint(doc)
    validated = bool((verdict or {}).get("dynamic_better")) and not verdict_stale
    return {"status": "validated" if validated else "draft",
            "revision": 1, "fingerprint": fp,
            "verdict_stale": bool(verdict_stale) or None}


def effective_status(doc: Dict) -> str:
    rel = doc.get("release")
    if not isinstance(rel, dict):
        return "legacy"
    if rel.get("status") in ("validated", "approved") \
            and rel.get("fingerprint") != definition_fingerprint(doc):
        return "stale"
    return str(rel.get("status") or "draft")


def activation_block_reason(doc: Dict) -> Optional[str]:
    """None = Live-Aktivierung erlaubt, sonst deutsche Begründung (R09)."""
    status = effective_status(doc)
    if status in ("legacy", "validated", "approved"):
        return None
    if status == "stale":
        return ("Definition wurde nach der Validierung/Freigabe geändert – "
                "Walkforward erneut ausführen oder ausdrücklich freigeben "
                "(POST /api/dynamic/{id}/approve).")
    return ("Entwurf ohne bestandene Validierung – erst Walkforward bestehen "
            "oder ausdrücklich freigeben (POST /api/dynamic/{id}/approve).")


def approve(doc: Dict, actor: str, note: str = "") -> Dict:
    """Ausdrückliche menschliche Freigabe der AKTUELLEN Definition."""
    rel = dict(doc.get("release") or {})
    rel.update({"status": "approved", "fingerprint": definition_fingerprint(doc),
                "approved_by": actor, "approved_note": note or None,
                "revision": int(rel.get("revision") or 1)})
    return rel
