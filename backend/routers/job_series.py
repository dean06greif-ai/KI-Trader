"""Nacht-Serie / Job-Warteschlange: Backtests, Optimierungen, Regime-Analysen
als Serie einplanen (services/job_series.py)."""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from services import job_series

logger = logging.getLogger(__name__)

router = APIRouter(tags=["series"])


@router.get("/api/series")
async def series_list():
    """Alle Einträge (wartend/laufend/fertig) + Serien-Status."""
    items = await job_series.list_items(state.db)
    st = await job_series.get_state(state.db)
    ok, why = job_series.may_start(st)
    return {"items": items, "state": st, "may_start": ok, "wait_reason": why,
            "external_job_running": job_series.any_job_running(),
            "current_id": job_series.current_item_id(),
            "queued": sum(1 for i in items if i["status"] == "queued")}


@router.post("/api/series/add")
async def series_add(body: Dict, _: bool = Depends(require_admin)):
    """Eintrag hinzufügen: {kind: backtest|optimizer|regime_analysis, body: <Request
    wie beim direkten Start>, label?: str}."""
    try:
        item = await job_series.add_item(state.db, body.get("kind"), body.get("body"),
                                         body.get("label"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", "item": item}


@router.post("/api/series/state")
async def series_state(body: Dict, _: bool = Depends(require_admin)):
    """Serie steuern: paused, start_at (ISO oder null = sofort), notify_each, notify_done."""
    try:
        st = await job_series.update_state(state.db, body)
    except ValueError:
        raise HTTPException(status_code=400, detail="start_at muss ISO-Datum/Zeit sein")
    return {"status": "success", "state": st}


@router.post("/api/series/reorder")
async def series_reorder(body: Dict, _: bool = Depends(require_admin)):
    ids = body.get("ids") or []
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="ids (Liste) erforderlich")
    return {"status": "success", "reordered": await job_series.reorder(state.db, ids)}


@router.delete("/api/series/finished")
async def series_clear_finished(_: bool = Depends(require_admin)):
    return {"status": "success", "removed": await job_series.clear_finished(state.db)}


@router.delete("/api/series/{item_id}")
async def series_remove(item_id: str, _: bool = Depends(require_admin)):
    """Eintrag löschen (laufender Job wird abgebrochen)."""
    if not await job_series.remove_item(state.db, item_id):
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    return {"status": "success"}


@router.get("/api/series/{item_id}/result")
async def series_result(item_id: str):
    """Vollständiges Ergebnis eines fertigen Eintrags (aus den bestehenden
    Ergebnis-Collections) – für die Detail-Ansicht / 'Als Strategie übernehmen'."""
    doc = await state.db.job_series.find_one({"id": item_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Eintrag nicht gefunden")
    job_id = doc.get("job_id")
    result = None
    if job_id:
        jobs = job_series._jobs_for(doc["kind"])
        job = jobs.get(job_id)
        if job and job.get("result"):
            result = job["result"]
        else:
            result = await job_series._result_from_db(state.db, doc["kind"], job_id)
    if result is None and doc["kind"] == "regime_analysis" and (doc.get("summary") or {}).get("analysis_id"):
        result = {"kind": "analysis", "analysis_id": doc["summary"]["analysis_id"]}
    return {"item": job_series._clean(doc), "result": result}
