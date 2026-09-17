"""DB-Wartung: Speicher-Übersicht + Retention-Policy (Atlas-Free-Tier-Quota)."""
import logging
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services import retention

router = APIRouter(tags=["maintenance"])
logger = logging.getLogger(__name__)


def _db():
    from core import state
    if state.db is None:
        raise HTTPException(503, "DB nicht bereit")
    return state.db


@router.get("/api/maintenance/retention")
async def retention_status():
    return await retention.status(_db())


@router.get("/api/maintenance/storage")
async def storage_overview():
    return await retention.storage_stats(_db())


@router.post("/api/maintenance/retention/run")
async def retention_run(_: bool = Depends(require_admin)):
    return await retention.run_sweep(_db(), trigger="manual")


@router.post("/api/maintenance/retention/config")
async def retention_config(body: Dict, _: bool = Depends(require_admin)):
    db = _db()
    upd: Dict = {}
    if "enabled" in body:
        upd["enabled"] = bool(body["enabled"])
    if isinstance(body.get("overrides"), dict):
        known = {r["coll"] for r in retention.DEFAULT_POLICY}
        upd["overrides"] = {k: {kk: vv for kk, vv in (v or {}).items() if kk in ("days", "keep_last")}
                            for k, v in body["overrides"].items() if k in known}
    if not upd:
        raise HTTPException(400, "enabled oder overrides erforderlich")
    await db.settings.update_one({"_id": retention.CONFIG_ID}, {"$set": upd}, upsert=True)
    return await retention.status(db)
