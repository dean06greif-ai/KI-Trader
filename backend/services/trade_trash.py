"""Papierkorb für Trades: Lösch-Endpunkte (analytics/clear, ai/trader/reset)
entfernen auto_trades nicht mehr endgültig, sondern verschieben sie als Batch in
`auto_trades_trash`. Jeder Batch ist per Klick wiederherstellbar.

Hintergrund: Vorfall 05.09.2026 – ein Test-Lauf gegen die Produktiv-DB hat
Paper-Trades unwiederbringlich gelöscht. Analog zu services/lab_trash.py."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COLLECTION = "auto_trades_trash"
KEEP_BATCHES = 30


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def archive(db, trade_filter: Dict, reason: str, actor: str = "") -> Dict:
    """Trades passend zum Filter in den Papierkorb verschieben. Rückgabe:
    {'batch_id', 'count'} – count 0 => nichts gelöscht, kein Batch angelegt."""
    docs = await db.auto_trades.find(trade_filter, {"_id": 0}).to_list(200000)
    if not docs:
        return {"batch_id": None, "count": 0}
    batch_id = uuid.uuid4().hex[:12]
    await db[COLLECTION].insert_one({
        "id": batch_id, "deleted_at": _now_iso(), "reason": reason, "actor": actor,
        "count": len(docs), "filter": {k: str(v) for k, v in trade_filter.items()},
        "modes": sorted({str(d.get("mode")) for d in docs}),
        "trades": docs})
    await db.auto_trades.delete_many({"id": {"$in": [d["id"] for d in docs if d.get("id")]}})
    try:
        old = await (db[COLLECTION].find({}, {"id": 1}).sort("deleted_at", -1)
                     .skip(KEEP_BATCHES).to_list(100))
        if old:
            await db[COLLECTION].delete_many({"id": {"$in": [r["id"] for r in old]}})
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Trade-Papierkorb Kappung fehlgeschlagen: {e}")
    logger.info(f"Trade-Papierkorb: {len(docs)} Trades archiviert ({reason}, Batch {batch_id})")
    return {"batch_id": batch_id, "count": len(docs)}


async def list_batches(db) -> List[Dict]:
    return await (db[COLLECTION].find({}, {"_id": 0, "trades": 0})
                  .sort("deleted_at", -1).to_list(KEEP_BATCHES))


async def restore(db, batch_id: str) -> Optional[Dict]:
    """Batch zurück nach auto_trades (nur IDs, die dort noch nicht existieren)."""
    doc = await db[COLLECTION].find_one({"id": batch_id})
    if not doc:
        return None
    trades = doc.get("trades") or []
    existing = {t["id"] for t in await db.auto_trades.find(
        {"id": {"$in": [t.get("id") for t in trades]}}, {"id": 1}).to_list(len(trades) + 1)}
    todo = [{k: v for k, v in t.items() if k != "_id"} for t in trades
            if t.get("id") and t["id"] not in existing]
    if todo:
        await db.auto_trades.insert_many(todo)
    await db[COLLECTION].delete_one({"id": batch_id})
    return {"batch_id": batch_id, "restored": len(todo), "skipped_existing": len(trades) - len(todo)}


async def discard(db, batch_id: str) -> bool:
    res = await db[COLLECTION].delete_one({"id": batch_id})
    return res.deleted_count > 0
