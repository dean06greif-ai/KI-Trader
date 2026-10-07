"""Confluence: mehrere unabhängige Strategien zeigen gleichzeitig dieselbe
Richtung auf einem Coin -> erhöhte Trefferwahrscheinlichkeit.

Die Setups selbst bleiben komplett unverändert (jede Strategie signalisiert und
handelt eigenständig weiter). Confluence ist eine reine Beobachtungsebene:
1. Signal-Badge + Event (db.confluence_events) + Telegram-Meldung
2. Optionaler Kapital-Boost auf den Trade, der im Confluence-Moment feuert
   (KEIN zusätzlicher Trade -> kein doppeltes Risiko, 1-Trade-pro-Coin bleibt)
3. Kontext-Block für den KI-Trader (Konfidenz-Signal)
Trades mit Boost werden separat markiert (trade.confluence) -> /api/confluence/stats
vergleicht Confluence- vs. Normal-Trades, damit der Edge messbar ist.
"""
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_CONFIG_ID = "confluence_config"

DEFAULTS = {
    "enabled": True,
    "window_min": 15,        # Zeitfenster, in dem Signale als "gleichzeitig" gelten
    "min_strategies": 2,     # ab wie vielen übereinstimmenden Strategien
    "boost_enabled": True,
    "capital_boost": 1.5,    # Margin-Multiplikator für den Confluence-Trade
    "notify_enabled": True,  # eigene Telegram-Meldung je Event
    "ai_context_enabled": True,  # Events als Konfidenz-Signal in den KI-Kontext
}


async def get_config(db) -> Dict:
    doc = await db.settings.find_one({"_id": _CONFIG_ID}) or {}
    return {**DEFAULTS, **{k: v for k, v in doc.items() if k in DEFAULTS}}


async def save_config(db, updates: Dict) -> Dict:
    cur = await get_config(db)
    for k in DEFAULTS:
        if k in (updates or {}) and updates[k] is not None:
            cur[k] = updates[k]
    try:
        cur["window_min"] = max(1, min(120, int(cur["window_min"])))
        cur["min_strategies"] = max(2, min(6, int(cur["min_strategies"])))
        cur["capital_boost"] = max(1.0, min(3.0, float(cur["capital_boost"])))
    except (TypeError, ValueError):
        cur["window_min"], cur["min_strategies"], cur["capital_boost"] = 15, 2, 1.5
    for k in ("enabled", "boost_enabled", "notify_enabled", "ai_context_enabled"):
        cur[k] = bool(cur[k])
    await db.settings.update_one({"_id": _CONFIG_ID}, {"$set": cur}, upsert=True)
    return cur


async def check(db, signal: Dict) -> Optional[Dict]:
    """Prüft ein frisches Voll-Signal auf Confluence mit anderen Strategien.
    Liefert {"summary", "boost", "event", "new_event", "notify"} oder None."""
    cfg = await get_config(db)
    if not cfg["enabled"]:
        return None
    sid = signal.get("strategy_id")
    symbol, direction = signal.get("symbol"), signal.get("type")
    if not sid or not symbol or direction not in ("LONG", "SHORT"):
        return None
    cutoff = (datetime.now(timezone.utc)
              - timedelta(minutes=cfg["window_min"])).isoformat()
    cursor = db.signals.find(
        {"symbol": symbol, "type": direction, "signal_class": "SIGNAL",
         "timestamp": {"$gte": cutoff},
         "strategy_id": {"$nin": [sid, None]},
         "data_collection": {"$ne": True},
         "manual_trade": {"$ne": True}},
        {"strategy_id": 1, "strategy_name": 1}).sort("timestamp", -1).limit(50)
    partners: Dict[str, Dict] = {}
    async for doc in cursor:
        p = doc.get("strategy_id")
        if p and p not in partners:
            partners[p] = {"id": p, "name": doc.get("strategy_name") or p}
    total = len(partners) + 1
    if total < int(cfg["min_strategies"]):
        return None
    strategies = ([{"id": sid, "name": signal.get("strategy_name") or sid}]
                  + list(partners.values()))
    summary = {"count": total, "strategies": strategies,
               "window_min": cfg["window_min"]}
    boost = (float(cfg["capital_boost"])
             if cfg["boost_enabled"] and float(cfg["capital_boost"]) > 1.0 else None)
    # Dedupe: pro Symbol+Richtung nur ein Event je Fenster (kein Telegram-Spam)
    existing = await db.confluence_events.find_one(
        {"symbol": symbol, "direction": direction, "timestamp": {"$gte": cutoff}})
    new_event = existing is None
    event = None
    if new_event:
        event = {"id": str(uuid.uuid4()), "symbol": symbol, "direction": direction,
                 "strategies": strategies, "count": total,
                 "entry_price": signal.get("entry_price"), "boost": boost,
                 "timestamp": datetime.now(timezone.utc).isoformat()}
        await db.confluence_events.insert_one(dict(event))
        logger.info(f"Confluence: {symbol} {direction} – {total} Strategien "
                    f"({', '.join(s['id'] for s in strategies)})")
    return {"summary": summary, "boost": boost, "event": event,
            "new_event": new_event, "notify": bool(cfg["notify_enabled"])}


async def notify(telegram, event: Dict) -> bool:
    if not event or not telegram or not getattr(telegram, "bot", None) \
            or not getattr(telegram, "chat_id", None):
        return False
    names = " + ".join(s["name"] for s in event.get("strategies", []))
    arrow = "📈" if event["direction"] == "LONG" else "📉"
    boost_line = (f"💪 Kapital-Boost ×{event['boost']:g} aktiv"
                  if event.get("boost") else "Kapital-Boost: aus")
    msg = (f"⚡ *CONFLUENCE* {arrow} *{event['symbol']}* {event['direction']}\n"
           f"{event['count']} Strategien zeigen dieselbe Richtung:\n"
           f"{names}\n{boost_line}")
    try:
        from telegram.constants import ParseMode
        await telegram.bot.send_message(chat_id=telegram.chat_id, text=msg,
                                        parse_mode=ParseMode.MARKDOWN)
        return True
    except Exception as e:
        logger.warning(f"Confluence-Telegram fehlgeschlagen: {e}")
        return False


async def recent_events(db, limit: int = 50) -> List[Dict]:
    cur = db.confluence_events.find({}, {"_id": 0}) \
        .sort("timestamp", -1).limit(max(1, min(200, int(limit))))
    return [doc async for doc in cur]


async def trade_stats(db) -> Dict:
    """Confluence- vs. Normal-Trades vergleichen (Edge messbar machen)."""
    async def _agg(match: Dict) -> Dict:
        total = await db.auto_trades.count_documents(match)
        closed_match = {**match, "status": "closed"}
        closed = await db.auto_trades.count_documents(closed_match)
        wins = await db.auto_trades.count_documents({**closed_match, "pnl": {"$gt": 0}})
        pnl = 0.0
        async for d in db.auto_trades.find(closed_match, {"pnl": 1}):
            try:
                pnl += float(d.get("pnl") or 0)
            except (TypeError, ValueError):
                pass
        return {"total": total, "closed": closed, "wins": wins,
                "win_rate": round(wins / closed * 100, 1) if closed else 0.0,
                "pnl": round(pnl, 2)}
    return {"confluence": await _agg({"confluence": {"$ne": None}}),
            "normal": await _agg({"confluence": None})}


async def ai_context_block(db) -> str:
    """Kontext-Block für den KI-Trader: Confluence-Events der letzten 24h."""
    cfg = await get_config(db)
    if not cfg["enabled"] or not cfg["ai_context_enabled"]:
        return ""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    cur = db.confluence_events.find({"timestamp": {"$gte": cutoff}}, {"_id": 0}) \
        .sort("timestamp", -1).limit(10)
    rows = [doc async for doc in cur]
    if not rows:
        return ""
    lines = []
    for e in rows:
        names = " + ".join(s.get("name", s.get("id", "?"))
                           for s in e.get("strategies", []))
        lines.append(f"- {str(e.get('timestamp', ''))[:16]} {e['symbol']} "
                     f"{e['direction']}: {names}")
    return ("=== CONFLUENCE-EREIGNISSE (24h) ===\n"
            "Mehrere unabhängige Strategien zeigten gleichzeitig dieselbe Richtung "
            "(erhöhte Trefferwahrscheinlichkeit – als zusätzliches Konfidenz-Signal "
            "werten, wenn deine Analyse dieselbe Richtung stützt):\n"
            + "\n".join(lines))
