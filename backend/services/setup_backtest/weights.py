"""Gewichtung Backtest- vs. echte Trades für das Reife-Gate (rein & testbar).

Grundsatz: Backtest-Trades sind eine BESCHLEUNIGUNG, kein Ersatz.
  * Backtest-Trade zählt mit BACKTEST_WEIGHT (0.5) -> 2 Backtest = 1 Paper-Trade.
  * Der Backtest-Anteil ist auf MAX_BACKTEST_WEIGHTED gewichtete Trades gedeckelt,
    sodass immer mindestens MIN_REAL_TRADES echte, in Summe profitable
    Paper-Trades nötig bleiben (MIN_TRADES_PROMOTE - MAX_BACKTEST_WEIGHTED).
  * Nie für live_stats, Rückstufung, Divergenz-Gate oder Kapital-Eskalation.
  * Backtest-Trades verfallen nach BACKTEST_TTL_DAYS.
"""
import math
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from services import setup_lifecycle as lifecycle

BACKTEST_WEIGHT = 0.5
BACKTEST_TTL_DAYS = 60
MIN_REAL_TRADES = 2
MAX_BACKTEST_WEIGHTED = lifecycle.MIN_TRADES_PROMOTE - MIN_REAL_TRADES  # 3
COLLECTION = "setup_backtest_trades"


def boost_allowed(real: Optional[Dict]) -> bool:
    """Echte Paper-Trades bleiben Pflicht: mind. MIN_REAL_TRADES mit PnL > 0."""
    n = int((real or {}).get("trades") or 0)
    return n >= MIN_REAL_TRADES and float((real or {}).get("pnl") or 0) > 0


def merge_stats(real: Optional[Dict], bt: Optional[Dict]) -> Optional[Dict]:
    """Gewichtete Statistik (echt + gedeckelter Backtest-Anteil) oder None,
    wenn der Backtest nichts beitragen darf."""
    if not bt or not int(bt.get("trades") or 0) or not boost_allowed(real):
        return None
    real = dict(real or {})
    n_bt = int(bt["trades"])
    w_total = min(MAX_BACKTEST_WEIGHTED, n_bt * BACKTEST_WEIGHT)
    scale = w_total / n_bt                      # Anteil je Backtest-Trade nach Deckelung
    trades = int(real.get("trades") or 0) + math.floor(w_total)
    wins = int(real.get("wins") or 0) + math.floor(int(bt.get("wins") or 0) * scale)
    pnl = float(real.get("pnl") or 0) + float(bt.get("pnl") or 0) * scale
    margin = float(real.get("margin") or 0) + float(bt.get("margin") or 0) * scale
    return {"trades": trades, "wins": wins, "pnl": round(pnl, 2), "margin": round(margin, 2),
            "verdict": lifecycle_verdict(trades, wins, pnl),
            "backtest_trades": n_bt, "backtest_weighted": round(w_total, 1),
            "real_trades": int(real.get("trades") or 0)}


def lifecycle_verdict(trades: int, wins: int, pnl: float) -> str:
    from services import ai_playbook  # lazy: Zyklus vermeiden
    return ai_playbook.verdict_for(trades, wins, pnl)


def cutoff_iso(now: Optional[datetime] = None) -> str:
    return ((now or datetime.now(timezone.utc)) - timedelta(days=BACKTEST_TTL_DAYS)).isoformat()


async def class_backtest_stats(db, asset_class: str) -> Dict[str, Dict]:
    """Backtest-Trades je Setup einer Klasse (nur gespeicherte OOS-Trades,
    innerhalb der TTL)."""
    rows = await db[COLLECTION].aggregate([
        {"$match": {"asset_class": asset_class, "run_at": {"$gte": cutoff_iso()}}},
        {"$group": {"_id": "$setup", "trades": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$gt": ["$realized_pnl", 0]}, 1, 0]}},
                    "pnl": {"$sum": "$realized_pnl"},
                    "margin": {"$sum": {"$ifNull": ["$margin_used", 0]}}}},
    ]).to_list(100)
    return {str(r["_id"]): {"trades": int(r["trades"]), "wins": int(r["wins"]),
                            "pnl": round(float(r.get("pnl") or 0), 2),
                            "margin": round(float(r.get("margin") or 0), 2)} for r in rows}
