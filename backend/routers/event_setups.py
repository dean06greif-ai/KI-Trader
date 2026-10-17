"""Event-Setups gebündelt für den Backtester: Übersicht, Batch-Backtest mit
Event-Auswahl + KI-Schleife, Live-Opt-in & Reset der KI-Parameter.
Die bestehenden Einzel-Endpunkte (/api/fomc/*, /api/econ/*) bleiben unverändert."""
import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

from core.auth import require_admin
from services import econ_event, event_assets, fomc_event
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
        backtests, ai_params_by_class = {}, {}
        for cls in event_assets.CLASSES:
            res = await loop.last_result_light(db, key, cls)
            if res:
                backtests[cls] = res
            meta = await loop.stored_params_meta(db, key, cls)
            if meta:
                ai_params_by_class[cls] = meta
        snap["backtests"] = backtests
        snap["ai_params_by_class"] = ai_params_by_class
        # Abwärtskompatibilität: bisherige Felder = Krypto-Stand
        snap["backtest"] = backtests.get("crypto")
        snap["ai_params"] = ai_params_by_class.get("crypto")
        snap["backtest_running"] = loop.event_running(key)
    return {"events": snaps, "job": loop.job_public(),
            "event_keys": list(loop.EVENT_KEYS), "max_rounds": loop.MAX_ROUNDS,
            "asset_classes": [{"key": c, "label": event_assets.LABELS[c],
                               "symbols": event_assets.symbols_for(c),
                               "hist_note": event_assets.HIST_NOTE[c]}
                              for c in event_assets.CLASSES]}


@router.post("/api/event-setups/backtest")
async def event_setups_backtest(body: dict, _: bool = Depends(require_admin)):
    events = [e for e in (body.get("events") or []) if e in loop.EVENT_KEYS]
    if not events:
        raise HTTPException(400, "Mindestens ein Event wählen (fomc|cpi|nfp|ppi|pce)")
    classes = [c for c in (body.get("asset_classes") or ["crypto"])
               if c in event_assets.CLASSES] or ["crypto"]
    if loop.job_running() or any(loop.event_running(k) for k in events):
        return {"status": "busy", "detail": "Es läuft bereits ein Event-Backtest"}
    years = max(0.5, min(2.5, float(body.get("years", 2.0) or 2.0)))
    ai_revise = bool(body.get("ai_revise"))
    ai_rounds = max(1, min(loop.MAX_ROUNDS, int(body.get("ai_rounds", 2) or 2)))
    db = _db()
    asyncio.create_task(loop.run_batch(db, events, years=years,
                                       ai_revise=ai_revise, ai_rounds=ai_rounds,
                                       asset_classes=classes))
    return {"status": "started", "events": events, "years": years,
            "asset_classes": classes, "ai_revise": ai_revise, "ai_rounds": ai_rounds}


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
async def event_setup_reset_params(key: str, asset_class: str = "crypto",
                                   _: bool = Depends(require_admin)):
    _check_key(key)
    cls = event_assets.normalize(asset_class)
    await loop.reset_params(_db(), key, cls)
    return {"status": "ok", "event": key, "asset_class": cls,
            "detail": "KI-Parameter gelöscht – nächster Lauf nutzt die festen Basis-Regeln"}
