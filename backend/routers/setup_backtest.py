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
from services.setup_backtest import edges

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
    mode = runner.normalize_mode(body.get("mode"))
    setups = body.get("setups") or None
    ai = runner.ai_options(body)
    params = {"kind": "ai_seed", "asset_classes": classes, "days": days, "mode": mode,
              "setups": setups, **ai}
    job_id = runner.create_job(params)
    queued = ram_queue.submit(runner.JOBS, job_id, lambda: runner.run_job(
        job_id, state.db, classes, days, mode, setups, ai=ai), kind="ai_seed")
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


# ---- Edge-Register (dauerhafte Edges, Rollback, rückwirkende Wiederherstellung) ----
@router.get("/api/ai/playbook/backtest/edges")
async def seeding_edges(asset_class: str = None, setup: str = None):
    if asset_class and asset_class not in ac.CLASSES:
        raise HTTPException(status_code=400, detail="unbekannte Anlageklasse")
    items = await edges.list_edges(state.db, asset_class or None, setup or None)
    return {"edges": items, "rules": {"stale_max": edges.STALE_MAX, "replace_margin": edges.REPLACE_MARGIN,
                                      "min_trades_ratio": edges.MIN_TRADES_RATIO,
                                      "oos_is_consistency": edges.OOS_IS_CONSISTENCY, "min_pf": edges.MIN_PF,
                                      "wr_crv_margin": edges.WR_CRV_MARGIN}}


@router.post("/api/ai/playbook/backtest/edges/activate")
async def seeding_edge_activate(body: Dict, _: bool = Depends(require_admin)):
    """Rollback: einen gespeicherten Edge wieder aktiv setzen (Parameter, Stand,
    OOS-Trades fürs Reife-Gate)."""
    cls, sid, edge_id = body.get("asset_class"), body.get("setup"), body.get("edge_id")
    if cls not in ac.CLASSES or not sid or not edge_id:
        raise HTTPException(status_code=400, detail="asset_class, setup und edge_id erforderlich")
    if runner.running_job():
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Backtest")
    try:
        res = await edges.activate(state.db, cls, sid, edge_id, reason="manuell (Rollback)")
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    from services import ai_playbook
    ai_playbook.invalidate_cache()
    return {"status": "ok", **res}


@router.post("/api/ai/playbook/backtest/edges/recover")
async def seeding_edge_recover(body: Dict = None, _: bool = Depends(require_admin)):
    """Rückwirkend: früher bestandene Parameter-Sätze aus dem Verlauf ins
    Register holen; Setups ohne Edge bekommen den robustesten davon zurück."""
    if runner.running_job():
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Backtest")
    res = await edges.recover_from_history(state.db, activate_best=bool((body or {}).get("activate_best", True)))
    from services import ai_playbook
    ai_playbook.invalidate_cache()
    return {"status": "ok", **res}
