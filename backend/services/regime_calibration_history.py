"""Kalibrierungs-Verlauf & Einstellungs-Snapshots (Regime-Lab).

Ein Verlauf für ALLE Wege, die Engine-Einstellungen verändern:
- wissenschaftliche Kalibrierung (`regime_calibrations`, Metrik: balancierter
  Richtungs-Treffer gegen die Referenz),
- Regime-Autopilot (`regime_lab_runs`, Metrik: Live=Final-Holdout-Treffer).
Beide werden hier auf EIN Zeilenformat gebracht (`report.best_config`,
`report.metric`, `source`), damit die Oberfläche jede Einstellung – auch die
des Autopiloten – jederzeit wieder übernehmen kann.

Snapshots (`regime_engine_snapshots`): vor jeder Übernahme wird der bisherige
Stand (Engine-Konfiguration + aktive Kalibrierung) gesichert -> "zurückholen"
statt Datenverlust beim Überschreiben. Reine Funktionen oben, dünne DB-Helfer unten.
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

SOURCE_CALIBRATION = "calibration"
SOURCE_AUTOPILOT = "autopilot"
METRIC_CALIBRATION = "balanced_direction_pct"
METRIC_AUTOPILOT = "holdout_direction_pct"
MAX_SNAPSHOTS = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- rein ----------------
def calibration_row(doc: Dict) -> Dict:
    """Wissenschaftliche Kalibrierung -> Verlaufszeile (Metrik explizit)."""
    rep = dict(doc.get("report") or {})
    rep.setdefault("metric", METRIC_CALIBRATION)
    return {"id": doc.get("id"), "source": SOURCE_CALIBRATION,
            "created_at": doc.get("created_at"), "symbols": doc.get("symbols") or [],
            "timeframe": doc.get("timeframe"), "report": rep}


def autopilot_row(run: Dict) -> Optional[Dict]:
    """Autopilot-Lauf -> Verlaufszeile im Kalibrierungs-Format. None ohne Konfiguration."""
    res = run.get("result") or {}
    best = res.get("best") or {}
    cfg = res.get("best_engine_config") or best.get("engine_config")
    if not cfg:
        return None
    base = res.get("baseline") or {}
    report = {"detector": best.get("detector") or str(cfg.get("detector") or "reactive"),
              "best_config": dict(cfg),
              "baseline": dict(base.get("metrics") or {}),
              "best": dict(best.get("metrics") or {}),
              "baseline_score": base.get("score"), "best_score": best.get("score"),
              "improved": bool(res.get("improved")),
              "holdout_regressed": bool(res.get("holdout_regressed")),
              "metric": METRIC_AUTOPILOT, "truth_source": "live_final",
              "total_days": res.get("days"), "evals": res.get("tested"),
              "changes": [{"key": k, "to": v} for k, v in (best.get("changes") or {}).items()]}
    return {"id": run.get("id"), "source": SOURCE_AUTOPILOT,
            "created_at": run.get("created_at") or res.get("created_at"),
            "symbols": res.get("symbols") or [], "timeframe": res.get("timeframe"),
            "report": report}


def merged_history(calibrations: List[Dict], runs: List[Dict], limit: int = 20) -> List[Dict]:
    rows = [calibration_row(c) for c in calibrations or []]
    rows += [r for r in (autopilot_row(x) for x in runs or []) if r]
    rows.sort(key=lambda r: str(r.get("created_at") or ""), reverse=True)
    return rows[:max(1, limit)]


def snapshot_doc(engine_config: Dict, calib_applied: Optional[Dict], reason: str,
                 by: str = "Admin") -> Dict:
    return {"id": f"snap_{uuid.uuid4().hex[:10]}", "engine_config": dict(engine_config or {}),
            "calib_applied": dict(calib_applied) if isinstance(calib_applied, dict) else None,
            "reason": str(reason or "")[:120], "by": by, "created_at": _now_iso()}


# ---------------- DB ----------------
async def list_history(db, limit: int = 20, include_autopilot: bool = True) -> List[Dict]:
    lim = max(1, min(int(limit), 100))
    cals = await db.regime_calibrations.find({}, {"_id": 0, "report.per_symbol": 0}) \
        .sort("created_at", -1).to_list(lim)
    runs = []
    if include_autopilot:
        runs = await db.regime_lab_runs.find(
            {"result.kind": "autopilot"}, {"_id": 0, "result.history": 0}) \
            .sort("created_at", -1).to_list(lim)
    return merged_history(cals, runs, lim)


async def save_snapshot(db, engine_config: Dict, calib_applied: Optional[Dict], reason: str) -> Dict:
    doc = snapshot_doc(engine_config, calib_applied, reason)
    await db.regime_engine_snapshots.insert_one(dict(doc))
    old = await db.regime_engine_snapshots.find({}, {"_id": 0, "id": 1}) \
        .sort("created_at", -1).skip(MAX_SNAPSHOTS).to_list(200)
    if old:
        await db.regime_engine_snapshots.delete_many({"id": {"$in": [o["id"] for o in old]}})
    return doc


async def list_snapshots(db, limit: int = 12) -> List[Dict]:
    return await db.regime_engine_snapshots.find({}, {"_id": 0}) \
        .sort("created_at", -1).to_list(max(1, min(int(limit), MAX_SNAPSHOTS)))
