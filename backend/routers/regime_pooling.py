"""Partial Pooling der Regime-Erkennung (services/regime_pooling).
Eigener Router VOR routers/regime_lab (dort fängt /api/regime-lab/{aid} sonst alles)."""
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from services import ram_queue
from services import regime_lab as lab
from services import regime_pooling as pooling

router = APIRouter(tags=["regime-pooling"])


@router.get("/api/regime-lab/pooling")
async def pooling_overview():
    """Gruppen-Analysen (mit Eignungs-Prüfung) + bisherige Pooling-Ergebnisse."""
    if state.db is None:
        raise HTTPException(status_code=503, detail="DB nicht bereit")
    return await pooling.overview(state.db)


@router.post("/api/regime-lab/pooling")
async def pooling_start(body: Dict, _: bool = Depends(require_admin)):
    """Pooling-Job starten (Cloud). Status über /api/regime-lab/status/{job_id}."""
    from routers.regime_lab import _guard_no_running
    aid = str(body.get("analysis_id") or "")
    doc = await state.db.regime_analyses.find_one({"id": aid}, {"_id": 0, "chart": 0, "chart_emas": 0,
                                                                "combined.per_symbol": 0, "per_coin": 0})
    chk = pooling.source_check(doc)
    if not chk["ok"]:
        raise HTTPException(status_code=400, detail="; ".join(chk["reasons"]))
    prior = float(body.get("prior_phases") or pooling.PRIOR_PHASES)
    if prior not in pooling.PRIOR_CHOICES:
        raise HTTPException(status_code=400, detail=f"prior_phases: {pooling.PRIOR_CHOICES}")
    _guard_no_running(body)
    params = {"analysis_id": aid, "prior_phases": prior, "execution": "cloud"}
    job_id = lab.create_job("pooling", params)
    queued = ram_queue.submit(lab.JOBS, job_id, lambda: pooling.run_pooling(job_id, params, state.db),
                              kind="regime_analysis")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}
