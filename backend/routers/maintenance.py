"""DB-Wartung: Speicher-Übersicht + Retention-Policy (Atlas-Free-Tier-Quota)."""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services import backup, retention

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


@router.post("/api/maintenance/compact")
async def compact_run(body: Optional[Dict] = None, _: bool = Depends(require_admin)):
    colls = (body or {}).get("collections")
    if colls is not None and not (isinstance(colls, list) and all(isinstance(c, str) for c in colls)):
        raise HTTPException(400, "collections muss eine Liste von Namen sein")
    return await retention.compact(_db(), colls or None)


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


# ---- Backup (services/backup.py): täglicher Dump nach Supabase Storage ----
@router.get("/api/maintenance/backup")
async def backup_status():
    return await backup.status(_db())


@router.get("/api/maintenance/backup/list")
async def backup_list():
    return {"bucket": backup.BUCKET, "files": await backup.list_backups()}


@router.post("/api/maintenance/backup/run")
async def backup_run(_: bool = Depends(require_admin)):
    return await backup.run_backup(_db(), trigger="manual")


@router.post("/api/maintenance/backup/config")
async def backup_config(body: Dict, _: bool = Depends(require_admin)):
    db = _db()
    upd: Dict = {}
    if "enabled" in body:
        upd["enabled"] = bool(body["enabled"])
    if "retention_days" in body:
        try:
            upd["retention_days"] = max(3, min(365, int(body["retention_days"])))
        except (TypeError, ValueError):
            raise HTTPException(400, "retention_days muss eine Zahl sein")
    if isinstance(body.get("collections"), list):
        colls = [str(c) for c in body["collections"] if isinstance(c, str) and c.strip()]
        if not colls:
            raise HTTPException(400, "collections darf nicht leer sein")
        upd["collections"] = colls
    if not upd:
        raise HTTPException(400, "enabled, retention_days oder collections erforderlich")
    await db.settings.update_one({"_id": backup.CONFIG_ID}, {"$set": upd}, upsert=True)
    return await backup.status(db)


@router.post("/api/maintenance/backup/restore")
async def backup_restore(body: Dict, _: bool = Depends(require_admin)):
    """Standard: Trockenlauf (zählt nur). apply=true spielt per _id-Upsert zurück."""
    file = str(body.get("file") or "")
    if not file.startswith("backup_") or "/" in file:
        raise HTTPException(400, "file muss ein Backup-Dateiname sein (backup_…json.gz)")
    colls = body.get("collections")
    if colls is not None and not (isinstance(colls, list) and all(isinstance(c, str) for c in colls)):
        raise HTTPException(400, "collections muss eine Liste von Namen sein")
    return await backup.restore(_db(), file, colls or None, dry_run=not bool(body.get("apply")))
