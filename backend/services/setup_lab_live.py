"""Lab-validierte Setups live schalten (KI-Trader-Lab -> Live, mit allen Risiko-Limits).

Befund 10/2026 (Prod-Daten):
  * Event-Setups (FOMC/CPI/NFP/PPI/PCE) waren für Krypto im Lab validiert, gingen
    aber nie live: der Live-Bonus verlangte zusätzlich den manuellen Schalter
    `live_enabled` – und eine reine Revisions-Rückstufung („Validierung neu
    gestartet“) blockte vor dem Bonus. Jetzt: validiert = automatisch live
    (Opt-out bleibt), Revisions-Rückstufungen blocken lab-validierte Setups nicht.
  * Detektor-Setups (z.B. session_open Krypto: 278 OOS-Trades, 3/3 Walk-Forward-
    Fenster positiv) brauchen für Live echte profitable Paper-Trades. Mit Trader-
    Opt-in je Klasse × Setup gilt ein streng geprüfter Lab-Edge als Freigabe;
    Performance-Rückstufungen (schwache echte Ergebnisse) gewinnen weiterhin.

State (settings._id = DOC_ID): {"optin": {"crypto": ["session_open"]}, "auto_all": false}
"""
import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DOC_ID = "setup_lab_live"
EVENT_SETUPS = ("fomc_event", "cpi_event", "nfp_event", "ppi_event", "pce_event")
MIN_WF_POS = 2               # Walk-Forward-Fenster mit positivem PnL (von 3)
MIN_OOS_FACTOR = 1.5         # OOS-Trades >= 1,5 x Mindest-Trades (nicht knapp)
MIN_OOS_PPT = 0.02           # Netto-OOS-PnL je Trade (USDT bei 100 USDT Notional) – sonst Rauschen
CACHE_S = 120

_state: Dict = {"optin": {}, "auto_all": False}
_cache: Dict = {"ts": 0.0, "classes": {}}


def event_live_allowed(ev_state: Optional[Dict]) -> bool:
    """Event-Setup: Live erlaubt, wenn eingeschaltet ODER automatisch (Standard)
    und nicht ausdrücklich abgeschaltet (rein)."""
    st = ev_state or {}
    return bool(st.get("live_enabled")) or (bool(st.get("live_auto", True)) and not st.get("live_opt_out"))


def lab_validated(entry: Optional[Dict]) -> Tuple[bool, str]:
    """Strenger Lab-Nachweis eines Detektor-Setups aus setup_backtest_state (rein):
    Edge bestanden, OOS profitabel, genug OOS-Trades, >= 2/3 Walk-Forward-Fenster
    positiv, IS nicht negativ, kein Edge im Stale-Zustand."""
    from services.setup_backtest import runner
    e = entry or {}
    if e.get("status") not in runner.PASSED_STATES:
        return False, f"Lab-Status '{e.get('status') or 'ungetestet'}' – kein bestätigter Edge"
    oos, is_ = e.get("oos") or {}, e.get("is") or {}
    n = int(oos.get("trades") or 0)
    need = int(((e.get("min_trades") or {}).get("oos")) or runner.MIN_OOS_TRADES)
    if n < need * MIN_OOS_FACTOR:
        return False, f"zu wenig OOS-Trades ({n} < {need * MIN_OOS_FACTOR:g})"
    if float(oos.get("pnl") or 0) <= 0:
        return False, "OOS-PnL nicht positiv"
    ppt = float(oos.get("pnl") or 0) / max(n, 1)
    if ppt < MIN_OOS_PPT:
        return False, f"OOS-Edge je Trade {ppt:+.3f} < {MIN_OOS_PPT} (nach Gebühren kaum von Null zu trennen)"
    if float(is_.get("pnl") or 0) < 0:
        return False, "In-Sample negativ – Edge nur im OOS-Zufall"
    wins = oos.get("windows") or []
    pos = int(oos.get("windows_pos") or sum(1 for w in wins if float(w.get("pnl") or 0) > 0))
    if wins and pos < MIN_WF_POS:
        return False, f"nur {pos}/{len(wins)} Walk-Forward-Fenster positiv"
    if int(e.get("stale") or 0) > 0:
        return False, "Edge zuletzt nicht bestätigt (stale)"
    return True, (f"Lab-validiert: OOS {n} Trades, WR {oos.get('winrate')}%, PnL {float(oos.get('pnl') or 0):+.2f}, "
                  f"{pos}/{len(wins) or '-'} WF-Fenster positiv ({e.get('name') or 'Edge'})")


def opted_in(setup: str, asset_class: str, state: Optional[Dict] = None) -> bool:
    st = state if state is not None else _state
    return bool(st.get("auto_all")) or setup in ((st.get("optin") or {}).get(asset_class) or [])


def override_for(setup: Optional[str], asset_class: Optional[str]) -> Optional[Tuple[bool, str]]:
    """(True, Grund) für opt-in-Detektor-Setups mit strengem Lab-Nachweis (Cache)."""
    if not setup or not asset_class or setup in EVENT_SETUPS or not opted_in(setup, asset_class):
        return None
    ok, why = lab_validated(((_cache.get("classes") or {}).get(asset_class) or {}).get(setup))
    return (True, f"{why} + Trader-Opt-in") if ok else None


def event_override(setup: Optional[str], asset_class: str) -> Optional[Tuple[bool, str]]:
    if setup == "fomc_event":
        from services import fomc_event
        return fomc_event.live_override(asset_class)
    if setup in EVENT_SETUPS:
        from services import econ_event
        return econ_event.live_override_for_setup(setup, asset_class)
    return None


def any_override(setup: Optional[str], asset_class: Optional[str]) -> Optional[Tuple[bool, str]]:
    if not setup or not asset_class:
        return None
    return event_override(setup, asset_class) or override_for(setup, asset_class)


def block_bypassed(setup: Optional[str], asset_class: Optional[str], block: Optional[Dict]) -> bool:
    """Rückstufung übergehen? Nur reine Revisions-Rückstufungen und nur für
    Setups mit Lab-Freigabe – schwache echte Ergebnisse blocken weiter (rein)."""
    from services import setup_lifecycle as lifecycle
    if not block or not lifecycle.revision_demotion(block):
        return False
    return any_override(setup, asset_class) is not None


async def load(db) -> Dict:
    global _state
    try:
        doc = await db.settings.find_one({"_id": DOC_ID}, {"_id": 0}) or {}
        _state = {"optin": {k: list(v or []) for k, v in (doc.get("optin") or {}).items()},
                  "auto_all": bool(doc.get("auto_all"))}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"setup_lab_live load: {e}")
    return _state


async def refresh(db, force: bool = False) -> None:
    """Lab-Stand (setup_backtest_state) + Opt-in in den Cache (alle 2 min)."""
    if not force and time.time() - _cache["ts"] < CACHE_S:
        return
    _cache["ts"] = time.time()
    try:
        from services.setup_backtest import runner
        st = await runner.load_state(db)
        _cache["classes"] = st.get("classes") or {}
        await load(db)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"setup_lab_live refresh: {e}")


async def set_optin(db, asset_class: str, setup: str, enabled: bool) -> Dict:
    await load(db)
    cur: List[str] = list((_state["optin"].get(asset_class) or []))
    if enabled and setup not in cur:
        cur.append(setup)
    if not enabled:
        cur = [s for s in cur if s != setup]
    _state["optin"][asset_class] = cur
    await db.settings.update_one({"_id": DOC_ID}, {"$set": {"optin": _state["optin"]}}, upsert=True)
    await refresh(db, force=True)
    return status()


def status() -> Dict:
    rows = []
    for cls, entries in (_cache.get("classes") or {}).items():
        for sid, e in (entries or {}).items():
            if sid in EVENT_SETUPS or not isinstance(e, dict):
                continue
            ok, why = lab_validated(e)
            rows.append({"asset_class": cls, "setup": sid, "validated": ok, "why": why,
                         "opted_in": opted_in(sid, cls), "live_override": bool(ok and opted_in(sid, cls))})
    rows.sort(key=lambda r: (not r["validated"], r["asset_class"], r["setup"]))
    return {"optin": _state.get("optin") or {}, "auto_all": bool(_state.get("auto_all")), "setups": rows}
