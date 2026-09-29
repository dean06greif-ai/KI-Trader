"""Slippage-Wächter: kein Live-Einstieg in Momentum-Setups, wenn die zuletzt
GEMESSENE Entry-Slippage des Coins zu hoch ist.

Befund 02.09.2026: squeeze_breakout lief im Paper mit 48 % Winrate, live mit
7 % – der Unterschied war fast komplett Ausführung (Signalpreis vs. Fill:
DOGE 0,86 %, GOLD 1,16 %), nicht Strategie. Momentum-/Breakout-Einstiege
kaufen per Definition in die Bewegung hinein – dort ist Slippage am größten.

Der Wächter greift NUR live (Sammel-Trades messen weiter) und nur für die
Setups in MOMENTUM_SETUPS (optional: alle). Grundlage sind die echten
`slippage_pct`-Messwerte der letzten Live-Trades des Coins (Baustein B).
Reine Funktionen unten sind testbar; die DB-Abfrage ist dünn und gecacht.
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

MOMENTUM_SETUPS = ("momentum_news", "breakout", "squeeze_breakout")

DEFAULTS = {
    "slippage_guard_enabled": True,
    "slippage_guard_max_pct": 0.3,      # Ø gemessene Slippage des Coins (%, 0 = aus)
    "slippage_guard_min_trades": 3,     # erst ab so vielen Messwerten urteilen
    "slippage_guard_days": 14,          # Messfenster
    "slippage_guard_all_setups": False, # False = nur Momentum-Setups
}

CACHE_SEC = 300
_cache: Dict[str, Dict] = {}   # symbol -> {"ts", "stats"}


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    """update_config-Klemmen (mutiert cfg)."""
    if "slippage_guard_enabled" in updates:
        cfg["slippage_guard_enabled"] = bool(updates["slippage_guard_enabled"])
    if "slippage_guard_all_setups" in updates:
        cfg["slippage_guard_all_setups"] = bool(updates["slippage_guard_all_setups"])
    for key, lo, hi, cast in (("slippage_guard_max_pct", 0.0, 5.0, float),
                              ("slippage_guard_min_trades", 1, 50, int),
                              ("slippage_guard_days", 1, 90, int)):
        if key in updates:
            try:
                cfg[key] = cast(max(lo, min(hi, float(updates[key]))))
            except (TypeError, ValueError):
                pass


def slippage_stats(rows: List[Dict]) -> Dict:
    """Ø und Maximum der gemessenen (schlechteren) Slippage in % (rein).
    Negative Werte (besserer Fill) zählen als 0 – sie kompensieren keine
    Ausrutscher, die Frage ist 'wie teuer wird der Einstieg im Schnitt'."""
    vals = []
    for r in rows:
        try:
            vals.append(max(float(r.get("slippage_pct")), 0.0))
        except (TypeError, ValueError):
            continue
    if not vals:
        return {"n": 0, "avg_pct": 0.0, "max_pct": 0.0}
    return {"n": len(vals), "avg_pct": round(sum(vals) / len(vals), 4),
            "max_pct": round(max(vals), 4)}


def applies_to(setup: Optional[str], cfg: Dict) -> bool:
    if not cfg.get("slippage_guard_enabled", DEFAULTS["slippage_guard_enabled"]):
        return False
    if float(cfg.get("slippage_guard_max_pct", DEFAULTS["slippage_guard_max_pct"]) or 0) <= 0:
        return False
    if cfg.get("slippage_guard_all_setups", False):
        return True
    return str(setup or "") in MOMENTUM_SETUPS


def block_reason(symbol: str, setup: Optional[str], stats: Dict, cfg: Dict) -> Optional[str]:
    """Sperrgrund oder None (rein & testbar)."""
    if not applies_to(setup, cfg):
        return None
    min_n = int(cfg.get("slippage_guard_min_trades", DEFAULTS["slippage_guard_min_trades"]) or 1)
    if int(stats.get("n") or 0) < min_n:
        return None
    limit = float(cfg.get("slippage_guard_max_pct", DEFAULTS["slippage_guard_max_pct"]))
    avg = float(stats.get("avg_pct") or 0)
    if avg <= limit:
        return None
    return (f"Slippage-Wächter: {symbol} hatte zuletzt Ø {avg:.2f}% Entry-Slippage "
            f"({stats['n']} Live-Trades, max {stats.get('max_pct', 0):.2f}%) – Limit {limit:g}% "
            f"für Setup '{setup}' – Live-Entry ausgelassen, Sammel-Messung läuft weiter")


async def symbol_stats(db, symbol: str, cfg: Dict) -> Dict:
    """Gemessene Slippage der letzten Live-Trades des Coins (5-min-Cache)."""
    now = time.time()
    hit = _cache.get(symbol)
    if hit and now - hit["ts"] < CACHE_SEC:
        return hit["stats"]
    days = int(cfg.get("slippage_guard_days", DEFAULTS["slippage_guard_days"]) or 14)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.auto_trades.find(
        {"symbol": symbol, "mode": "live", "data_collection": {"$ne": True},
         "opened_at": {"$gte": cutoff}, "slippage_pct": {"$ne": None}},
        {"_id": 0, "slippage_pct": 1}).sort("opened_at", -1).to_list(30)
    stats = slippage_stats(rows)
    _cache[symbol] = {"ts": now, "stats": stats}
    return stats


def invalidate(symbol: Optional[str] = None) -> None:
    if symbol is None:
        _cache.clear()
    else:
        _cache.pop(symbol, None)
