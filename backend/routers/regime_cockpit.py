"""Regime-Cockpit-API (PLAN_REGIME_COCKPIT B2) – nur lesend."""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException

from core import state
from services import regime_cockpit
from services.ai_engine import ai_engine

logger = logging.getLogger(__name__)
router = APIRouter(tags=["regime-cockpit"])


def _symbols(raw: Optional[str]) -> list:
    if raw:
        return [s.strip().upper() for s in raw.split(",") if s.strip()][:30]
    return list(getattr(ai_engine, "symbols", None) or [])[:30]


@router.get("/api/regime-cockpit/overview")
async def cockpit_overview(days: int = 14, symbols: Optional[str] = None):
    """Kennzahlen je Symbol (Vorwärts-Trefferquote, Übereinstimmung, Stufe, Trades)."""
    syms = _symbols(symbols)
    if not syms:
        return {"rows": [], "days": days}
    return {"days": days, "rows": await regime_cockpit.overview(state.db, syms, days)}


@router.get("/api/regime-cockpit/health")
async def cockpit_health(force: bool = False):
    """Gesundheit der Regime-Brücke: verwaiste/inaktive dynamische Strategien,
    fehlende Lab-Freigabe, Kurzfrist-Erkennung auf Zufallsniveau (nur lesend)."""
    from services import regime_bridge_health
    return await regime_bridge_health.status(state.db, force=force)


@router.get("/api/regime-cockpit/{symbol}")
async def cockpit_symbol(symbol: str, days: int = 14):
    """Komplettes Cockpit: Kurs, beide Regime-Ebenen, Trade-Marker, Trefferquoten."""
    try:
        return await regime_cockpit.assemble(state.db, symbol.upper(), days)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"regime-cockpit {symbol}: {e}")
        raise HTTPException(status_code=502, detail=f"Cockpit-Daten nicht verfügbar: {str(e)[:160]}")
