"""Aktive Signale je Asset (für das Signal-Panel unter dem Chart).

Aktiv =
  * Signal mit eröffnetem Trade, solange der Trade offen ist (egal wie alt), oder
  * Signal ohne Trade (nur im Modus "Jedes Signal"), solange TP1/SL noch nicht
    berührt wurde und es nicht älter als FRESH_MIN Minuten ist.
Vorwarnungen (PRE_SIGNAL) und ausgewertete Signale sind nie aktiv. Jedes
Ergebnis trägt Setup + Trade-Modus (LIVE/PAPER/DATENSAMMLUNG).
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

FRESH_MIN = 60
LOOKBACK_DAYS = 14


def _age_min(ts, now: datetime) -> Optional[float]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return (now - d).total_seconds() / 60.0


def setup_label(setup: Optional[str], library: Optional[Dict] = None) -> Optional[str]:
    """Kurzname eines KI-Setups aus der Playbook-Beschreibung ("Trendfolge: …")."""
    if not setup:
        return None
    desc = str((library or {}).get(setup) or "")
    head = desc.split(":", 1)[0].strip()
    return head[:48] if head and len(head) < len(desc) else None


def select_active(signals: List[Dict], trades: Dict[str, Dict], only_traded: bool,
                  now: Optional[datetime] = None, library: Optional[Dict] = None) -> List[Dict]:
    """Aktive Signale filtern + anreichern (rein & testbar). `trades` = id -> Trade."""
    now = now or datetime.now(timezone.utc)
    out = []
    for s in signals:
        if s.get("signal_class") == "PRE_SIGNAL":
            continue
        trade = trades.get(s.get("trade_id")) if s.get("trade_id") else None
        if trade is not None:
            if trade.get("status") != "open":
                continue
            mode = "collection" if trade.get("data_collection") else (
                "live" if trade.get("mode") == "live" else "paper")
            state = "trade_open"
        else:
            if only_traded or s.get("status") == "closed" or s.get("result"):
                continue
            age = _age_min(s.get("timestamp"), now)
            if age is None or age > FRESH_MIN:
                continue
            mode, state = None, "no_trade"
        setup = s.get("trade_setup") or s.get("ai_setup") or (trade or {}).get("setup")
        out.append({**s, "active_state": state, "trade_mode": mode or s.get("trade_mode"),
                    "setup": setup, "setup_label": setup_label(setup, library),
                    "trade_status": (trade or {}).get("status"),
                    "trade_entry": (trade or {}).get("entry_price")})
    return out


async def load_active(db, symbol: str, strategy_id: Optional[str], only_traded: bool,
                      library: Optional[Dict] = None) -> List[Dict]:
    since = (datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)).isoformat()
    q = {"symbol": symbol, "timestamp": {"$gte": since}, "signal_class": {"$ne": "PRE_SIGNAL"}}
    if strategy_id:
        q["strategy_id"] = strategy_id
    sigs = await db.signals.find(q, {"_id": 0}).sort("timestamp", -1).to_list(60)
    ids = [s["trade_id"] for s in sigs if s.get("trade_id")]
    trades = {}
    if ids:
        async for t in db.auto_trades.find({"id": {"$in": ids}},
                                           {"_id": 0, "id": 1, "status": 1, "mode": 1,
                                            "data_collection": 1, "setup": 1, "entry_price": 1}):
            trades[t["id"]] = t
    return select_active(sigs, trades, only_traded, library=library)
