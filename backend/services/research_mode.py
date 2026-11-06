"""Globaler Forschungs-Modus (Bewertung neuer Läufe, ANALYSE_DYNAMIK_REGIME_0626 E1/E2).

- evidence_mode:   "robust" (Untergrenze/Glaubwürdigkeit/Bootstrap) | "classic" (Alt-Verhalten)
- regime_val_mode: "time" (spätere Phasen + Embargo) | "phase" (Ende jeder Phase, Alt-Verhalten)

Ein Wert im Job-Body hat Vorrang (z.B. lokaler Worker, Tests). Gespeicherte
Analysen/Strategien bleiben unverändert – der Modus wirkt nur auf neue Läufe.
"""
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

DOC_ID = "research_mode"
EVIDENCE_MODES = ("robust", "classic")
VAL_MODES = ("time", "phase")
DEFAULTS = {"evidence_mode": "robust", "regime_val_mode": "time"}

_state: Dict = dict(DEFAULTS)


def current() -> Dict:
    return dict(_state)


def _pick(body: Optional[Dict], key: str, allowed) -> str:
    v = str((body or {}).get(key) or "").lower()
    return v if v in allowed else _state.get(key, DEFAULTS[key])


def evidence_mode(body: Optional[Dict] = None) -> str:
    return _pick(body, "evidence_mode", EVIDENCE_MODES)


def regime_val_mode(body: Optional[Dict] = None) -> str:
    return _pick(body, "regime_val_mode", VAL_MODES)


def clean(body: Optional[Dict]) -> Dict:
    out = {}
    for key, allowed in (("evidence_mode", EVIDENCE_MODES), ("regime_val_mode", VAL_MODES)):
        v = str((body or {}).get(key) or "").lower()
        if v in allowed:
            out[key] = v
    return out


async def load(db) -> Dict:
    try:
        doc = await db.app_config.find_one({"id": DOC_ID}, {"_id": 0})
        _state.update(clean(doc))
    except Exception as e:  # noqa: BLE001 – fail-soft: Standard bleibt
        logger.warning(f"research_mode laden: {e}")
    return current()


async def save(db, body: Dict) -> Dict:
    upd = clean(body)
    _state.update(upd)
    await db.app_config.update_one({"id": DOC_ID}, {"$set": {"id": DOC_ID, **_state}}, upsert=True)
    return current()
