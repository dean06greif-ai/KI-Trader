"""Laufzeit dynamischer Strategien als eigene, handelbare Strategie.

- Registriert jede (nicht archivierte) dynamische Strategie in der Strategie-
  Registry (strategies.dynamic_regime_strategy.DynamicRegimeStrategy).
- Hält den Live-Regime-Zustand je (dynamische Strategie, Symbol) im RAM und
  in `runtime_state` des Dokuments (getrennt vom Legacy-`last_state`, damit
  der bisherige Apply-/Bestätigungs-Weg unverändert weiterläuft).
- Regimewechsel: protokollieren und – wie im Backtest/Walk-Forward – offene
  Trades DIESER dynamischen Strategie auf dem Symbol schließen
  (settings.on_switch = 'close', Standard) oder mit eigenem Stop/Ziel
  weiterlaufen lassen ('let_run'). Neue Trades kommen nur noch von der
  Strategie des neuen Regimes; unbelegte Regime handeln nicht.
- Regime-spezifische Trade-Einstellungen aus dem Blitz (regime_configs).
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from core import state

logger = logging.getLogger(__name__)

TICK_SECONDS = 60
STATE: Dict[str, Dict[str, Dict]] = {}     # did -> symbol -> regime state
ON_SWITCH_DEFAULT = "close"
REGIME_CFG_RESERVED = {"mode", "signals_enabled", "regime_configs"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def current_state(did: str, symbol: str) -> Optional[Dict]:
    return (STATE.get(did) or {}).get(symbol)


# ---------------- Registry ----------------
def register(doc: Dict):
    from strategies.registry import registry
    registry.upsert_dynamic(doc)
    rs = (doc.get("runtime_state") or {}).get("per_symbol") or {}
    if rs and doc["id"] not in STATE:
        STATE[doc["id"]] = {s: v for s, v in rs.items() if not (v or {}).get("error")}


def unregister(did: str):
    from strategies.registry import registry
    registry.remove_dynamic(did)
    STATE.pop(did, None)


async def load_all(db):
    docs = await db.dynamic_strategies.find({"archived": {"$ne": True}}).to_list(200)
    for d in docs:
        d.pop("_id", None)
        try:
            register(d)
        except Exception as e:  # noqa: BLE001 – ein kaputtes Dokument blockiert den Boot nicht
            logger.warning(f"dynamic_runtime: {d.get('id')} nicht registrierbar: {e}")
    return len(docs)


async def reload(did: str):
    """Nach Save/Build/Optimierung: Registry-Eintrag mit dem DB-Stand abgleichen."""
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc or doc.get("archived"):
        unregister(did)
        return None
    doc.pop("_id", None)
    register(doc)
    return doc


# ---------------- Regime-spezifische Trade-Einstellungen ----------------
def regime_trade_cfg(scc: Dict, dyn: Dict) -> Tuple[Optional[str], Dict]:
    """(Ablehnungsgrund | None, Overrides) für den Trade eines Regimes (rein).
    scc = Blitz-Konfiguration (dynamische Strategie × Coin);
    scc['regime_configs'][rid] überschreibt einzelne Werte, enabled=False
    schaltet das Regime für diesen Coin ab."""
    rc = ((scc or {}).get("regime_configs") or {}).get(str((dyn or {}).get("regime")))
    if not isinstance(rc, dict):
        return None, {}
    if rc.get("enabled") is False:
        return (f"Regime '{dyn.get('label')}' ist in den Auto-Trade-Einstellungen "
                "für diesen Coin deaktiviert"), {}
    return None, {k: v for k, v in rc.items()
                  if k not in REGIME_CFG_RESERVED and k != "enabled" and v is not None}


# ---------------- Live-Regime-Erkennung ----------------
def traded_ids() -> List[str]:
    from core.state import scanner
    from strategies.registry import registry
    return [s for s in scanner.enabled_strategies() if registry.is_dynamic(s)]


def _configured_symbols(did: str) -> List[str]:
    from core.state import autotrader
    out = []
    for key, cfg in (autotrader.config.get("strategy_coin_configs") or {}).items():
        if key.startswith(did + "_") and (cfg or {}).get("mode") in ("live", "paper"):
            out.append(key[len(did) + 1:])
    return out


def _interval_minutes(doc: Dict) -> int:
    from services.timeframes import TIMEFRAMES
    s = doc.get("settings") or {}
    if s.get("runtime_interval_minutes"):
        return int(min(max(int(s["runtime_interval_minutes"]), 5), 1440))
    tf_min = TIMEFRAMES.get(doc.get("timeframe") or "1h", 60)
    return int(min(max(tf_min, 15), 240))


def _due(doc: Dict) -> bool:
    checked = (doc.get("runtime_state") or {}).get("checked_at")
    if not checked:
        return True
    try:
        last = datetime.fromisoformat(checked)
    except ValueError:
        return True
    return (datetime.now(timezone.utc) - last).total_seconds() >= _interval_minutes(doc) * 60


async def refresh(doc: Dict) -> Dict:
    """Regime je Symbol bestimmen, Wechsel protokollieren + Übergang behandeln."""
    from services import dynamic_live
    s = doc.get("settings") or {}
    # Mindest-Tage; refresh_state hebt auf den Detektor-Warmup des Modells an
    days = int(min(max(int(s.get("check_days") or 30), 7), 90))
    symbols = sorted(set(doc.get("symbols") or []) | set(_configured_symbols(doc["id"])))
    new_state = await dynamic_live.refresh_state({**doc, "symbols": symbols}, days,
                                                 with_performance=False)
    old = STATE.get(doc["id"]) or {}
    switched = []
    for sym, st in (new_state.get("per_symbol") or {}).items():
        if st.get("error") or st.get("regime") is None:
            continue
        prev = old.get(sym)
        if prev and prev.get("regime") is not None and prev["regime"] != st["regime"]:
            switched.append((sym, prev, st))
    STATE[doc["id"]] = {sym: st for sym, st in (new_state.get("per_symbol") or {}).items()
                        if not st.get("error")}
    await state.db.dynamic_strategies.update_one(
        {"id": doc["id"]}, {"$set": {"runtime_state": new_state}})
    closed = []
    for sym, prev, st in switched:
        closed += await _on_switch(doc, sym, prev, st)
    return {"state": new_state, "switches": len(switched), "closed": closed}


async def _on_switch(doc: Dict, sym: str, prev: Dict, st: Dict) -> List[str]:
    from core.state import autotrader, scanner
    mode = (doc.get("settings") or {}).get("on_switch") or ON_SWITCH_DEFAULT
    closed = []
    if mode == "close":
        rows = await state.db.auto_trades.find(
            {"symbol": sym, "status": "open", "strategy_id": doc["id"],
             "manual_trade": {"$ne": True}}).to_list(50)
        for t in rows:
            try:
                price = scanner.current_price(sym) or t.get("entry")
                await state.db.auto_trades.update_one(
                    {"id": t["id"]},
                    {"$push": {"events": f"REGIMEWECHSEL: {prev.get('label')} → "
                                         f"{st.get('label')} – Position wird geschlossen "
                                         "(wie im Backtest/Walk-Forward)"}})
                res = await autotrader.manual_close(t["id"], price)
                if res is not None and not (isinstance(res, dict) and res.get("error")):
                    closed.append(t["id"])
            except Exception as e:  # noqa: BLE001
                logger.warning(f"Regimewechsel-Close {t.get('id')} fehlgeschlagen: {e}")
    else:
        await state.db.auto_trades.update_many(
            {"symbol": sym, "status": "open", "strategy_id": doc["id"]},
            {"$push": {"events": f"REGIMEWECHSEL: {prev.get('label')} → {st.get('label')} "
                                 "– Trade läuft mit eigenem Stop/Ziel weiter"}})
    try:
        await state.db.dynamic_switch_log.insert_one({
            "id": uuid.uuid4().hex[:10], "dynamic_id": doc["id"], "name": doc.get("name"),
            "symbol": sym, "at": _now_iso(), "source": "runtime",
            "from_regime": prev.get("regime"), "from_label": prev.get("label"),
            "to_regime": st.get("regime"), "to_label": st.get("label"),
            "confidence": st.get("confidence"), "similarities": st.get("similarities") or [],
            "reason": "Live-Handel als eigene Strategie", "auto_applied": True,
            "on_switch": mode, "closed_trades": closed})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"runtime switch log failed: {e}")
    return closed


async def runtime_loop():
    """Hält den Regime-Zustand aller aktiv gehandelten dynamischen Strategien frisch."""
    await asyncio.sleep(25)
    while True:
        try:
            if state.db is not None:
                ids = traded_ids()
                if ids:
                    docs = await state.db.dynamic_strategies.find(
                        {"id": {"$in": ids}, "archived": {"$ne": True}}).to_list(50)
                    for doc in docs:
                        if _due(doc):
                            await refresh(doc)
        except Exception as e:  # noqa: BLE001 – Loop darf nie sterben
            logger.warning(f"dynamic runtime loop error: {e}")
        await asyncio.sleep(TICK_SECONDS)
