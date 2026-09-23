"""API: Website-Benachrichtigungen, Telegram-Meldungs-Toggles, Kill-Switch/Anti-Stacking."""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from fastapi import APIRouter, Depends

from core import state
from core.auth import require_admin
from services import notifications, trade_guard

router = APIRouter(tags=["notify"])

READ_RETENTION_DAYS = 7
_last_purge = 0.0


async def _purge_old_read():
    """Gelesene Meldungen nach READ_RETENTION_DAYS automatisch löschen
    (max. 1x pro Stunde ausgeführt)."""
    global _last_purge
    now = time.time()
    if now - _last_purge < 3600:
        return
    _last_purge = now
    cutoff = (datetime.now(timezone.utc) - timedelta(days=READ_RETENTION_DAYS)).isoformat()
    try:
        await state.db.app_notifications.delete_many(
            {"read": True, "$or": [{"read_at": {"$lt": cutoff}},
                                   {"read_at": None, "created_at": {"$lt": cutoff}}]})
    except Exception:
        pass


@router.get("/api/notifications")
async def get_notifications(unread_only: bool = True, limit: int = 50,
                            filter: str = None):
    """filter=unread|read|all (überschreibt unread_only, das kompatibel bleibt)."""
    await _purge_old_read()
    mode = (filter or ("unread" if unread_only else "all")).lower()
    if mode == "unread":
        q = {"read": False}
    elif mode == "read":
        q = {"read": True}
    else:
        q = {}
    rows = await state.db.app_notifications.find(q, {"_id": 0}) \
        .sort("created_at", -1).to_list(max(1, min(limit, 200)))
    return {"notifications": rows}


@router.post("/api/notifications/popped")
async def mark_notifications_popped(body: Dict = None):
    """Popup wurde gezeigt -> nie wieder aufpoppen (bleibt in der Glocke lesbar)."""
    ids = (body or {}).get("ids") or []
    if not ids:
        return {"status": "ok", "updated": 0}
    res = await state.db.app_notifications.update_many(
        {"id": {"$in": ids}}, {"$set": {"popped": True}})
    return {"status": "ok", "updated": res.modified_count}


@router.post("/api/notifications")
async def add_notification(body: Dict = None, _: bool = Depends(require_admin)):
    """Von der UI genutzt: unterdrückte doppelte Fehler-Popups landen hier,
    damit sie in der Glocke nachlesbar bleiben (Toast-Dedupe)."""
    body = body or {}
    msg = str(body.get("message") or "").strip()[:400]
    if not msg:
        return {"status": "ignored"}
    await notifications.website_notify(
        state.db, str(body.get("kind") or "error")[:20],
        str(body.get("title") or "Fehler")[:80], msg)
    return {"status": "ok"}


@router.post("/api/notifications/read")
async def mark_notifications_read(body: Dict = None):
    ids = (body or {}).get("ids")
    q = {"id": {"$in": ids}} if ids else {"read": False}
    res = await state.db.app_notifications.update_many(
        q, {"$set": {"read": True,
                     "read_at": datetime.now(timezone.utc).isoformat()}})
    return {"status": "ok", "updated": res.modified_count}


@router.get("/api/telegram/notify-config")
async def get_telegram_notify_config():
    return await notifications.get_config(state.db)


@router.get("/api/telegram/notify-catalog")
async def get_telegram_notify_catalog():
    """Gruppierter Katalog aller Telegram-Meldungen (Beschriftung + Standard) für die UI."""
    return {"catalog": notifications.catalog(),
            "config": await notifications.get_config(state.db)}


@router.get("/api/min-trade/config")
async def get_min_trade_config(_: bool = Depends(require_admin)):
    """Mindest-Trade (nur Echtgeld-Live) statt Ablehnung an Kapital-/Risikogrenze."""
    from services import min_trade
    return {"config": await min_trade.get_config(state.db),
            "open_live_min_trades": await min_trade.open_count(state.db)}


@router.post("/api/min-trade/config")
async def set_min_trade_config(body: Dict, _: bool = Depends(require_admin)):
    from services import min_trade
    return {"config": await min_trade.update_config(state.db, body)}


@router.post("/api/telegram/notify-config")
async def set_telegram_notify_config(body: Dict, _: bool = Depends(require_admin)):
    return await notifications.update_config(state.db, body)


@router.get("/api/trade-guard")
async def get_trade_guard(mode: Optional[str] = None):
    """Guard-Config + Zustand (State je Modus: ?mode=live|paper, Default aktueller Modus)."""
    st = await trade_guard.get_state(state.db, mode or trade_guard._current_mode())
    other = "paper" if st["mode"] == "live" else "live"
    return {"config": await trade_guard.get_config(state.db),
            "state": st,
            "states": {st["mode"]: st, other: await trade_guard.get_state(state.db, other)}}


@router.post("/api/trade-guard/config")
async def set_trade_guard_config(body: Dict, _: bool = Depends(require_admin)):
    return {"config": await trade_guard.update_config(state.db, body),
            "state": await trade_guard.get_state(state.db)}


@router.post("/api/trade-guard/resume")
async def resume_trade_guard(body: Optional[Dict] = None, _: bool = Depends(require_admin)):
    return {"state": await trade_guard.resume(state.db, (body or {}).get("mode"))}


@router.get("/api/risk-budget")
async def get_risk_budget(mode: Optional[str] = None, _: bool = Depends(require_admin)):
    """Gesamt-Risikobudget: Config + aktuelle Auslastung je Modus (Phase 1.6)."""
    from services import risk_budget
    m = trade_guard.normalize_mode(mode or trade_guard._current_mode())
    return {"config": await risk_budget.get_config(state.db),
            "usage": await risk_budget.usage(state.db, m)}


@router.post("/api/risk-budget/config")
async def set_risk_budget(body: Dict, _: bool = Depends(require_admin)):
    from services import risk_budget
    return {"config": await risk_budget.update_config(state.db, body)}


@router.get("/api/safety/status")
async def get_safety_status(_: bool = Depends(require_admin)):
    """Sicherheitsstatus-Ampel (Audit 2.4): ok | warn | critical + Einzel-Checks."""
    from services import safety_status
    return await safety_status.status(state.db, force=True)


@router.post("/api/safety/config")
async def set_safety_config(body: Dict, _: bool = Depends(require_admin)):
    from services import safety_status
    return {"config": await safety_status.update_config(state.db, body)}
