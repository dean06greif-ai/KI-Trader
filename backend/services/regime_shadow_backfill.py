"""Shadow-Phase beschleunigen: Struktur-Regime für bereits geschlossene KI-Trades
nachtragen (Regime-Brücke, Stufe `shadow`).

Warum das ohne Qualitätsverlust geht: das Struktur-Regime zum Entry-Zeitpunkt
ist deterministisch aus dem freigegebenen Lab-Modell und den Kerzen BIS zum
Entry berechenbar (Live-Erkennung ohne Zukunftswissen – exakt derselbe Pfad wie
`structural_regime.context_from_model`). In der Shadow-Stufe beeinflusst das
Regime keine Entscheidung, die Trades wären also identisch gelaufen. Statt
wochenlang auf 30 neue Trades je Regime zu warten, wird die vorhandene
Reward-Historie (Fenster wie `ai_rewards.by_structural_regime`) nachgelabelt.

Nachgetragene Zeilen sind markiert (`structural_source=backfill`, `structural_aid`).
Reine Funktionen oben (testbar), DB/Netz unten.
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

WINDOW_DAYS = 90
HISTORY_MARGIN_DAYS = 30      # wie structural_regime.DETECT_DAYS: Vorlauf vor dem ältesten Entry
MAX_REWARDS = 2000


def _parse_ts(value) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value if value > 1e11 else value * 1000)
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def entry_ts_of(trade: Dict) -> Optional[int]:
    """Entry-Zeit eines Trades in ms (opened_at bevorzugt, sonst Snapshot-/Close-Zeit)."""
    for key in ("opened_at", "entry_time", "created_at"):
        ts = _parse_ts((trade or {}).get(key))
        if ts:
            return ts
    return None


def candles_until(candles: List[Dict], entry_ts: int) -> List[Dict]:
    """Nur ABGESCHLOSSENE Kerzen vor dem Entry (kein Lookahead)."""
    return [c for c in candles if int(c.get("timestamp") or 0) < entry_ts]


def structural_label(model: Dict, candles: List[Dict], timeframe: str, tf_sec: int) -> Optional[str]:
    """'strukturell bär|bulle|seitwärts' oder None (zu wenig/lückige Daten)."""
    from services import market_context as mc
    from services import regime as rg
    from services import structural_regime as sr
    if len(candles) < 2 or not sr.candle_gap_ok(candles, tf_sec):
        return None
    cur = rg.current_regime(model, candles, timeframe)
    rid = cur.get("regime")
    if rid is None:
        return None
    mode = (model.get("config") or {}).get("regime_mode", model.get("regime_mode"))
    direction = mc.direction_from_regime_id(rid, mode)
    phase = mc.PHASE_BY_DIRECTION.get(direction or "")
    return f"strukturell {phase}" if phase else None


def plan_updates(rewards: List[Dict], trades_by_id: Dict[str, Dict], model: Dict,
                 candles: List[Dict], timeframe: str, tf_sec: int, aid: str) -> List[Tuple[str, Dict]]:
    """(reward_id, $set) je nachlabelbarem Reward – rein."""
    out = []
    for r in rewards:
        trade = trades_by_id.get(str(r.get("trade_id")))
        ts = entry_ts_of(trade) if trade else _parse_ts(r.get("ts"))
        if not ts:
            continue
        label = structural_label(model, candles_until(candles, ts), timeframe, tf_sec)
        if label:
            out.append((r["id"], {"structural_regime": label, "structural_source": "backfill",
                                  "structural_aid": aid}))
    return out


# ---------------- DB / Netz ----------------
async def backfill_structural(db, days: int = WINDOW_DAYS, only_aid: Optional[str] = None) -> Dict:
    """Rewards ohne Struktur-Regime im Fenster nachlabeln. Idempotent, fail-soft je Symbol."""
    import aiohttp
    from services import regime_lab as lab
    from services import setup_asset_class
    from services import structural_regime as sr
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles

    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rewards = await db.ai_rewards.find(
        {"ts": {"$gte": cutoff}, "structural_regime": None, "structural_source": {"$exists": False}},
        {"_id": 0, "id": 1, "trade_id": 1, "symbol": 1, "ts": 1}).to_list(MAX_REWARDS)
    summary = {"scanned": len(rewards), "labeled": 0, "skipped_no_release": 0, "symbols": {}}
    if not rewards:
        return summary
    by_symbol: Dict[str, List[Dict]] = {}
    for r in rewards:
        by_symbol.setdefault(str(r.get("symbol") or ""), []).append(r)
    trade_ids = [str(r["trade_id"]) for r in rewards if r.get("trade_id")]
    trades = await db.auto_trades.find({"id": {"$in": trade_ids}},
                                       {"_id": 0, "id": 1, "opened_at": 1, "created_at": 1}).to_list(len(trade_ids) or 1)
    trades_by_id = {str(t["id"]): t for t in trades}
    async with aiohttp.ClientSession() as session:
        for symbol, rows in by_symbol.items():
            if not symbol:
                continue
            doc = await sr._release_for(setup_asset_class.asset_class_of(symbol))
            if not doc or (only_aid and doc.get("id") != only_aid):
                summary["skipped_no_release"] += len(rows)
                continue
            rel = doc.get("release") or {}
            scope = rel.get("scope") or doc.get("scope") or "combined"
            model = lab.model_for(doc, scope, rel.get("symbol") if scope == "per_coin" else symbol)
            if not model:
                summary["skipped_no_release"] += len(rows)
                continue
            tf = str(doc.get("timeframe") or "1h")
            tf_sec = sr.tf_seconds(tf)
            try:
                oldest = min(_parse_ts(r.get("ts")) or 0 for r in rows)
                span_days = int((datetime.now(timezone.utc).timestamp() * 1000 - oldest) / 86400000) + HISTORY_MARGIN_DAYS
                raw = await fetch_history(session, symbol, max(span_days, HISTORY_MARGIN_DAYS))
                candles = aggregate_candles(raw, tf, drop_partial=True)
                del raw
                updates = plan_updates(rows, trades_by_id, model, candles, tf, tf_sec, doc["id"])
                for rid, sets in updates:
                    await db.ai_rewards.update_one({"id": rid}, {"$set": sets})
                summary["labeled"] += len(updates)
                summary["symbols"][symbol] = {"rewards": len(rows), "labeled": len(updates), "aid": doc["id"]}
            except Exception as e:  # noqa: BLE001
                logger.warning(f"shadow backfill {symbol}: {e}")
                summary["symbols"][symbol] = {"rewards": len(rows), "labeled": 0, "error": str(e)[:120]}
    return summary
