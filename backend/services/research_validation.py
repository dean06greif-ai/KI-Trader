"""AP07 (Befunde R05/R09/R10): Forschungsvalidierung ohne Holdout-Tuning.

Reine Kernfunktionen + persistenter Versuchszähler:
- Auswahl von Kandidaten (EMA-Periode, Kombi-Parameter) erfolgt auf der
  INNEREN Validierung (letzter Teil des Trainingsfensters) – NIE auf dem
  Holdout. Der Holdout ist ausschließlich abschließender Test (final_test).
- Jeder Suchlauf/Holdout-Blick wird gezählt (research_attempts) – wiederholte
  manuelle Suche auf demselben sichtbaren Holdout wird dadurch sichtbar.
- Zu wenig Holdout-Evidenz wird als `insufficient_evidence` benannt statt
  stillschweigend als Ergebnis verkauft.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

INNER_FRACTION = 0.25          # letzter Anteil des Trainingsfensters
MIN_HOLDOUT_BARS = 50          # darunter: insufficient_evidence


def inner_anchor_ts(candles: List[Dict], cut: int,
                    frac: float = INNER_FRACTION) -> Optional[int]:
    """Zeitanker, AB dem (exklusiv) die innere Validierung beginnt.
    cut = erster Index NACH dem Trainingsfenster. None wenn zu wenig Daten."""
    if not candles or cut < 8:
        return None
    idx = max(int(cut * (1.0 - frac)), 1)
    if idx >= cut:
        return None
    return int(candles[idx - 1]["timestamp"])


def select_best_row(rows: List[Dict], metric: str = "inner_direction_pct",
                    fallback: str = "direction_pct",
                    key_field: str = None) -> Tuple[Optional[Dict], str]:
    """Beste Zeile NACH INNERER VALIDIERUNG wählen – niemals nach dem Holdout
    (R10). Fallback (Altdaten ohne inneres Fenster): Trainingsmetrik, klar
    benannt. Rückgabe: (row|None, selection_basis)."""
    cand = [r for r in rows if r.get(metric) is not None]
    if cand:
        return max(cand, key=lambda r: r[metric]), "inner_validation"
    cand = [r for r in rows if r.get(fallback) is not None]
    if cand:
        return max(cand, key=lambda r: r[fallback]), "train_only"
    return None, "none"


def evidence_verdict(holdout_bars: Optional[int],
                     min_bars: int = MIN_HOLDOUT_BARS) -> str:
    if not holdout_bars or int(holdout_bars) < min_bars:
        return "insufficient_evidence"
    return "ok"


def experiment_manifest(kind: str, params: Dict, dataset: Dict = None) -> Dict:
    """Experiment-Steckbrief VOR Suchstart: Fragestellung/Suchraum/Datenbezug.
    Wird am Ergebnis gespeichert (Provenienz, R10-Versuchstransparenz)."""
    return {"kind": kind, "params": dict(params or {}),
            "dataset_ref": ({sym: {"start_ts": d.get("start_ts"),
                                   "end_ts": d.get("end_ts"),
                                   "hash": d.get("hash")}
                             for sym, d in (dataset or {}).get(
                                 "per_symbol", {}).items()}
                            if dataset else None),
            "holdout_role": "final_test",
            "selection_rule": "inner_validation",
            "created_at": datetime.now(timezone.utc).isoformat()}


async def register_attempt(db, scope: str, kind: str) -> Optional[int]:
    """Persistenter Versuchszähler je Suchziel (z.B. Analyse+Bereich).
    Gibt die laufende Versuchsnummer zurück; fail-safe (None bei DB-Fehler)."""
    if db is None:
        return None
    try:
        now = datetime.now(timezone.utc).isoformat()
        await db.research_attempts.update_one(
            {"scope": scope},
            {"$inc": {"attempts": 1},
             "$set": {"kind": kind, "last_at": now}},
            upsert=True)
        doc = await db.research_attempts.find_one({"scope": scope})
        return int((doc or {}).get("attempts") or 0)
    except Exception:  # noqa: BLE001
        return None


def walkforward_status(doc: Dict) -> Dict:
    """AP10/R17: differenzierter Walk-Forward-Status je Analyse.

    passed: True/False/None (kein WF vorhanden) über alle Scope-Ergebnisse.
    stale:  True, wenn eine Strategie-Zuordnung im getesteten Scope NACH dem
            Walk-Forward geändert/bestätigt wurde – das gespeicherte Ergebnis
            beschreibt dann nicht mehr die aktuelle Zusammenstellung."""
    wfs = {k: w for k, w in (doc.get("walkforward") or {}).items()
           if isinstance(w, dict)}
    if not wfs:
        return {"passed": None, "stale": False}
    passed = all(bool((w.get("verdict") or {}).get("dynamic_better"))
                 for w in wfs.values())
    assignments = doc.get("assignments") or {}
    stale = False
    for key, w in wfs.items():
        created = str(w.get("created_at") or "")
        if not created:
            continue
        for akey, a in assignments.items():
            if not str(akey).startswith(f"{key}:"):
                continue
            if str((a or {}).get("assigned_at") or "") > created:
                stale = True
    return {"passed": passed, "stale": stale}


def walkforward_status_for(doc: Dict, key: str) -> Dict:
    """Wie walkforward_status, aber konsistent NUR für den ausgewählten
    Testbereich (z.B. 'combined' oder 'coin:BTCUSDT') – das Beweispaket darf
    passed/stale nicht aus einem anderen Scope ableiten als dem, den es zeigt."""
    wfs = {k: w for k, w in (doc.get("walkforward") or {}).items()
           if isinstance(w, dict)}
    w = wfs.get(key)
    if not w:
        return {"passed": None, "stale": False}
    passed = bool((w.get("verdict") or {}).get("dynamic_better"))
    stale = False
    created = str(w.get("created_at") or "")
    if created:
        for akey, a in (doc.get("assignments") or {}).items():
            if not str(akey).startswith(f"{key}:"):
                continue
            if str((a or {}).get("assigned_at") or "") > created:
                stale = True
    return {"passed": passed, "stale": stale}
