"""Strategie-Copilot Endpoints – eigenes KI-System, getrennt vom KI-Trader.

Der Copilot berät beim Strategie-Bau, erkennt die aktuellen Einstellungen
(Kontext kommt vom Frontend + Server-Zustand), bewertet Ergebnisse und darf
Änderungen NUR nach Bestätigung des Nutzers anwenden (POST /api/copilot/apply).
"""
import logging
import uuid
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.state import scanner
from services import ai_providers
from services.ai_memory import memory
from services.strategy_copilot import (copilot, copilot_key_source, copilot_keys,
                                       PROVIDER, KEY_ENV, sanity_check)
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["copilot"])


@router.get("/api/copilot/status")
async def copilot_status():
    cfg = await copilot.config()
    keys = copilot_keys()
    return {"provider": PROVIDER,
            "model": cfg.get("model"),
            "models": ai_providers.allowed_models(PROVIDER),
            "paid_models": sorted(ai_providers.PAID_MODELS_NO_FALLBACK),
            "key_env": KEY_ENV,
            "key_source": copilot_key_source(),
            "keys_available": len(keys),
            "ready": bool(keys)}


@router.post("/api/copilot/model")
async def copilot_set_model(body: Dict, _: bool = Depends(require_admin)):
    try:
        cfg = await copilot.set_model(body.get("model") or None)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"status": "success", **cfg}


@router.get("/api/copilot/history")
async def copilot_history(limit: int = 60):
    return {"messages": await copilot.history(limit)}


@router.delete("/api/copilot/history")
async def copilot_clear(_: bool = Depends(require_admin)):
    return {"status": "success", "deleted": await copilot.clear_history()}


@router.post("/api/copilot/chat")
async def copilot_chat(body: Dict, _: bool = Depends(require_admin)):
    message = (body.get("message") or "").strip()
    if not message:
        raise HTTPException(status_code=400, detail="message erforderlich")
    try:
        return await copilot.chat(message, body.get("context") or {})
    except Exception as e:
        logger.warning(f"Copilot-Chat fehlgeschlagen: {e}")
        raise HTTPException(status_code=502, detail=f"Copilot nicht erreichbar: {str(e)[:200]}")


@router.post("/api/copilot/apply")
async def copilot_apply(body: Dict, _: bool = Depends(require_admin)):
    """Bestätigten Copilot-Vorschlag anwenden – nutzt exakt dieselben
    Validierungs- und Speicherpfade wie die manuellen Endpoints."""
    proposal = body.get("proposal") or {}
    ptype = proposal.get("type")

    if ptype == "settings":
        # Labor-Einstellungen werden direkt im Frontend-Formular übernommen –
        # sie sind bewusst KEIN Server-Zustand (kein Apply-Endpunkt nötig).
        raise HTTPException(status_code=400,
                            detail="Einstellungs-Vorschläge werden direkt im Panel "
                                   "übernommen – bitte den Übernehmen-Button im Chat nutzen")

    if ptype == "definition":
        from routers.strategies import validate_custom_definition
        definition = dict(proposal.get("definition") or {})
        sid = proposal.get("strategy_id") or definition.get("id")
        if sid:
            existing = strategy_registry.get(sid)
            if existing is not None and not getattr(existing, "IS_CUSTOM", False):
                raise HTTPException(status_code=400,
                                    detail="Nur Custom-Strategien können geändert werden")
        definition.pop("id", None)
        norm = validate_custom_definition(definition)   # wirft 422 mit problems/fixes
        sid = sid or f"custom_{uuid.uuid4().hex[:8]}"
        norm["id"] = sid
        norm.setdefault("timeframe", "1m")
        await state.db.custom_strategies.update_one({"id": sid}, {"$set": norm}, upsert=True)
        strategy_registry.upsert_custom(norm)
        updates: Dict = {}
        tfs = dict(scanner.settings.get("strategy_timeframes", {}))
        if tfs.get(sid) != norm["timeframe"]:
            tfs[sid] = norm["timeframe"]
            updates["strategy_timeframes"] = tfs
        enabled = list(scanner.settings.get("enabled_strategies", []))
        if sid not in enabled:
            enabled.append(sid)
            updates["enabled_strategies"] = enabled
        if updates:
            await scanner.save_settings(updates)
        await memory.remember(
            "copilot_note", f"Copilot: Strategie '{norm.get('name')}' übernommen",
            proposal.get("summary") or f"Definition von {sid} per Copilot-Vorschlag gespeichert",
            meta={"strategy_id": sid}, source="strategy_copilot", weight=2)
        return {"status": "success", "type": "definition", "id": sid, "definition": norm}

    if ptype == "params":
        sid = proposal.get("strategy_id")
        if not sid or not strategy_registry.get(sid):
            raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
        params = proposal.get("params") or {}
        trade_params = proposal.get("trade_params") or {}
        if not params and not trade_params and not proposal.get("timeframe"):
            raise HTTPException(status_code=400, detail="Vorschlag enthält keine Änderung")
        updates: Dict = {}
        if params:
            sp = dict(scanner.settings.get("strategy_params", {}))
            sp[sid] = {**sp.get(sid, {}), **params}
            updates["strategy_params"] = sp
        if proposal.get("timeframe"):
            tfs = dict(scanner.settings.get("strategy_timeframes", {}))
            tfs[sid] = proposal["timeframe"]
            updates["strategy_timeframes"] = tfs
        if updates:
            await scanner.save_settings(updates)
        if trade_params:
            from routers.optimizer import _write_strategy_override
            await _write_strategy_override(sid, trade_params)
        from routers.optimizer import _write_backtest_config
        await _write_backtest_config(sid, params, trade_params, proposal.get("timeframe"))
        await memory.remember(
            "copilot_note", f"Copilot: Parameter für {sid} übernommen",
            proposal.get("summary") or f"params={params} trade_params={trade_params}",
            meta={"strategy_id": sid}, source="strategy_copilot", weight=2)
        return {"status": "success", "type": "params", "strategy_id": sid,
                "params": params, "trade_params": trade_params}

    raise HTTPException(status_code=400, detail="proposal.type muss definition|params sein")


@router.post("/api/copilot/review")
async def copilot_review(body: Dict, _: bool = Depends(require_admin)):
    """Deterministische Ergebnis-Prüfung ohne LLM (schnell, kostenlos)."""
    metrics = (body.get("metrics") or {})
    notes = sanity_check(metrics)
    return {"status": "success", "ok": not notes, "checks": notes}
