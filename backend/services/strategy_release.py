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


def revised_release(prev: Optional[Dict], doc: Dict,
                    verdict: Optional[Dict] = None) -> Dict:
    """Release bei erneutem Build/Edit (R09): unveränderte Definition behält
    ihren Release; geänderte Definition erzeugt eine NEUE Revision – alte
    Validierung/Freigabe gilt für die neue Definition nicht weiter."""
    fp = definition_fingerprint(doc)
    if isinstance(prev, dict) and prev.get("fingerprint") == fp:
        return dict(prev)
    rel = initial_release(doc, verdict)
    rel["revision"] = int((prev or {}).get("revision") or 0) + 1
    return rel


def approve(doc: Dict, actor: str, note: str = "") -> Dict:
    """Ausdrückliche menschliche Freigabe der AKTUELLEN Definition."""
    rel = dict(doc.get("release") or {})
    rel.update({"status": "approved", "fingerprint": definition_fingerprint(doc),
                "approved_by": actor, "approved_note": note or None,
                "revision": int(rel.get("revision") or 1)})
    return rel


# ---------------- AP12: Beweispaket für die gestufte Abnahme ----------------
def _wf_for_doc(doc: Dict, analysis: Optional[Dict]):
    """Passendes Walk-Forward-Ergebnis der Quell-Analyse zum Scope der
    dynamischen Strategie (per_coin vor combined)."""
    wfs = {k: w for k, w in ((analysis or {}).get("walkforward") or {}).items()
           if isinstance(w, dict)}
    if not wfs:
        return None, None
    syms = doc.get("symbols") or []
    if len(syms) == 1 and f"coin:{syms[0]}" in wfs:
        key = f"coin:{syms[0]}"
    elif "combined" in wfs:
        key = "combined"
    else:
        key = sorted(wfs)[0]
    return key, wfs.get(key)


def evidence_bundle(doc: Dict, analysis: Optional[Dict] = None,
                    safety: Optional[Dict] = None) -> Dict:
    """AP12: Beweispaket einer dynamischen Strategie – rein, read-only.

    Bündelt Datensatz-/Release-/Validierungs-/Anwendungs-/Runtime-Belege und
    benennt Blocker in Klartext. `ready_for_live` ist eine EMPFEHLUNG für die
    menschliche Freigabe, kein automatischer Schalter; ein guter Profitfaktor
    allein genügt bewusst nicht (Blocker-Liste muss leer sein)."""
    from services import research_dataset, research_validation
    status = effective_status(doc)
    blockers = []
    if status == "draft":
        blockers.append("Release ist Entwurf – Walkforward bestehen oder ausdrücklich freigeben")
    elif status == "stale":
        blockers.append("Definition nach Validierung/Freigabe geändert – erneut validieren/freigeben")
    elif status == "legacy":
        blockers.append("Bestandsdokument ohne Release-Validierungsnachweis (legacy)")

    aid = ((doc.get("settings") or {}).get("analysis_id"))
    if analysis is None:
        ds_status = "missing_analysis"
        if aid:
            blockers.append("Quell-Analyse nicht (mehr) vorhanden – Datenherkunft unbelegt")
        else:
            blockers.append("Keine Quell-Analyse verknüpft – Datenherkunft unbelegt")
    else:
        ds_status = research_dataset.dataset_status(analysis)
        if ds_status != "pinned":
            blockers.append("Datensatz nicht gepinnt – Analyse nicht exakt reproduzierbar")

    wf_key, wf = _wf_for_doc(doc, analysis)
    wf_state = (research_validation.walkforward_status(analysis)
                if analysis else {"passed": None, "stale": False})
    if wf is None:
        blockers.append("Kein finaler Walk-Forward auf dem Holdout")
    else:
        if not (wf.get("verdict") or {}).get("dynamic_better"):
            blockers.append("Walk-Forward nicht bestanden (Benchmark war besser)")
        if wf_state.get("stale"):
            blockers.append("Walk-Forward veraltet – Zuordnungen wurden danach geändert")

    app = doc.get("application_status") or {}
    if app.get("status") == "failed":
        blockers.append("Letzte Übernahme fehlgeschlagen – Retry ausstehend")
    elif app.get("status") == "blocked":
        blockers.append(f"Übernahme blockiert: {app.get('error') or 'Release-Gate'}")

    level = (safety or {}).get("level")
    if level == "critical":
        blockers.append("Sicherheitsstatus CRITICAL – Runtime-Health nicht abnahmefähig")

    return {"schema": 1,
            "strategy": {"id": doc.get("id"), "name": doc.get("name"),
                         "timeframe": doc.get("timeframe"),
                         "symbols": doc.get("symbols") or [],
                         "created_at": doc.get("created_at")},
            "release": {**(doc.get("release") or {}), "status": status,
                        "fingerprint_current": definition_fingerprint(doc)},
            "dataset": {"status": ds_status, "analysis_id": aid,
                        "manifest": (analysis or {}).get("dataset")},
            "validation": {"walkforward_key": wf_key,
                           "passed": wf_state.get("passed"),
                           "stale": wf_state.get("stale"),
                           "label_basis": (wf or {}).get("label_basis"),
                           "attempt_no": (wf or {}).get("attempt_no"),
                           "tested_at": (wf or {}).get("created_at"),
                           "dynamic_test": (wf or {}).get("dynamic_test"),
                           "best_single": ((wf or {}).get("best_single") or {}).get("label")},
            "application": app or None,
            "runtime_health": {"level": level} if safety else None,
            "ready_for_live": not blockers,
            "blockers": blockers}
