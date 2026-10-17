"""Confluence-Endpoints: Config, Events, Trade-Statistik (Vergleich)."""
from typing import Dict

from fastapi import APIRouter, Depends

from core import state
from core.auth import require_admin
from services import confluence

router = APIRouter(tags=["confluence"])


@router.get("/api/confluence/config")
async def get_config():
    return await confluence.get_config(state.db)


@router.post("/api/confluence/config")
async def save_config(body: Dict, _: bool = Depends(require_admin)):
    return await confluence.save_config(state.db, body or {})


@router.get("/api/confluence/events")
async def events(limit: int = 50):
    return {"events": await confluence.recent_events(state.db, limit)}


@router.get("/api/confluence/stats")
async def stats():
    return await confluence.trade_stats(state.db)
