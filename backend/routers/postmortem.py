"""Nachanalyse (Post-Trade-Review) – Was-wäre-wenn nach Trade-Close."""
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from core.auth import require_admin
from services.trade_postmortem import postmortem

router = APIRouter(tags=["postmortem"])
logger = logging.getLogger(__name__)


@router.get("/api/ai/postmortem/summary")
async def postmortem_summary(days: int = Query(30, ge=1, le=90),
                             mode: Optional[str] = Query(None)):
    """Setup-Übersicht (robuste Befunde, Varianten, Nachlauf) + jüngste Reviews."""
    return await postmortem.summary(days=days, mode=mode)


@router.get("/api/ai/postmortem/trade/{trade_id}")
async def postmortem_trade(trade_id: str):
    doc = await postmortem.db.trade_reviews.find_one({"trade_id": trade_id}, {"_id": 0})
    return doc or {"trade_id": trade_id, "status": "pending"}


@router.get("/api/ai/postmortem/context")
async def postmortem_context():
    """Der Prompt-Block, den der KI-Trader tatsächlich sieht (Transparenz)."""
    return {"text": await postmortem.context_text()}


@router.post("/api/ai/postmortem/run")
async def postmortem_run(_: bool = Depends(require_admin)):
    """Ausstehende Reviews sofort verarbeiten (sonst alle 10 min automatisch)."""
    return await postmortem.run_pending(limit=40)
