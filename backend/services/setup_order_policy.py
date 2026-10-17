"""KI-Trader: Order-Art (Maker-Limit vs. Market) je Setup.

Vorgabe je Setup (PRESETS) + Lernen aus echten Ergebnissen: der KI-Trader
weicht automatisch von der Vorgabe ab, wenn die Daten es zeigen, und schaltet
auch wieder zurück (Statistik rollierend). Manuelle Festlegung je Setup hat
immer Vorrang. Ausführung bleibt die bestehende Maker-Funktion
(bitunix_trade._maker_entry: Post-Only-Limit, Market-Nachschub bei No-Fill).
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional, Tuple

# Rücksetzer/Zonen/Range -> Preis kommt meist zurück -> Limit spart Gebühr.
# Ausbruch/Momentum/News/Sweep -> Kurs läuft weg -> Market.
MARKET_SETUPS = {"breakout", "squeeze_breakout", "momentum_news", "session_open",
                 "liquidity_sweep", "hedge", "fomc_event", "cpi_event", "nfp_event",
                 "ppi_event", "pce_event"}
MIN_ATTEMPTS = 8        # Maker-Versuche je Setup für Fill-Urteil
MIN_FILL = 0.3          # darunter -> Market
MIN_TRADES = 8          # geschlossene Trades je Order-Art für Performance-Urteil
MARGIN_R = 0.10         # Ø-R-Vorsprung, ab dem umgeschaltet wird
LOOKBACK_DAYS = 90
CACHE_S = 600
_cache: Dict = {"at": 0.0, "data": None}


def preset(setup: Optional[str]) -> str:
    return "market" if setup in MARKET_SETUPS else "maker"


def decide(setup: Optional[str], stats: Dict, overrides: Optional[Dict] = None) -> Tuple[str, str, str]:
    """(mode 'maker'|'market', Begründung, Quelle 'manual'|'preset'|'learned') – rein."""
    ov = (overrides or {}).get(setup or "")
    if ov in ("maker", "market"):
        return ov, "manuell festgelegt", "manual"
    base = preset(setup)
    s = (stats or {}).get(setup or "") or {}
    att, fills = int(s.get("attempts") or 0), int(s.get("fills") or 0)
    mk, mt = s.get("maker") or {}, s.get("market") or {}
    both = int(mk.get("n") or 0) >= MIN_TRADES and int(mt.get("n") or 0) >= MIN_TRADES
    if base == "maker":
        if att >= MIN_ATTEMPTS and fills / att < MIN_FILL:
            return "market", f"gelernt: nur {fills}/{att} Limits gefüllt", "learned"
        if both and mk["avg_r"] < mt["avg_r"] - MARGIN_R:
            return "market", f"gelernt: Limit Ø {mk['avg_r']:+.2f}R < Market Ø {mt['avg_r']:+.2f}R", "learned"
        return "maker", "Vorgabe: Rücksetzer/Zone – Limit spart Gebühr", "preset"
    if both and mk["avg_r"] > mt["avg_r"] + MARGIN_R and (not att or fills / att >= 0.5):
        return "maker", f"gelernt: Limit Ø {mk['avg_r']:+.2f}R > Market Ø {mt['avg_r']:+.2f}R", "learned"
    return "market", "Vorgabe: Ausbruch/Momentum – Einstieg nicht verpassen", "preset"


def _kind(order_kind: Optional[str]) -> Optional[str]:
    k = str(order_kind or "")
    if k.startswith("maker"):
        return "maker"
    if k in ("market", "taker_fallback", ""):
        return "market"
    return None


def aggregate(trades, attempts_by_setup: Dict) -> Dict[str, Dict]:
    """Je Setup: Maker-Fill-Quote + Ø-R je Order-Art (rein)."""
    out: Dict[str, Dict] = {}
    for t in trades:
        k, setup = _kind(t.get("order_kind")), t.get("setup")
        risk = float(t.get("risk_usdt") or 0)
        if not k or not setup or risk <= 0:
            continue
        r = float(t.get("realized_pnl") or 0) / risk
        b = out.setdefault(setup, {}).setdefault(k, {"n": 0, "sum_r": 0.0})
        b["n"] += 1
        b["sum_r"] += r
    for setup, rows in (attempts_by_setup or {}).items():
        out.setdefault(setup, {}).update(attempts=len(rows), fills=sum(1 for a in rows if a.get("filled")))
    for s in out.values():
        for k in ("maker", "market"):
            if k in s:
                s[k]["avg_r"] = round(s[k]["sum_r"] / s[k]["n"], 3)
    return out


async def stats(db, force: bool = False) -> Dict[str, Dict]:
    if not force and _cache["data"] is not None and time.time() - _cache["at"] < CACHE_S:
        return _cache["data"]
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    trades = await db.auto_trades.find(
        {"status": "closed", "setup": {"$nin": [None, ""]}, "opened_at": {"$gte": since}},
        {"_id": 0, "setup": 1, "order_kind": 1, "realized_pnl": 1, "risk_usdt": 1}).to_list(5000)
    doc = await db.settings.find_one({"_id": "maker_mode_stats"}, {"_id": 0, "by_setup": 1}) or {}
    data = aggregate(trades, doc.get("by_setup") or {})
    _cache.update(at=time.time(), data=data)
    return data


async def table(db, overrides: Optional[Dict] = None) -> list:
    from services import ai_playbook
    st = await stats(db)
    rows = []
    for setup in ai_playbook.SETUPS:
        mode, why, src = decide(setup, st, overrides)
        rows.append({"setup": setup, "preset": preset(setup), "mode": mode, "reason": why,
                     "source": src, "stats": st.get(setup) or {}})
    return rows
