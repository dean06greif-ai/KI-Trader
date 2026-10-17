"""Regelbasierte Fehlerdiagnose je Setup × Anlageklasse (kein LLM).

Liefert 3-5 kompakte Zeilen, WARUM ein Setup Verluste macht – als Input für die
KI-Revision (ai_playbook.context_text, Block RÜCKGESTUFT) und die UI (Tooltip).
Datenbasis: geschlossene auto_trades (entry, initial_sl, exit_price, peak/trough,
opened_at/closed_at, timeframe, side).
"""
from datetime import datetime
from typing import Dict, List, Optional

from services import setup_asset_class as ac

MIN_TRADES = 4
QUICK_LOSS_MIN = 5.0     # Verlust in < 5 Minuten = SL zu eng / Timing falsch
SL_HIT_TOL = 0.15        # Exit innerhalb 15 % der SL-Distanz vom SL = SL-Hit


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _minutes(t: Dict) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(t.get("opened_at")))
        b = datetime.fromisoformat(str(t.get("closed_at")))
        return (b - a).total_seconds() / 60.0
    except (TypeError, ValueError):
        return None


def _session(t: Dict) -> Optional[str]:
    try:
        h = datetime.fromisoformat(str(t.get("opened_at"))).hour  # UTC
    except (TypeError, ValueError):
        return None
    return "Asia" if h < 7 else "London" if h < 13 else "US" if h < 21 else "Late-US"


def _sl_hit(t: Dict) -> Optional[bool]:
    entry, sl, ex = _f(t.get("entry")), _f(t.get("initial_sl") or t.get("sl")), _f(t.get("exit_price"))
    if not entry or not sl or not ex or entry == sl:
        return None
    return abs(ex - sl) <= abs(entry - sl) * SL_HIT_TOL


def _mfe_pct(t: Dict) -> Optional[float]:
    """Max. Gewinn-Ausflug (%) vor dem Verlust – 'war der Trade mal im Plus?'"""
    entry = _f(t.get("entry"))
    if not entry:
        return None
    side = str(t.get("side") or "").upper()
    ext = _f(t.get("peak_price")) if side == "LONG" else _f(t.get("trough_price"))
    if ext is None:
        return None
    return (ext - entry) / entry * 100.0 * (1 if side == "LONG" else -1)


def diagnose(trades: List[Dict]) -> List[str]:
    """Kompakte Befunde (rein & testbar). [] bei zu wenig Daten."""
    closed = [t for t in trades if _f(t.get("realized_pnl")) is not None]
    losers = [t for t in closed if float(t["realized_pnl"]) < 0]
    if len(closed) < MIN_TRADES or not losers:
        return []
    n, nl = len(closed), len(losers)
    out: List[str] = []
    hits = [_sl_hit(t) for t in losers]
    known = [h for h in hits if h is not None]
    if known:
        share = sum(known) / len(known)
        if share >= 0.6:
            out.append(f"{share:.0%} der Verluste = SL-Hit (SL vermutlich zu eng/an offensichtlicher Stelle)")
        elif share <= 0.3:
            out.append(f"nur {share:.0%} der Verluste per SL – Rest manuell/Zeit/KI-Exit: Exit-Regeln prüfen")
    mins = [m for m in (_minutes(t) for t in losers) if m is not None]
    if mins:
        quick = sum(1 for m in mins if m < QUICK_LOSS_MIN) / len(mins)
        if quick >= 0.4:
            out.append(f"{quick:.0%} der Verluste in <{QUICK_LOSS_MIN:.0f} Min → Einstieg zu früh/ohne Bestätigung")
    mfes = [m for m in (_mfe_pct(t) for t in losers) if m is not None]
    if mfes:
        in_plus = sum(1 for m in mfes if m > 0.15) / len(mfes)
        if in_plus >= 0.5:
            out.append(f"{in_plus:.0%} der Verlierer waren >0.15% im Plus → TP1/Breakeven früher setzen")
    sess = {}
    for t in losers:
        s = _session(t)
        if s:
            sess[s] = sess.get(s, 0) + 1
    if sess:
        top, cnt = max(sess.items(), key=lambda x: x[1])
        tot = sum(1 for t in closed if _session(t) == top)
        if cnt / nl >= 0.5 and tot and cnt / tot >= 0.6:
            out.append(f"{cnt}/{tot} Trades in {top}-Session verloren → Session meiden/Fenster einschränken")
    tfs = {}
    for t in losers:
        tf = str(t.get("timeframe") or "")
        if tf:
            tfs[tf] = tfs.get(tf, 0) + 1
    if tfs:
        top, cnt = max(tfs.items(), key=lambda x: x[1])
        tot = sum(1 for t in closed if str(t.get("timeframe") or "") == top)
        if cnt / nl >= 0.6 and tot >= 3 and cnt / tot >= 0.7:
            out.append(f"TF {top}: {cnt}/{tot} verloren → anderen Timeframe nutzen")
    sides = {s: sum(1 for t in losers if str(t.get("side") or "").upper() == s) for s in ("LONG", "SHORT")}
    for s, cnt in sides.items():
        tot = sum(1 for t in closed if str(t.get("side") or "").upper() == s)
        if tot >= 3 and cnt / tot >= 0.75 and cnt / nl >= 0.6:
            out.append(f"{s}: {cnt}/{tot} verloren → Richtung nur mit HTF-Bestätigung")
    if not out:
        out.append(f"{nl}/{n} Verluste ohne klares Muster (SL/Timing/Session unauffällig) → Setup-Logik selbst prüfen")
    return out[:5]


async def for_setup(db, asset_class: str, setup: str, since: Optional[str] = None,
                    days: int = 30) -> List[str]:
    """Diagnose-Zeilen für ein Setup in einer Klasse aus der DB."""
    from datetime import timedelta, timezone
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    if since and str(since) > cutoff:
        cutoff = str(since)
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "setup": setup,
         "symbol": {"$in": ac.symbols_of(asset_class)}, "opened_at": {"$gte": cutoff}},
        {"_id": 0, "entry": 1, "initial_sl": 1, "sl": 1, "exit_price": 1, "peak_price": 1,
         "trough_price": 1, "opened_at": 1, "closed_at": 1, "timeframe": 1, "side": 1,
         "realized_pnl": 1}).to_list(500)
    return diagnose(rows)
