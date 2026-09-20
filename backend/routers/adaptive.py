"""Adaptive KI-Bausteine: Aktivitäts-Wächter, Bewegungs-Scanner, Setup-Trigger."""
import logging
from typing import Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services.activity_guard import activity_guard
from services.ai_move_scanner import move_scanner
from services.setup_trigger import setup_trigger

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


@router.post("/api/ai/activity-guard/idea-round")
async def activity_guard_idea_round(_: bool = Depends(require_admin)):
    """Stufe 4 manuell: LLM-Ideen-Runde (Setup-Lücke) sofort ausführen."""
    _ready(activity_guard)
    return await activity_guard.run_idea_round()


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


@router.get("/api/ai/setup-trigger")
async def setup_trigger_status():
    """Setup-Trigger (services/setup_trigger.py): Backtest-Detektoren live."""
    _ready(setup_trigger)
    return setup_trigger.status()


@router.post("/api/ai/setup-trigger/run")
async def setup_trigger_run(body: Optional[Dict] = None, _: bool = Depends(require_admin)):
    """Manueller Scan (Detektoren auf der letzten geschlossenen 5m-Kerze)."""
    _ready(setup_trigger)
    symbol = (body or {}).get("symbol")
    return await setup_trigger.scan(symbols=[symbol] if symbol else None)


@router.get("/api/ai/setup-trigger/hits/{symbol}")
async def setup_trigger_hits(symbol: str, window_min: int = 90):
    """Welche Setups haben für das Symbol im Fenster gefeuert (ohne LLM)?"""
    _ready(setup_trigger)
    return {"symbol": symbol, "window_min": window_min,
            "hits": setup_trigger.detector_hits(symbol.upper(), max(5, min(1440, window_min)))}
