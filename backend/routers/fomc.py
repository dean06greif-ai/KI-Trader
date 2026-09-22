"""FOMC-Event-Setup: Status, Backtest (vergangene Zinsentscheide) & Live-Opt-in."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from core.auth import require_admin
from services import fomc_backtest, fomc_event, key_credits

router = APIRouter(tags=["fomc"])
logger = logging.getLogger(__name__)


def _db():
    from core import state
    if state.db is None:
        raise HTTPException(503, "DB nicht bereit")
    return state.db


@router.get("/api/fomc/status")
async def fomc_status():
    snap = fomc_event.status_snapshot()
    snap["backtest_running"] = fomc_backtest.is_running()
    # Geschwindigkeits-Check: reicht das aktuelle Modell im Event-Fenster?
    try:
        from services.ai_engine import ai_engine
        from services.ai_news_watcher import FOMC_INTERVAL_MIN, news_watcher
        interval_min = ai_engine.current_interval()[0]
        snap["engine"] = {
            "provider": ai_engine.config.get("provider"),
            "model": ai_engine.config.get("model"),
            "interval_min": interval_min,
            "fomc_interval_min": min(interval_min, fomc_event.FAST_INTERVAL_MIN),
            "fast_provider": ai_engine.config.get("provider") in ("groq", "gemini"),
            "news_fomc_interval_min": FOMC_INTERVAL_MIN,
            "news_boost_active": bool((news_watcher.status().get("fomc_boost") or {}).get("active")),
        }
    except Exception:
        snap["engine"] = None
    return snap


@router.post("/api/fomc/backtest")
async def fomc_run_backtest(request: Request, _: bool = Depends(require_admin)):
    if fomc_backtest.is_running():
        return {"status": "busy", "detail": "FOMC-Backtest läuft bereits"}
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    years = max(0.5, min(2.5, float(body.get("years", 2.0) or 2.0)))
    db = _db()
    asyncio.create_task(fomc_backtest.run(db, years=years))
    return {"status": "started", "years": years,
            "detail": "Backtest läuft im Hintergrund – Ergebnis unter /api/fomc/backtest"}


@router.get("/api/fomc/backtest")
async def fomc_backtest_result():
    res = await fomc_backtest.last_result(_db())
    return {"running": fomc_backtest.is_running(), "result": res}


@router.post("/api/fomc/config")
async def fomc_config(request: Request, _: bool = Depends(require_admin)):
    body = await request.json()
    db = _db()
    if "live_enabled" in body:
        await fomc_event.set_live_enabled(db, bool(body["live_enabled"]))
    return fomc_event.status_snapshot()


@router.get("/api/fomc/key-credits")
async def fomc_key_credits(_: bool = Depends(require_admin)):
    snap = key_credits.snapshot()
    if not snap.get("checked_at"):
        from core import state
        snap = await key_credits.check_once(state.db, state.telegram)
    return snap
