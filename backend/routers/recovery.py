"""Daten-Wiederherstellung (services/data_recovery.py) – Vorschau, Ausführung, Report."""
from fastapi import APIRouter, Depends, Request

from core import state
from core.audit import log_action
from core.auth import require_admin
from services import data_recovery

router = APIRouter(tags=["recovery"])


@router.get("/api/admin/recovery/preview")
async def recovery_preview(_: bool = Depends(require_admin)):
    """Nur lesen: was WÜRDE wiederhergestellt (MasterPrompt, Test-Lektionen, Paper-Trades)?"""
    return await data_recovery.run(state.db, dry_run=True)


@router.post("/api/admin/recovery/run")
async def recovery_run(request: Request, _: bool = Depends(require_admin)):
    report = await data_recovery.run(state.db, dry_run=False)
    await log_action(request, "data_recovery_run", {
        "master": (report.get("master_prompt") or {}).get("restored"),
        "lessons_removed": len((report.get("lessons") or {}).get("removed") or []),
        "trades_recovered": (report.get("paper_trades") or {}).get("recovered")})
    return report


@router.get("/api/admin/recovery/report")
async def recovery_report():
    doc = await state.db.settings.find_one({"_id": "data_recovery"}, {"_id": 0}) or {}
    return {"last_report": doc.get("last_report")}
