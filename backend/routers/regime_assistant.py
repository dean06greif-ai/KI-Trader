"""Regime-Assistent je Anlageklasse + Forschungs-Modus (06/2026).
Eigener Router VOR routers/regime_lab (dort fängt /api/regime-lab/{aid} sonst alles)."""
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from services import regime_assistant as ra
from services import regime_hierarchy as rh
from services import research_mode
from services import setup_asset_class as ac

router = APIRouter(tags=["regime-assistant"])


def _db():
    if state.db is None:
        raise HTTPException(status_code=503, detail="DB nicht bereit")
    return state.db


@router.get("/api/regime-assistant/overview")
async def overview():
    """Rezepte je Anlageklasse, letzter Lauf je Klasse, Einstellungen."""
    classes = []
    for cls in ac.CLASSES:
        classes.append({"asset_class": cls, "label": ac.LABELS.get(cls, cls),
                        "recipe": rh.public_recipe(cls), "symbols": ac.symbols_of(cls),
                        "latest": ra.latest_summary(cls)})
    job = ra.running()
    return {"classes": classes, "config": ra.config(), "research_mode": research_mode.current(),
            "running_job": job and {k: job[k] for k in ("id", "status", "progress", "phase", "params")}}


@router.post("/api/regime-assistant/run")
async def run(body: Dict, _: bool = Depends(require_admin)):
    import asyncio
    cls = str(body.get("asset_class") or "")
    if cls not in ac.CLASSES:
        raise HTTPException(status_code=400, detail=f"asset_class: {ac.CLASSES}")
    if ra.running():
        raise HTTPException(status_code=409, detail="Es läuft bereits eine Assistenten-Prüfung")
    allowed = set(ac.symbols_of(cls))
    syms = [s for s in (body.get("symbols") or []) if s in allowed] or None
    days = body.get("days")
    try:
        days = int(min(max(int(days), 120), 1500)) if days else None
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days muss eine Zahl sein")
    if ra.recently_finished(cls):
        raise HTTPException(status_code=429, detail="Gerade erst geprüft – bitte kurz warten")
    params = {"asset_class": cls, "symbols": syms, "days": days}
    jid = ra.create_job(params)
    asyncio.create_task(ra.run(jid, _db()))
    return {"job_id": jid}


@router.get("/api/regime-assistant/job/{jid}")
async def job_status(jid: str):
    job = ra.JOBS.get(jid)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return job


@router.get("/api/regime-assistant/runs/latest")
async def latest_run(asset_class: str):
    doc = await _db().regime_assistant_runs.find_one({"asset_class": asset_class}, {"_id": 0},
                                                    sort=[("created_at", -1)])
    return {"run": doc}


@router.post("/api/regime-assistant/config")
async def save_config(body: Dict, _: bool = Depends(require_admin)):
    return {"config": await ra.save_config(_db(), body)}


@router.get("/api/regime-assistant/live/{symbol}")
async def live(symbol: str, refresh: Optional[bool] = False):
    return await rh.resolve(symbol, max_age_s=0 if refresh else rh.CACHE_TTL_S)


@router.get("/api/research-mode")
async def get_research_mode():
    return research_mode.current()


@router.post("/api/research-mode")
async def set_research_mode(body: Dict, _: bool = Depends(require_admin)):
    return await research_mode.save(_db(), body)
