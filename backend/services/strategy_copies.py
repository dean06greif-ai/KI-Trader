"""Unabhängige Strategie-Kopien anlegen (Duplizieren in „Strategien verwalten“
und „Ergebnis sichern“ im Strategie-Optimizer nutzen denselben Weg).

  * Custom-/Discovery-Strategie -> Kopie der Regel-Definition (custom_strategies)
  * Built-in/Variante -> Variante: gleicher Code, eigene ID/Name/Parameter
    (strategy_variants)
"""
import uuid
from datetime import datetime, timezone
from typing import Dict, Optional


async def create_custom_copy(db, registry, definition: Dict, name: str, timeframe: Optional[str],
                             extra: Optional[Dict] = None) -> Dict:
    new_id = f"custom_{uuid.uuid4().hex[:8]}"
    d = {**dict(definition), **(extra or {}), "id": new_id, "name": name}
    d["timeframe"] = timeframe or d.get("timeframe") or "1m"
    await db.custom_strategies.update_one({"id": new_id}, {"$set": d}, upsert=True)
    registry.upsert_custom(d)
    return d


async def create_variant(db, registry, strat, name: str, timeframe: Optional[str],
                         extra: Optional[Dict] = None) -> Optional[Dict]:
    """None = Basis nicht als Variante kopierbar."""
    base_id = getattr(strat, "BASE_STRATEGY_ID", None) or strat.STRATEGY_ID
    new_id = f"variant_{uuid.uuid4().hex[:8]}"
    d = {**(extra or {}), "kind": "variant", "id": new_id, "base_id": base_id, "name": name,
         "description": getattr(strat, "STRATEGY_DESCRIPTION", ""),
         "timeframe": timeframe or getattr(strat, "STRATEGY_TIMEFRAME", "1m"),
         "created_at": datetime.now(timezone.utc).isoformat()}
    if registry.upsert_variant(d) is None:
        return None
    await db.strategy_variants.update_one({"id": new_id}, {"$set": d}, upsert=True)
    return d
