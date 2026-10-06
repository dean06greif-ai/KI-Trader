"""Regime-Champion je Asset: robuste Auswahl zwischen mehreren Regime-Erkennungen
(Timeframes, Regime-Anzahl, kombiniert/je Coin) – siehe services/regime_selection.
Eigener Router VOR routers/regime_lab (dort fängt /api/regime-lab/{aid} sonst alles)."""
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.config import ALL_SYMBOLS
from services import regime_selection

router = APIRouter(tags=["regime-champions"])


def _symbols(raw) -> list:
    syms = [s.strip().upper() for s in str(raw or "").split(",") if s.strip()]
    return syms or list(ALL_SYMBOLS)


@router.get("/api/regime-lab/champions")
async def champions(symbols: str = ""):
    """Vergleich aller gespeicherten v2-Erkennungen je Asset × Band (nur lesen)."""
    if state.db is None:
        raise HTTPException(status_code=503, detail="DB nicht bereit")
    return await regime_selection.compute(state.db, _symbols(symbols))


@router.post("/api/regime-lab/champions/apply")
async def champions_apply(body: Dict, _: bool = Depends(require_admin)):
    """Empfohlene Champions übernehmen (keys = ['BTCUSDT|swing', ...] oder alle)."""
    res = await regime_selection.compute(state.db, _symbols(",".join(body.get("symbols") or [])))
    out = await regime_selection.apply(state.db, res["results"], body.get("keys"))
    from services import structural_regime
    structural_regime.invalidate()
    return out


@router.post("/api/regime-lab/champions/mode")
async def champions_mode(body: Dict, _: bool = Depends(require_admin)):
    """off = nur Klassen-Freigabe (Alt-Verhalten) · suggest = übernommene Champions
    wirken, neue nur per Klick · auto = alle 6 h automatisch übernehmen."""
    mode = str(body.get("mode") or "").lower()
    if mode not in regime_selection.MODES:
        raise HTTPException(status_code=400, detail=f"mode: {'|'.join(regime_selection.MODES)}")
    st = await regime_selection.load_state(state.db)
    st["mode"] = mode
    await regime_selection.save_state(state.db, st)
    from services import structural_regime
    structural_regime.invalidate()
    return st


@router.delete("/api/regime-lab/champions/{key}")
async def champions_remove(key: str, _: bool = Depends(require_admin)):
    st = await regime_selection.load_state(state.db)
    st["assign"].pop(key, None)
    await regime_selection.save_state(state.db, st)
    from services import structural_regime
    structural_regime.invalidate()
    return st
