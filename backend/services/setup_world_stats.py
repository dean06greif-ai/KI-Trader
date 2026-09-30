"""Setup-Reife je Welt (Verlauf-Reiter): Trades / Winrate / PnL eines Setups
getrennt nach Echtgeld, Paper, Live-Logik gesamt (Echtgeld + Paper) und
Sammlung – im GLEICHEN Zeitfenster wie die Hauptspalten der Setup-Reife
(aktive Variante seit Rückstufung/Revision/Profil, sonst LOOKBACK_DAYS).

Rein informativ für die UI: Reife-Gate, Rückstufung und Lebenszyklus nutzen
weiterhin ausschließlich services/ai_playbook (keine Verhaltensänderung).
Reine Funktionen oben (unit-testbar), dünner DB-Wrapper unten.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from services import setup_asset_class as ac

# Welt-Schlüssel wie die Reiter des Verlauf-Panels (AIEquityPanel.MODES)
WORLDS = ("real", "paper", "live", "collection")
BASE_WORLDS = ("real", "paper", "collection")


def world_of(mode: Optional[str], data_collection) -> str:
    """Welt eines Trades: Sammel-Trades zählen immer zur Sammlung."""
    if data_collection is True:
        return "collection"
    return "real" if mode == "live" else "paper"


def _empty() -> Dict:
    return {"trades": 0, "wins": 0, "pnl": 0.0}


def _finish(b: Dict) -> Dict:
    n = int(b.get("trades") or 0)
    return {"trades": n, "wins": int(b.get("wins") or 0),
            "winrate": round(int(b.get("wins") or 0) / n * 100) if n else 0,
            "pnl": round(float(b.get("pnl") or 0), 2)}


def rows_to_worlds(rows: List[Dict]) -> Dict[str, Dict[str, Dict]]:
    """Aggregat-Zeilen {_id: {setup, dc, live}, trades, wins, pnl} ->
    {setup: {real|paper|collection: {trades, wins, pnl}}} (roh, summierbar)."""
    out: Dict[str, Dict[str, Dict]] = {}
    for r in rows or []:
        rid = r.get("_id") or {}
        sid = str(rid.get("setup") or "")
        if not sid:
            continue
        w = world_of("live" if rid.get("live") else None, True if rid.get("dc") else None)
        b = out.setdefault(sid, {k: _empty() for k in BASE_WORLDS})[w]
        b["trades"] += int(r.get("trades") or 0)
        b["wins"] += int(r.get("wins") or 0)
        b["pnl"] += float(r.get("pnl") or 0)
    return out


def merge_worlds(parts: List[Dict[str, Dict[str, Dict]]]) -> Dict[str, Dict[str, Dict]]:
    """Mehrere Klassen-Ergebnisse summieren (globale Tabelle)."""
    out: Dict[str, Dict[str, Dict]] = {}
    for part in parts:
        for sid, worlds in (part or {}).items():
            acc = out.setdefault(sid, {k: _empty() for k in BASE_WORLDS})
            for w in BASE_WORLDS:
                src = worlds.get(w) or {}
                acc[w]["trades"] += int(src.get("trades") or 0)
                acc[w]["wins"] += int(src.get("wins") or 0)
                acc[w]["pnl"] += float(src.get("pnl") or 0)
    return out


def display_worlds(raw: Optional[Dict[str, Dict]]) -> Dict[str, Dict]:
    """Roh-Welten eines Setups -> Anzeige inkl. 'live' (Echtgeld + Paper)."""
    raw = raw or {}
    real, paper = raw.get("real") or _empty(), raw.get("paper") or _empty()
    live = {"trades": int(real.get("trades") or 0) + int(paper.get("trades") or 0),
            "wins": int(real.get("wins") or 0) + int(paper.get("wins") or 0),
            "pnl": float(real.get("pnl") or 0) + float(paper.get("pnl") or 0)}
    return {"real": _finish(real), "paper": _finish(paper), "live": _finish(live),
            "collection": _finish(raw.get("collection") or _empty())}


def attach(rows: List[Dict], worlds: Dict[str, Dict[str, Dict]]) -> List[Dict]:
    """Jeder Setup-Reife-Zeile das Feld `worlds` geben (additiv, rein)."""
    for r in rows or []:
        r["worlds"] = display_worlds(worlds.get(r.get("setup")))
    return rows


def build_match(asset_class: Optional[str], since_map: Optional[Dict[str, str]],
                days: int, now: Optional[datetime] = None) -> Dict:
    """Match wie ai_playbook.setup_stats(_since_map): Setups mit aktiver
    Variante ab Varianten-Start, alle anderen ab dem Lookback-Cutoff."""
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).isoformat()
    match: Dict = {"strategy_id": "ai_trader", "status": "closed",
                   "setup": {"$nin": [None, ""]}}
    if asset_class:
        match["symbol"] = {"$in": ac.symbols_of(asset_class)}
    since_map = {k: v for k, v in (since_map or {}).items() if v}
    windows = [{"setup": sid, "opened_at": {"$gte": max(str(since), cutoff)}}
               for sid, since in since_map.items()]
    windows.append({"setup": {"$nin": list(since_map)}, "opened_at": {"$gte": cutoff}})
    match["$or"] = windows
    return match


GROUP_STAGE = {"$group": {
    "_id": {"setup": "$setup",
            "dc": {"$eq": [{"$ifNull": ["$data_collection", False]}, True]},
            "live": {"$eq": ["$mode", "live"]}},
    "trades": {"$sum": 1},
    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
    "pnl": {"$sum": {"$ifNull": ["$realized_pnl", 0]}}}}


async def class_worlds(db, asset_class: str, since_map: Optional[Dict[str, str]],
                       days: int) -> Dict[str, Dict[str, Dict]]:
    rows = await db.auto_trades.aggregate([
        {"$match": build_match(asset_class, since_map, days)}, GROUP_STAGE]).to_list(500)
    return rows_to_worlds(rows)
