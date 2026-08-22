"""Registry für offene Entry-Limit-Orders (KI-Trader Maker-Modus).

Bug-Report: KI-Limit-Orders, die erst NACH dem Warte-Timeout (oder nach einer
fehlgeschlagenen Stornierung) gefüllt wurden, erzeugten Börsen-Positionen ohne
lokalen Trade. Der Positions-Watchdog übernahm sie dann fälschlich als
'Manuell (Bitunix)' – ohne Telegram-Signal und ohne KI-Zuordnung.

Diese Registry merkt sich jede platzierte Entry-Limit-Order samt Kontext
(Strategie, SL/TP, Hebel, Kapital). Der Watchdog kann eine unbekannte
Börsen-Position damit ihrer Ursprungs-Order zuordnen und als echten KI-Trade
übernehmen. Einträge werden beim Fill/verifizierten Cancel aufgelöst und
nach `MAX_AGE_H` Stunden automatisch bereinigt (inkl. Cancel-Versuch).
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

logger = logging.getLogger(__name__)

MAX_AGE_H = 36.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cutoff_iso(hours: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


async def register(db, *, order_id: str, symbol: str, side: str, qty: float,
                   price: float, meta: Optional[Dict] = None,
                   status: str = "waiting") -> None:
    doc = {"order_id": str(order_id), "symbol": symbol,
           "side": str(side).upper(), "qty": float(qty), "price": float(price),
           "meta": dict(meta or {}), "status": status, "created_at": _now_iso()}
    await db.pending_entry_orders.update_one(
        {"order_id": str(order_id)}, {"$set": doc}, upsert=True)


async def resolve(db, order_id: str) -> None:
    """Order ist gefüllt oder sicher storniert -> Eintrag entfernen."""
    await db.pending_entry_orders.delete_one({"order_id": str(order_id)})


async def mark_orphan(db, order_id: str, reason: str = "") -> None:
    """Order konnte nicht verifiziert storniert werden – bleibt überwacht."""
    await db.pending_entry_orders.update_one(
        {"order_id": str(order_id)},
        {"$set": {"status": "orphan", "orphan_reason": str(reason)[:160],
                  "orphaned_at": _now_iso()}})


async def find_match(db, symbol: str, side: str,
                     max_age_h: float = MAX_AGE_H) -> Optional[Dict]:
    """Jüngste registrierte Entry-Order zu Symbol+Seite (für den Watchdog)."""
    rows = await db.pending_entry_orders.find(
        {"symbol": symbol, "side": str(side).upper(),
         "created_at": {"$gte": _cutoff_iso(max_age_h)}}
    ).sort("created_at", -1).to_list(5)
    return rows[0] if rows else None


async def cleanup(db, client=None, max_age_h: float = MAX_AGE_H) -> int:
    """Veraltete Einträge bereinigen: Cancel-Versuch an der Börse + löschen."""
    stale = await db.pending_entry_orders.find(
        {"created_at": {"$lt": _cutoff_iso(max_age_h)}}).to_list(50)
    removed = 0
    for row in stale:
        if client is not None:
            try:
                await client.cancel_orders(row["symbol"], [row["order_id"]])
            except Exception as e:
                logger.debug(f"Registry-Cleanup Cancel {row.get('symbol')}: {e}")
        await db.pending_entry_orders.delete_one({"order_id": row["order_id"]})
        removed += 1
    if removed:
        logger.info(f"Entry-Order-Registry: {removed} veraltete Einträge bereinigt")
    return removed
