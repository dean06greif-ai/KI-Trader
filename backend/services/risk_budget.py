"""Gesamt-Risikobudget offener Positionen (Audit E1, Phase 1.6).

Bisher gab es nur Kapital-Caps und `max_open_trades` je Welt – kein Limit für
das GLEICHZEITIG offene Verlustrisiko (Distanz zum Stop × Menge) über alle
Positionen eines Modus. Korrelierte Krypto-Longs konnten so das Tagesverlust-
Limit in einem Move sprengen.

Regel (je Modus live/paper, Sammel-Trades zählen nie):
    offenes Risiko (USDT) + Risiko des neuen Trades  <=  max_portfolio_risk_pct % × Equity
    zusätzlich je Anlageklasse (Cluster):            <=  max_cluster_risk_pct % × Equity

Risiko eines offenen Trades = max(0, Abstand Entry->aktueller SL) × Restmenge
(SL im Gewinn -> 0 Risiko). Equity unbekannt -> fail-open mit Log (kein Block
auf Basis fehlender Daten). Konfiguration in settings `risk_budget_config`,
abschaltbar (`enabled`).
"""
import logging
from typing import Dict, List, Optional, Tuple

from core import instruments as _instruments

logger = logging.getLogger(__name__)

CONFIG_ID = "risk_budget_config"
DEFAULT_CONFIG = {
    "enabled": True,
    "max_portfolio_risk_pct": 6.0,   # % Equity, Summe offenes Risiko + neuer Trade
    "max_cluster_risk_pct": 4.0,     # % Equity je Anlageklasse (0 = aus)
}
_cfg_cache: Optional[Dict] = None


def trade_risk_usdt(t: Dict) -> float:
    """Aktuelles Verlustrisiko eines offenen Trades bis zum Stop (rein)."""
    try:
        entry = float(t.get("entry") or 0)
        sl = float(t.get("sl") or 0)
        qty = float(t.get("qty_remaining", t.get("qty")) or 0)
    except (TypeError, ValueError):
        return 0.0
    if entry <= 0 or sl <= 0 or qty <= 0:
        return 0.0
    dist = (entry - sl) if str(t.get("side", "")).upper() == "LONG" else (sl - entry)
    return max(0.0, dist) * qty


def cluster_of(symbol: str) -> str:
    inst = _instruments.get(symbol)
    return str(getattr(inst, "group", None) or _instruments.GROUP_CRYPTO)


def open_risk(trades: List[Dict], mode: str) -> Tuple[float, Dict[str, float]]:
    """(Gesamtrisiko, Risiko je Cluster) der offenen Risiko-Trades des Modus (rein)."""
    total = 0.0
    by_cluster: Dict[str, float] = {}
    for t in trades or []:
        if t.get("status") != "open" or t.get("mode") != mode or t.get("data_collection"):
            continue
        r = trade_risk_usdt(t)
        if r <= 0:
            continue
        total += r
        c = cluster_of(t.get("symbol"))
        by_cluster[c] = by_cluster.get(c, 0.0) + r
    return round(total, 6), by_cluster


def check(new_risk_usdt: float, symbol: str, open_total: float, by_cluster: Dict[str, float],
          equity: Optional[float], cfg: Dict) -> Tuple[bool, str]:
    """Darf ein neuer Trade mit `new_risk_usdt` eröffnet werden? (rein)"""
    if not cfg.get("enabled", True):
        return True, ""
    if equity is None or equity <= 0:
        logger.warning("Risikobudget: Equity unbekannt – Prüfung übersprungen (fail-open)")
        return True, ""
    new_risk = max(0.0, float(new_risk_usdt or 0))
    pmax = float(cfg.get("max_portfolio_risk_pct") or 0)
    if pmax > 0:
        limit = equity * pmax / 100
        if open_total + new_risk > limit + 1e-9:
            return False, (f"Risikobudget: offenes Risiko {open_total:.2f} + neu {new_risk:.2f} "
                           f"= {open_total + new_risk:.2f} USDT > {pmax:g}% Equity "
                           f"({limit:.2f} USDT)")
    cmax = float(cfg.get("max_cluster_risk_pct") or 0)
    if cmax > 0:
        c = cluster_of(symbol)
        cur = by_cluster.get(c, 0.0)
        limit = equity * cmax / 100
        if cur + new_risk > limit + 1e-9:
            return False, (f"Risikobudget ({c}): Cluster-Risiko {cur:.2f} + neu {new_risk:.2f} "
                           f"USDT > {cmax:g}% Equity ({limit:.2f} USDT)")
    return True, ""


async def get_config(db) -> Dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache
    cfg = dict(DEFAULT_CONFIG)
    doc = await db.settings.find_one({"_id": CONFIG_ID}) if db is not None else None
    if doc:
        for k in DEFAULT_CONFIG:
            if k in doc:
                cfg[k] = doc[k]
    _cfg_cache = cfg
    return cfg


async def update_config(db, updates: Dict) -> Dict:
    global _cfg_cache
    clean = {}
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            continue
        if k == "enabled":
            clean[k] = bool(v)
        else:
            try:
                clean[k] = max(0.0, float(v))
            except (TypeError, ValueError):
                continue
    if clean:
        await db.settings.update_one({"_id": CONFIG_ID}, {"$set": clean}, upsert=True)
    _cfg_cache = None
    return await get_config(db)


async def usage(db, mode: str) -> Dict:
    """Aktuelle Auslastung des Budgets (für API/UI)."""
    rows = await db.auto_trades.find(
        {"status": "open", "mode": mode, "data_collection": {"$ne": True}},
        {"_id": 0, "entry": 1, "sl": 1, "qty": 1, "qty_remaining": 1, "side": 1,
         "symbol": 1, "status": 1, "mode": 1, "data_collection": 1}).to_list(500)
    total, by_cluster = open_risk(rows, mode)
    return {"mode": mode, "open_risk_usdt": total,
            "by_cluster": {k: round(v, 6) for k, v in by_cluster.items()},
            "open_trades": len(rows)}


async def check_new_trade(db, mode: str, symbol: str, new_risk_usdt: float,
                          equity: Optional[float]) -> Tuple[bool, str]:
    cfg = await get_config(db)
    if not cfg.get("enabled", True):
        return True, ""
    u = await usage(db, mode)
    return check(new_risk_usdt, symbol, u["open_risk_usdt"], u["by_cluster"], equity, cfg)
