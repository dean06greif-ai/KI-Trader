"""Event-Setups gebündelt für den Backtester: Übersicht, Batch-Backtest mit
Event-Auswahl + KI-Schleife, Live-Opt-in & Reset der KI-Parameter.
Die bestehenden Einzel-Endpunkte (/api/fomc/*, /api/econ/*) bleiben unverändert."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services import econ_event, fomc_event
from services import event_backtest_loop as loop

router = APIRouter(tags=["event-setups"])
logger = logging.getLogger(__name__)


def _db():
    from core import state
    if state.db is None:
        raise HTTPException(503, "DB nicht bereit")
    return state.db


def _check_key(key: str) -> str:
    if key not in loop.EVENT_KEYS:
        raise HTTPException(404, f"Unbekanntes Event '{key}' ({'|'.join(loop.EVENT_KEYS)})")
    return key


@router.get("/api/event-setups/overview")
async def event_setups_overview():
    db = _db()
    snaps = loop.status_snapshots()
    for key, snap in snaps.items():
        snap["backtest"] = await loop.last_result_light(db, key)
        snap["backtest_running"] = loop.event_running(key)
        snap["ai_params"] = await loop.stored_params_meta(db, key)
    return {"events": snaps, "job": loop.job_public(),
            "event_keys": list(loop.EVENT_KEYS), "max_rounds": loop.MAX_ROUNDS}


@router.post("/api/event-setups/backtest")
async def event_setups_backtest(body: dict, _: bool = Depends(require_admin)):
    events = [e for e in (body.get("events") or []) if e in loop.EVENT_KEYS]
    if not events:
        raise HTTPException(400, "Mindestens ein Event wählen (fomc|cpi|nfp|ppi|pce)")
    if loop.job_running() or any(loop.event_running(k) for k in events):
        return {"status": "busy", "detail": "Es läuft bereits ein Event-Backtest"}
    years = max(0.5, min(2.5, float(body.get("years", 2.0) or 2.0)))
    ai_revise = bool(body.get("ai_revise"))
    ai_rounds = max(1, min(loop.MAX_ROUNDS, int(body.get("ai_rounds", 2) or 2)))
    db = _db()
    asyncio.create_task(loop.run_batch(db, events, years=years,
                                       ai_revise=ai_revise, ai_rounds=ai_rounds))
    return {"status": "started", "events": events, "years": years,
            "ai_revise": ai_revise, "ai_rounds": ai_rounds}


@router.post("/api/event-setups/{key}/live")
async def event_setup_live(key: str, body: dict, _: bool = Depends(require_admin)):
    _check_key(key)
    db = _db()
    enabled = bool(body.get("live_enabled"))
    if key == "fomc":
        await fomc_event.set_live_enabled(db, enabled)
        snap = fomc_event.status_snapshot()
    else:
        ev = econ_event.get(key)
        await ev.set_live_enabled(db, enabled)
        snap = ev.status_snapshot()
    return snap


@router.post("/api/event-setups/{key}/reset-params")
async def event_setup_reset_params(key: str, _: bool = Depends(require_admin)):
    _check_key(key)
    await loop.reset_params(_db(), key)
    return {"status": "ok", "event": key,
            "detail": "KI-Parameter gelöscht – nächster Lauf nutzt die festen Basis-Regeln"}
