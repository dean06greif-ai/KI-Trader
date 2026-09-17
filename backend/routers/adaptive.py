"""Adaptive KI-Bausteine: Aktivitäts-Wächter + Bewegungs-Scanner."""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services.activity_guard import activity_guard
from services.ai_move_scanner import move_scanner

router = APIRouter(tags=["adaptive"])
logger = logging.getLogger(__name__)


def _ready(obj):
    if obj.engine is None or obj.db is None:
        raise HTTPException(503, "Engine nicht bereit")


@router.get("/api/ai/activity-guard")
async def activity_guard_status():
    _ready(activity_guard)
    return await activity_guard.status()


@router.post("/api/ai/activity-guard/config")
async def activity_guard_config(body: Dict, _: bool = Depends(require_admin)):
    _ready(activity_guard)
    return await activity_guard.save_config(body or {})


@router.post("/api/ai/activity-guard/check")
async def activity_guard_check(_: bool = Depends(require_admin)):
    _ready(activity_guard)
    return await activity_guard.check(manual=True)


@router.get("/api/ai/move-scanner")
async def move_scanner_status():
    _ready(move_scanner)
    return await move_scanner.status()


@router.post("/api/ai/move-scanner/config")
async def move_scanner_config(body: Dict, _: bool = Depends(require_admin)):
    _ready(move_scanner)
    return await move_scanner.save_config(body or {})


@router.post("/api/ai/move-scanner/run")
async def move_scanner_run(body: Optional[Dict] = None, _: bool = Depends(require_admin)):
    _ready(move_scanner)
    symbol = (body or {}).get("symbol")
    return await move_scanner.scan(manual=True, symbol=symbol)
