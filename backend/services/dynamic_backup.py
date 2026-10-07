"""Backup/Export, Import und Duplizieren dynamischer Strategien.

Ein Backup-Bündel (type="dynamic_strategy_backup") enthält alles, was für eine
1:1-Wiederherstellung nötig ist:
- die dynamische Strategie (Regime-Modell, Konfigurationen je Regime, Settings),
- die verknüpfte Regime-Analyse (Erkennung, Zuordnungen, Walk-Forward),
- die Definitionen aller verwendeten Custom-Strategien,
- die Blitz-Konfigurationen je Coin (strategy_coin_configs, Präfix = Dynamik-ID).

Import ist sicher: bestehende Strategien/Analysen werden nie überschrieben, die
importierte Strategie startet mit ausgeschalteter Auto-Prüfung/-Übernahme und
erhält bei Kollision eine neue ID.
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TYPE = "dynamic_strategy_backup"
VERSION = 1
# Laufzeit-Zustand: gehört nicht in ein Backup (wird nach Import neu bestimmt)
RUNTIME_FIELDS = ("_id", "last_state", "runtime_state", "pending_switch",
                  "application_status", "archived", "archived_at", "last_applied")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def strategy_ids_of(doc: Dict) -> List[str]:
    """Alle Strategie-IDs, die eine dynamische Strategie benutzt (rein)."""
    ids = [doc.get("strategy_id")]
    ids += list((doc.get("regime_strategies") or {}).values())
    for sub in (doc.get("sub_strategies") or {}).values():
        ids.append(sub.get("id") if isinstance(sub, dict) else sub)
    return sorted({i for i in ids if isinstance(i, str) and i})


def clean_dynamic(doc: Dict) -> Dict:
    return {k: v for k, v in doc.items() if k not in RUNTIME_FIELDS}


async def export_bundle(db, did: str, include_analysis: bool = True) -> Optional[Dict]:
    doc = await db.dynamic_strategies.find_one({"id": did}, {"_id": 0})
    if not doc:
        return None
    aid = (doc.get("settings") or {}).get("analysis_id")
    analysis = None
    if include_analysis and aid:
        analysis = await db.regime_analyses.find_one({"id": aid}, {"_id": 0})
    sids = strategy_ids_of(doc)
    customs = await db.custom_strategies.find({"id": {"$in": sids}}, {"_id": 0}).to_list(50)
    coin_cfgs = {}
    async for c in db.strategy_coin_configs.find({"_id": {"$regex": f"^{did}_"}}):
        coin_cfgs[c["_id"][len(did) + 1:]] = c.get("config") or {}
    return {"type": TYPE, "version": VERSION, "exported_at": _now(),
            "dynamic_id": did, "name": doc.get("name"),
            "dynamic": clean_dynamic(doc),
            "analysis": analysis, "analysis_id": aid,
            "custom_strategies": customs,
            "strategy_coin_configs": coin_cfgs}


def validate_bundle(bundle: Dict) -> Optional[str]:
    """Fehlertext oder None (rein)."""
    if not isinstance(bundle, dict) or bundle.get("type") != TYPE:
        return "Keine gültige Backup-Datei für dynamische Strategien"
    dyn = bundle.get("dynamic")
    if not isinstance(dyn, dict):
        return "Datei enthält keine dynamische Strategie"
    for k in ("strategy_id", "model", "configs"):
        if not dyn.get(k):
            return f"Dynamische Strategie unvollständig: {k} fehlt"
    return None


async def _restore_customs(db, registry, customs: List[Dict], validate) -> Dict:
    restored, kept = [], []
    for d in customs or []:
        sid = (d or {}).get("id")
        if not sid:
            continue
        if registry.get(sid):
            kept.append(sid)          # vorhandene Strategie nie überschreiben
            continue
        definition = validate(dict(d), check_meta=False)
        definition["id"] = sid
        await db.custom_strategies.update_one({"id": sid}, {"$set": definition}, upsert=True)
        registry.upsert_custom(definition)
        restored.append(sid)
    return {"restored": restored, "kept": kept}


async def _restore_analysis(db, analysis: Optional[Dict], aid: Optional[str] = None) -> str:
    """'exists' | 'restored' | 'missing'. Nie überschreiben, nie Bestand löschen."""
    if not isinstance(analysis, dict) or not analysis.get("id"):
        if aid and await db.regime_analyses.find_one({"id": aid}, {"_id": 0, "id": 1}):
            return "exists"
        return "missing"
    if await db.regime_analyses.find_one({"id": analysis["id"]}, {"_id": 0, "id": 1}):
        return "exists"
    analysis = {k: v for k, v in analysis.items() if k != "_id"}
    await db.regime_analyses.replace_one({"id": analysis["id"]}, analysis, upsert=True)
    try:   # Erkennung zusätzlich im Autopilot-Verlauf sichern
        from services import regime_history_import
        await regime_history_import.import_one(db, analysis, only_pinned=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Dynamik-Import: Verlauf-Sicherung fehlgeschlagen: {e}")
    return "restored"


def build_copy(dyn: Dict, new_id: str, name: str, source: Dict) -> Dict:
    """Neue dynamische Strategie aus einem Backup (rein): sichere Defaults."""
    from services import strategy_release
    doc = clean_dynamic(dyn)
    doc["id"] = new_id
    doc["name"] = name
    doc["created_at"] = _now()
    doc["last_state"] = {}
    doc["settings"] = {**(doc.get("settings") or {}),
                       "auto_check_enabled": False, "auto_apply_enabled": False}
    doc["imported_from"] = source
    doc["release"] = strategy_release.initial_release(doc, doc.get("verdict"))
    return doc


async def import_bundle(db, registry, bundle: Dict, validate, name: Optional[str] = None,
                        copy: bool = False) -> Dict:
    dyn = bundle["dynamic"]
    orig_id = dyn.get("id") or bundle.get("dynamic_id")
    taken = bool(orig_id) and bool(await db.dynamic_strategies.find_one(
        {"id": orig_id, "archived": {"$ne": True}}, {"_id": 0, "id": 1}))
    new_id = f"dyn_{uuid.uuid4().hex[:8]}" if (copy or taken or not orig_id) else orig_id
    customs = await _restore_customs(db, registry, bundle.get("custom_strategies"), validate)
    missing = [s for s in strategy_ids_of(dyn) if not registry.get(s)]
    if missing:
        raise ValueError(f"Strategie(n) fehlen in dieser Version: {', '.join(missing)}")
    analysis = await _restore_analysis(db, bundle.get("analysis"),
                                       (dyn.get("settings") or {}).get("analysis_id"))
    label = name or dyn.get("name") or new_id
    if new_id != orig_id and not name:
        label = f"{label} ({'Kopie' if copy else 'Import'})"
    doc = build_copy(dyn, new_id, label,
                     {"dynamic_id": orig_id, "at": _now(),
                      "kind": "duplicate" if copy else "import",
                      "exported_at": bundle.get("exported_at")})
    await db.dynamic_strategies.replace_one({"id": new_id}, doc, upsert=True)
    n_cfg = 0
    for sym, cfg in (bundle.get("strategy_coin_configs") or {}).items():
        key = f"{new_id}_{sym}"
        await db.strategy_coin_configs.replace_one({"_id": key}, {"_id": key, "config": cfg}, upsert=True)
        n_cfg += 1
    return {"id": new_id, "name": label, "same_id": new_id == orig_id,
            "analysis": analysis, "custom_strategies": customs, "coin_configs": n_cfg}
