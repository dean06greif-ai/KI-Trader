"""Backtest-Seeding der Playbook-Setups (KI-Trader-Modus im Backtester)."""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.utils import _job_public
from services import ram_queue
from services import setup_asset_class as ac
from services import backtester as bt
from services.setup_backtest import runner
from services.setup_backtest import auto as seed_auto

logger = logging.getLogger(__name__)

router = APIRouter(tags=["setup-backtest"])


@router.get("/api/ai/playbook/backtest")
async def seeding_overview():
    data = await runner.overview(state.db)
    job = runner.running_job()
    data["active"] = _job_public(job) if job else None
    return data


@router.post("/api/ai/playbook/backtest/run")
async def seeding_run(body: Dict, _: bool = Depends(require_admin)):
    classes = [c for c in (body.get("asset_classes") or []) if c in ac.CLASSES]
    if not classes and body.get("symbols"):
        classes = sorted({ac.asset_class_of(s) for s in body["symbols"]})
    if not classes:
        raise HTTPException(status_code=400, detail="asset_classes oder symbols erforderlich")
    if runner.running_job() or any(j["status"] == "running" for j in bt.JOBS.values()):
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Backtest")
    days = int(body.get("days") or runner.DEFAULT_DAYS)
    mode = "loop" if body.get("mode") == "loop" else "single"
    setups = body.get("setups") or None
    params = {"kind": "ai_seed", "asset_classes": classes, "days": days, "mode": mode,
              "setups": setups}
    job_id = runner.create_job(params)
    queued = ram_queue.submit(runner.JOBS, job_id, lambda: runner.run_job(
        job_id, state.db, classes, days, mode, setups), kind="ai_seed")
    return {"status": "started", "job_id": job_id, "ram_queued": queued, "params": params}


@router.get("/api/ai/playbook/backtest/status/{job_id}")
async def seeding_status(job_id: str):
    job = runner.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _job_public(job)


@router.post("/api/ai/playbook/backtest/cancel/{job_id}")
async def seeding_cancel(job_id: str, _: bool = Depends(require_admin)):
    job = runner.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    job["cancel"] = True
    job["phase"] = "Wird abgebrochen..."
    return {"status": "cancelling", "job_id": job_id}


@router.get("/api/ai/playbook/backtest/auto")
async def seeding_auto_get():
    """Automatik-Einstellungen + nächster/letzter Lauf."""
    return await seed_auto.get(state.db)


@router.post("/api/ai/playbook/backtest/auto")
async def seeding_auto_set(body: Dict, _: bool = Depends(require_admin)):
    """Automatik ein/aus, Intervall (Stunden), Zeitraum (Tage), Modus, Klassen."""
    return await seed_auto.save(state.db, body or {})


@router.post("/api/ai/playbook/backtest/reset")
async def seeding_reset(body: Dict, _: bool = Depends(require_admin)):
    cls = body.get("asset_class") or None
    if cls and cls not in ac.CLASSES:
        raise HTTPException(status_code=400, detail="unbekannte Anlageklasse")
    deleted = await runner.reset(state.db, cls, body.get("setup") or None)
    from services import ai_playbook
    ai_playbook.invalidate_cache()
    return {"status": "ok", "deleted_trades": deleted}
