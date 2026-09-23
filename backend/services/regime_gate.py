"""Regime-Gate: Marktphasen-Filter für den Auto-Trade.

Blockiert NEUE Trade-Eröffnungen einer Strategie, wenn sich der Coin aktuell
in einer vom Nutzer blockierten Marktphase befindet (z.B. Seitwärtsmarkt).
Konfiguration pro Strategie/Coin über die bestehende Auto-Trade-Config:
  regime_filter_enabled: bool (Default False)
  regime_block_phases:   Liste aus "seitwärts" | "bulle" | "bär"

Architektur: nutzt die vorhandene Regime-Erkennung (services.regime, ohne
Lookahead) auf 1h-Kerzen der letzten 30 Tage, gecacht pro Symbol (15 Min TTL).
Fail-open: Bei Datenfehlern wird der Trade NICHT blockiert – Stabilität des
bestehenden Live-Verhaltens hat Vorrang.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 900
DETECT_DAYS = 30
DETECT_TIMEFRAME = "1h"
MAX_REGIMES = 3
LOOKBACK_DAYS = 3.0
PHASES = ("seitwärts", "bulle", "bär")
DEFAULT_BLOCK_PHASES = ["seitwärts"]

_cache: Dict[str, Dict] = {}
_locks: Dict[str, asyncio.Lock] = {}


def phase_from_label(label: Optional[str]) -> Optional[str]:
    """Regime-Label -> grobe Phase. AP08/R15: nur noch benannter LEGACY-Adapter
    (market_context.legacy_phase_from_label) für KMeans-Modelle ohne
    Richtungs-IDs; v2-Modelle liefern die Richtung über die Regime-ID."""
    from services import market_context as mc
    return mc.legacy_phase_from_label(label)


def _cached(symbol: str) -> Optional[Dict]:
    ent = _cache.get(symbol)
    if ent and (time.monotonic() - ent["_at"]) < CACHE_TTL_SECONDS:
        return ent
    return None


async def _detect(symbol: str) -> Dict:
    from services import market_context as mc
    from services import regime as rg
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    import aiohttp

    async with aiohttp.ClientSession() as session:
        raw = await fetch_history(session, symbol, DETECT_DAYS)
    candles = aggregate_candles(raw, DETECT_TIMEFRAME, drop_partial=True)
    del raw
    if len(candles) < 200:
        raise RuntimeError(f"{symbol}: zu wenig Kerzen für Regime-Erkennung ({len(candles)})")
    model = await asyncio.to_thread(
        rg.detect_regimes, {symbol: candles}, DETECT_TIMEFRAME, MAX_REGIMES, LOOKBACK_DAYS)
    if not model:
        raise RuntimeError(f"{symbol}: Marktphasen konnten nicht bestimmt werden")
    cur = rg.current_regime(model, candles, DETECT_TIMEFRAME)
    label = cur.get("label")
    # AP08/R15: Richtung aus der Regime-ID (v2-Taxonomie); nur Legacy-KMeans
    # (Cluster-IDs ohne Richtung) nutzt den benannten Label-Adapter.
    direction = None
    if rg.is_v2(model):
        mode = (model.get("config") or {}).get("regime_mode",
                                               model.get("regime_mode"))
        direction = mc.direction_from_regime_id(cur.get("regime"), mode)
    if direction is None:
        direction = mc.direction_from_phase(mc.legacy_phase_from_label(label))
    ctx = mc.structural_context(
        "regime_gate", direction=direction, label=label,
        confidence=cur.get("confidence"), model_fp=mc.model_fingerprint(model))
    return {"_at": time.monotonic(),
            "symbol": symbol,
            "phase": ctx["phase"],
            "label": label,
            "confidence": cur.get("confidence"),
            "market_context": ctx,
            "state": ctx["state"],
            "checked_at": datetime.now(timezone.utc).isoformat()}


async def current_phase(symbol: str) -> Dict:
    """Aktuelle Marktphase eines Coins (gecacht, max. 1 Erkennung parallel)."""
    ent = _cached(symbol)
    if ent:
        return ent
    lock = _locks.setdefault(symbol, asyncio.Lock())
    async with lock:
        ent = _cached(symbol)
        if ent:
            return ent
        ent = await _detect(symbol)
        _cache[symbol] = ent
        return ent


def blocked_phases(cfg: Dict) -> List[str]:
    raw = cfg.get("regime_block_phases")
    if raw is None:
        raw = DEFAULT_BLOCK_PHASES
    return [str(p).strip().lower() for p in raw if str(p).strip().lower() in PHASES]


def gate_source(cfg: Dict) -> str:
    """`own` (heutige Erkennung, Default) | `lab` (freigegebene Lab-Analyse, nur Stufe active)."""
    return "lab" if str(cfg.get("regime_gate_source") or "own").lower() == "lab" else "own"


async def check_signal_allowed(cfg: Dict, symbol: str, horizon: Optional[str] = None) -> Tuple[bool, str]:
    """True = Trade erlaubt. Fail-open bei Fehlern (bestehendes Verhalten
    darf durch den Filter nie durch einen Datenfehler ausgehebelt werden).
    `horizon` (scalp|swing) wählt bei Quelle Lab die Freigabe des Horizont-Bands."""
    if not cfg.get("regime_filter_enabled"):
        return True, ""
    blocked = blocked_phases(cfg)
    if not blocked:
        return True, ""
    if gate_source(cfg) == "lab":
        # PLAN_REGIME_BRUECKE 2.2: Quelle = freigegebene Lab-Analyse (Stufe active).
        # unknown/stale/keine Freigabe -> fail-open wie heute.
        try:
            from services import structural_regime, regime_release
            ph = (await structural_regime.phase(symbol, regime_release.band_of_horizon(horizon))
                  if horizon else await structural_regime.phase(symbol))
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Regime-Gate {symbol}: Lab-Quelle fehlgeschlagen ({e}) – Trade erlaubt")
            return True, ""
        if ph is None:
            logger.info(f"Regime-Gate {symbol}: Lab-Regime nicht wirksam – Trade erlaubt")
            return True, ""
        if ph in blocked:
            return False, (f"Regime-Filter (Quelle Lab-Analyse): {symbol} ist strukturell in "
                           f"Phase '{ph}' – für diese Strategie blockiert")
        return True, ""
    try:
        ent = await current_phase(symbol)
    except Exception as e:  # noqa: BLE001 – fail-open
        # AP08: abgelaufener Cache-Stand wird als ECHTER Zustand 'stale'
        # sichtbar gemacht (statt kommentarlos zu verschwinden); Verhalten
        # bleibt fail-open.
        old = _cache.get(symbol)
        if old is not None:
            old["state"] = "stale"
            if isinstance(old.get("market_context"), dict):
                old["market_context"]["state"] = "stale"
        logger.warning(f"Regime-Gate {symbol}: Erkennung fehlgeschlagen ({e}) – Trade erlaubt"
                       + (" (letzter Stand: stale)" if old else ""))
        return True, ""
    phase = ent.get("phase")
    if phase in blocked:
        return False, (f"Regime-Filter: {symbol} ist aktuell im Regime "
                       f"'{ent.get('label')}' ({ent.get('confidence')}% Sicherheit) – "
                       f"Phase '{phase}' ist für diese Strategie blockiert")
    return True, ""
