"""Bestehende Regime (gespeicherte Analysen) in den Autopilot-Verlauf übernehmen.

Der Autopilot-Verlauf (regime_lab_runs, result.kind="autopilot") kennt nur Läufe
seit es ihn gibt. Gespeicherte Regime-Analysen tragen aber dieselbe Erkennung
(settings.engine_config) und dieselben Live=Final-/Referenz-Kennzahlen je Symbol.
Hier werden sie als „importierte“ Verlaufszeilen gesichert:
- gleiche Form wie ein Autopilot-Ergebnis -> übernehmen / Regime suchen /
  Referenz / merken funktionieren unverändert,
- gemerkt (pinned) -> die Datenbank-Bereinigung löscht sie nie, auch wenn die
  Analyse selbst später aus dem 8er-Limit fällt (Sicherung der Erkennung),
- idempotent (ID = imp_<Analyse-ID>), Kennzahlen/Score mit exakt derselben
  Rechnung wie der Autopilot (regime_autopilot.MetricsAccumulator/score_metrics).
"""
import logging
from typing import Dict, List, Optional

from services import regime_autopilot as ap
from services import research_validation
from services.regime_quality import SWEET_SPOT_DAYS

logger = logging.getLogger(__name__)

SOURCE = "import"
ID_PREFIX = "imp_"
TARGET_MAX_DAYS = 15.0          # Standard-Sweet-Spot oben wie in der Oberfläche
SCAN_LIMIT = 60
# Nur die Felder, die für Erkennung + Kennzahlen nötig sind (Charts sind MB-groß)
PROJECTION = {"_id": 0, "id": 1, "name": 1, "symbols": 1, "timeframe": 1, "days": 1,
              "created_at": 1, "settings": 1, "scope": 1, "regimes": 1,
              "combined.per_symbol": 1, "combined.model.regimes": 1}


def run_id_for(analysis_id: str) -> str:
    return f"{ID_PREFIX}{analysis_id}"


def analysis_to_run(doc: Dict) -> Optional[Dict]:
    """Gespeicherte Analyse -> Autopilot-Verlaufszeile (rein). None, wenn keine
    Live-Erkennung vorliegt (K-Means/Regression oder ohne Kennzahlen)."""
    st = doc.get("settings") or {}
    if str(st.get("engine") or "v2").lower() != "v2":
        return None
    # Leere engine_config = Standard-Erkennung (ältere Analysen mit Standardwerten
    # wurden bisher übersprungen, obwohl sie eine vollwertige Live-Erkennung haben)
    cfg = dict(st.get("engine_config") or {}) or {"detector": ap.eng.DEFAULT_CONFIG.get("detector", "reactive")}
    if str(cfg.get("detector") or "reactive").lower() not in ap.DETECTORS:
        return None
    cfg["detector"] = ap.detector_of(cfg)
    tf = doc.get("timeframe") or "1h"
    per_symbol = ((doc.get("combined") or {}).get("per_symbol")) or \
        {s: pc for s, pc in (doc.get("per_coin") or {}).items() if isinstance(pc, dict) and not pc.get("error")}
    acc = ap.MetricsAccumulator(tf)
    for entry in per_symbol.values():
        if isinstance(entry, dict) and entry.get("live_agreement"):
            acc.add(entry)
    if not acc.agg["direction_pct"]:
        return None
    metrics = acc.result()
    lo, hi = SWEET_SPOT_DAYS[0], TARGET_MAX_DAYS
    score = ap.score_metrics(metrics, lo, hi)
    best = {"engine_config": cfg, "metrics": metrics, "score": score, "detector": cfg["detector"],
            "baseline_score": score, "changes": {}}
    created = doc.get("created_at")
    result = {"kind": "autopilot", "source": SOURCE,
              "imported_from": {"type": "analysis", "id": doc.get("id"), "name": doc.get("name"),
                                "grade": _grade(doc), "regimes": _n_regimes(doc),
                                "analysis_created_at": created},
              "best": best, "best_engine_config": cfg,
              "baseline": {"engine_config": cfg, "metrics": metrics, "score": score},
              "improved": False, "improvements": 0, "tested": 0, "history": [],
              "stop_reason": "imported", "holdout_regressed": False, "inner_regressed": False,
              "evidence": research_validation.evidence_verdict(metrics.get("holdout_bars")),
              "settings": {"min_phase_days_target": lo, "max_phase_days_target": hi},
              "symbols": list(doc.get("symbols") or list(per_symbol.keys())),
              "timeframe": tf, "days": doc.get("days"),
              "train_pct": st.get("train_pct"), "created_at": created}
    return {"id": run_id_for(doc.get("id")), "result": result, "created_at": created,
            "pinned": True}


def _grade(doc: Dict) -> Optional[str]:
    """Erkennungs-Note der Analyse („sehr gut“ …) – fail-open."""
    try:
        from services import regime_quality
        return regime_quality.grade_for_classes(doc)
    except Exception:  # noqa: BLE001
        return None


def _n_regimes(doc: Dict) -> Optional[int]:
    regs = ((doc.get("combined") or {}).get("model") or {}).get("regimes") or doc.get("regimes") or []
    return len(regs) or None


def same_detection_key(result: Dict) -> str:
    """Erkennung + Timeframe + Coins (rein) – erkennt Analysen, die nur die
    Folge-Analyse eines schon vorhandenen Autopilot-Laufs sind."""
    cfg = result.get("best_engine_config") or (result.get("best") or {}).get("engine_config") or {}
    return f"{ap.config_key(cfg)}|{result.get('timeframe')}|{','.join(sorted(result.get('symbols') or []))}"


async def import_existing(db, limit: int = SCAN_LIMIT) -> Dict:
    """Alle gespeicherten Analysen sichern, die noch nicht im Verlauf sind."""
    rows = await db.regime_lab_runs.find(
        {"result.kind": "autopilot"},
        {"_id": 0, "id": 1, "pinned": 1, "result.best_engine_config": 1, "result.best.engine_config": 1,
         "result.timeframe": 1, "result.symbols": 1}).to_list(2000)
    have = {r["id"] for r in rows}
    # Nur GEMERKTE Läufe ersetzen eine Analyse: ungemerkte Autopilot-Läufe löscht
    # die Datenbank-Bereinigung (40er-Limit) – sonst ginge die Analyse verloren.
    known = {same_detection_key(r.get("result") or {}) for r in rows if r.get("pinned")}
    cursor = db.regime_analyses.find({}, PROJECTION).sort("created_at", -1).limit(limit)
    imported: List[str] = []
    skipped = already = 0
    async for doc in cursor:
        if doc.get("scope") == "per_coin" and not (doc.get("combined") or {}).get("per_symbol"):
            extra = await db.regime_analyses.find_one({"id": doc.get("id")}, {"_id": 0, "per_coin": 1})
            doc = {**doc, **(extra or {})}
        if run_id_for(doc.get("id")) in have:
            already += 1
            await _refresh_meta(db, doc)
            continue
        run = analysis_to_run(doc)
        if not run:
            skipped += 1
            continue
        if same_detection_key(run["result"]) in known:
            already += 1   # gleiche Erkennung liegt schon als Autopilot-Lauf im Verlauf
            continue
        known.add(same_detection_key(run["result"]))
        await db.regime_lab_runs.update_one({"id": run["id"]}, {"$setOnInsert": run}, upsert=True)
        imported.append(doc.get("name") or doc.get("id"))
    logger.info(f"Autopilot-Verlauf: {len(imported)} Analysen importiert, "
                f"{already} schon vorhanden, {skipped} ohne Live-Erkennung")
    return {"imported": len(imported), "already": already, "skipped": skipped,
            "names": imported[:20]}


async def _refresh_meta(db, doc: Dict) -> None:
    """Bereits importierte Zeilen um Note/Regime-Zahl ergänzen (Alt-Importe)."""
    try:
        await db.regime_lab_runs.update_one(
            {"id": run_id_for(doc.get("id"))},
            {"$set": {"result.imported_from.grade": _grade(doc),
                      "result.imported_from.regimes": _n_regimes(doc)}})
    except Exception:  # noqa: BLE001
        pass
