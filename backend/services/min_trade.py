"""Mindest-Trade für ECHTGELD-Live-Trades (rein & testbar + Config).

Problem (09/2026, ~30 USDT frei): Kapital-Grenze (capital_fit) bzw. Gesamt-
Risikobudget (risk_budget) lehnten Live-Signale komplett ab ("Rest-Budget 0.00
USDT ... kein Trade") bzw. wichen auf einen Paper-Trade aus. Nutzer-Vorgabe:
statt Ablehnung einen MINDEST-Trade live eröffnen – aber NUR im Echtgeld-Live-
Modus. Paper- und Sammel-Trades bleiben exakt wie bisher.

Regeln (bewusst konservativ, damit die Ausnahme nie selbst zum Risiko wird):
  * Größe = kleinste handelbare Position: Ziel-Marge `target_margin_usdt`
    (Standard = capital_fit.MIN_MARGIN_USDT), mind. aber das Börsen-Minimum
    (min_qty). Ist das Risiko bei Ziel-Marge zu hoch, wird auf das reine
    Börsen-Minimum verkleinert.
  * Risiko (Entry->SL × Menge) <= `max_risk_pct` % der Live-Equity.
  * Marge muss ins ECHTE freie Börsen-Guthaben passen (mit Gebühren-Puffer).
  * Max. `max_open` gleichzeitig offene Mindest-Trades (umgehen das Budget).
Nicht möglich -> None + Grund; dann greift das bisherige Verhalten.
"""
from typing import Dict, List, Optional

from services import capital_fit

CONFIG_ID = "min_trade_fallback_config"
DEFAULT_CONFIG = {
    "enabled": True,
    "target_margin_usdt": capital_fit.MIN_MARGIN_USDT,
    "max_risk_pct": 3.0,         # % der Live-Equity je Mindest-Trade
    "max_open": 3,               # gleichzeitig offene Mindest-Trades (live)
    # Ist auch der Mindest-Trade unmöglich: bisheriges Verhalten (Paper statt
    # Ablehnung bei der Kapital-Grenze) beibehalten.
    "paper_fallback_if_impossible": True,
}
_BOOL_KEYS = ("enabled", "paper_fallback_if_impossible")
_cfg_cache: Optional[Dict] = None


def _floor_step(qty: float, step: float) -> float:
    if step and step > 0:
        return round(int(qty / step + 1e-9) * step, 10)
    return round(qty, 6)


def _ceil_step(qty: float, step: float) -> float:
    if step and step > 0:
        n = int(qty / step)
        if n * step < qty - 1e-12:
            n += 1
        return round(n * step, 10)
    return round(qty, 6)


def plan(entry: float, sl: float, lev: float, *, min_qty: float = 0.0, qty_step: float = 0.0,
         exchange_free: Optional[float] = None, equity: Optional[float] = None,
         open_min_trades: int = 0, cfg: Optional[Dict] = None) -> Dict:
    """Mindest-Trade planen. Rückgabe {"ok", "margin", "qty", "risk", "note"|"reason"}."""
    cfg = {**DEFAULT_CONFIG, **(cfg or {})}
    if not cfg.get("enabled", True):
        return {"ok": False, "reason": "Mindest-Trade deaktiviert"}
    try:
        entry, sl, lev = float(entry), float(sl), max(1.0, float(lev or 1))
        min_qty, qty_step = float(min_qty or 0), float(qty_step or 0)
    except (TypeError, ValueError):
        return {"ok": False, "reason": "ungültige Eingaben"}
    if entry <= 0 or sl <= 0 or entry == sl:
        return {"ok": False, "reason": "Entry/SL ungültig"}
    max_open = int(cfg.get("max_open") or 0)
    if max_open and open_min_trades >= max_open:
        return {"ok": False, "reason": f"bereits {open_min_trades} Mindest-Trades offen (max {max_open})"}
    usable = capital_fit.usable_free(exchange_free, live=True)
    if usable is None or usable <= 0:
        return {"ok": False, "reason": "kein freies Börsen-Guthaben"}
    dist = abs(entry - sl)
    risk_cap = None
    if equity and equity > 0 and float(cfg.get("max_risk_pct") or 0) > 0:
        risk_cap = equity * float(cfg["max_risk_pct"]) / 100
    floor_qty = _ceil_step(min_qty, qty_step) if min_qty > 0 else 0.0
    target = max(0.0, float(cfg.get("target_margin_usdt") or 0))
    candidates: List[float] = []
    if target > 0:
        q = max(_floor_step(target * lev / entry, qty_step), floor_qty)
        if q > 0:
            candidates.append(q)
    if floor_qty > 0 and floor_qty not in candidates:
        candidates.append(floor_qty)
    if not candidates:
        return {"ok": False, "reason": "keine handelbare Mindestmenge bestimmbar"}
    last_reason = ""
    for qty in candidates:
        margin = round(qty * entry / lev * 1.005, 6)   # kleiner Rundungspuffer
        risk = dist * qty
        if margin > usable + 1e-9:
            last_reason = (f"Mindest-Marge {margin:.2f} USDT > frei {usable:.2f} USDT")
            continue
        if risk_cap is not None and risk > risk_cap + 1e-9:
            last_reason = (f"Mindest-Risiko {risk:.2f} USDT > {cfg['max_risk_pct']:g}% "
                           f"Equity ({risk_cap:.2f} USDT)")
            continue
        return {"ok": True, "margin": margin, "qty": qty, "risk": round(risk, 6),
                "note": (f"MINDEST-TRADE (live): {qty:g} @ {lev:g}x, Marge {margin:.2f} USDT, "
                         f"Risiko {risk:.2f} USDT")}
    return {"ok": False, "reason": last_reason or "Mindest-Trade nicht möglich"}


async def get_config(db) -> Dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache
    cfg = dict(DEFAULT_CONFIG)
    doc = await db.settings.find_one({"_id": CONFIG_ID}) if db is not None else None
    for k in DEFAULT_CONFIG:
        if doc and k in doc:
            cfg[k] = doc[k]
    _cfg_cache = cfg
    return cfg


async def update_config(db, updates: Dict) -> Dict:
    global _cfg_cache
    clean = {}
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            continue
        if k in _BOOL_KEYS:
            clean[k] = bool(v)
        else:
            try:
                clean[k] = int(v) if k == "max_open" else max(0.0, float(v))
            except (TypeError, ValueError):
                continue
            if k == "max_open":
                clean[k] = max(0, min(20, clean[k]))
    if clean:
        await db.settings.update_one({"_id": CONFIG_ID}, {"$set": clean}, upsert=True)
    _cfg_cache = None
    return await get_config(db)


async def open_count(db) -> int:
    try:
        return await db.auto_trades.count_documents(
            {"status": "open", "mode": "live", "min_trade": True})
    except Exception:  # noqa: BLE001
        return 0
