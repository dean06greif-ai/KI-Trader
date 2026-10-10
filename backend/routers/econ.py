"""CPI-/NFP-Event-Setups: Status, Backtest (vergangene Datenveröffentlichungen)
& Live-Opt-in – gleiche API-Struktur wie routers/fomc.py."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import require_admin
from services import econ_backtest, econ_event

router = APIRouter(tags=["econ"])
logger = logging.getLogger(__name__)


def _db():
    from core import state
    if state.db is None:
        raise HTTPException(503, "DB nicht bereit")
    return state.db


def _event(key: str) -> econ_event.EconEvent:
    ev = econ_event.get(key)
    if not ev:
        keys = "|".join(econ_event.EVENTS)
        raise HTTPException(404, f"Unbekanntes Event '{key}' ({keys})")
    return ev


@router.get("/api/econ/status")
async def econ_status_all():
    snaps = econ_event.status_snapshots()
    for key, snap in snaps.items():
        snap["backtest_running"] = econ_backtest.is_running(key)
    return {"events": snaps}


@router.get("/api/econ/{key}/status")
async def econ_status(key: str):
    ev = _event(key)
    snap = ev.status_snapshot()
    snap["backtest_running"] = econ_backtest.is_running(ev.key)
    # Geschwindigkeits-Check wie beim FOMC-Panel: Tempo der Engine im Event
    try:
        from services.ai_engine import ai_engine
        interval_min = ai_engine.current_interval()[0]
        snap["engine"] = {
            "provider": ai_engine.config.get("provider"),
            "model": ai_engine.config.get("model"),
            "interval_min": interval_min,
            "event_interval_min": min(interval_min, ev.fast_interval_min),
            "fast_provider": ai_engine.config.get("provider") in ("groq", "gemini"),
        }
    except Exception:
        snap["engine"] = None
    return snap


@router.post("/api/econ/{key}/backtest")
async def econ_run_backtest(key: str, request: Request, _: bool = Depends(require_admin)):
    ev = _event(key)
    if econ_backtest.is_running(ev.key):
        return {"status": "busy", "detail": f"{ev.label}-Backtest läuft bereits"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    years = max(0.5, min(2.5, float(body.get("years", 2.0) or 2.0)))
    db = _db()
    asyncio.create_task(econ_backtest.run(db, ev.key, years=years))
    return {"status": "started", "event": ev.key, "years": years,
            "detail": f"Backtest läuft im Hintergrund – Ergebnis unter /api/econ/{ev.key}/backtest"}


@router.get("/api/econ/{key}/backtest")
async def econ_backtest_result(key: str):
    ev = _event(key)
    res = await econ_backtest.last_result(_db(), ev.key)
    return {"running": econ_backtest.is_running(ev.key), "result": res}


@router.post("/api/econ/{key}/config")
async def econ_config(key: str, request: Request, _: bool = Depends(require_admin)):
    ev = _event(key)
    body = await request.json()
    db = _db()
    if "live_enabled" in body:
        await ev.set_live_enabled(db, bool(body["live_enabled"]))
    return ev.status_snapshot()
