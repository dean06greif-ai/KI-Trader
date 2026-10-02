"""Tägliches Backup der kritischen MongoDB-Collections nach Supabase Storage.

Hintergrund: Atlas Free Tier hat kein Backup; am 05./06.09. gingen MasterPrompt
und Paper-Trades verloren. Dieses Modul sichert die kleinen, wertvollen
Collections (Einstellungen, Trades, Entscheidungen, Lektionen, Regime-Analysen,
Edge-Register …) einmal täglich als gezipptes JSON (bson.json_util – ObjectId/
Datumswerte bleiben verlustfrei) in den privaten Bucket `mongo-backups` und
behält RETENTION_DAYS Tage.

Wiederherstellung: `restore()` (API, Admin) oder backend/scripts/backup_restore.py
(CLI) – standardmäßig als Trockenlauf; echte Wiederherstellung ersetzt Dokumente
per _id (upsert), löscht also NIE etwas, was nicht im Dump ist.

Konfiguration: settings.backup_config {enabled, collections, retention_days},
Verlauf: settings.backup_state.
"""
import asyncio
import gzip
import io
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from bson import json_util

from services import supabase_storage as storage

logger = logging.getLogger(__name__)

CONFIG_ID = "backup_config"
STATE_ID = "backup_state"
BUCKET = os.environ.get("BACKUP_BUCKET", "mongo-backups")
BOOT_DELAY_S = 900            # 15 min nach Boot (Backfill/Migrationen nicht stören)
CHECK_EVERY_S = 1800
BACKUP_EVERY_S = 24 * 3600
RETENTION_DAYS = 30
HISTORY_KEEP = 20
BATCH = 2000

# Kritisch = klein und nicht reproduzierbar. Kerzen/Snapshots/Backtest-Rohdaten
# sind bewusst NICHT dabei (Bulk, jederzeit neu ladbar).
DEFAULT_COLLECTIONS: List[str] = [
    "settings",                 # Config, MasterPrompt, Playbook-/Seeding-State, Retention
    "auto_trades",              # Paper-/Live-Trades (Herzstück der Auswertung)
    "auto_trades_trash",
    "ai_decisions",             # Entscheidungen (Retention 14 Tage -> klein)
    "ai_lessons_history",
    "ai_lesson_candidates",
    "ai_knowledge",             # KI-Gedächtnis (Mongo-Kopie)
    "ai_limit_orders",
    "ai_proposals",
    "ai_rewards",
    "ai_strategy_candidates",
    "regime_analyses",          # keep_last 8 – groß, aber wertvoll
    "regime_calibrations",
    "setup_backtest_edges",     # Edge-Register
    "setup_backtest_trades",    # OOS-Trades fürs Reife-Gate
    "strategies", "custom_strategies", "dynamic_strategies", "strategy_variants",
    "strategy_coin_configs", "strategy_coin_toggles",
    "performance", "trade_stats", "analytics_daily",
    "trade_reviews", "limit_reviews",
    "job_series",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def backup_name(at: Optional[datetime] = None) -> str:
    at = at or _now()
    return f"backup_{at:%Y%m%d_%H%M%S}.json.gz"


def parse_backup_date(name: str) -> Optional[datetime]:
    """Zeitstempel aus dem Dateinamen (rein, testbar)."""
    try:
        stem = name.split("/")[-1]
        return datetime.strptime(stem[len("backup_"):len("backup_") + 15], "%Y%m%d_%H%M%S") \
            .replace(tzinfo=timezone.utc)
    except (ValueError, IndexError):
        return None


def expired(names: List[str], retention_days: int, now: Optional[datetime] = None) -> List[str]:
    """Dateinamen, die älter als die Aufbewahrung sind (rein, testbar)."""
    now = now or _now()
    cutoff = now - timedelta(days=int(retention_days))
    out = []
    for n in names:
        dt = parse_backup_date(n)
        if dt is not None and dt < cutoff:
            out.append(n)
    return out


def merged_config(cfg: Optional[Dict]) -> Dict:
    cfg = cfg or {}
    colls = cfg.get("collections")
    if not isinstance(colls, list) or not colls:
        colls = list(DEFAULT_COLLECTIONS)
    try:
        days = max(3, min(365, int(cfg.get("retention_days") or RETENTION_DAYS)))
    except (TypeError, ValueError):
        days = RETENTION_DAYS
    return {"enabled": cfg.get("enabled", True) is not False,
            "collections": [str(c) for c in colls], "retention_days": days}


def encode_dump(collections: Dict[str, List[Dict]], meta: Dict) -> bytes:
    """Dump -> gzip(JSON). Ein Objekt pro Collection (json_util, verlustfrei)."""
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as gz:
        gz.write(b'{"meta": ')
        gz.write(json.dumps(meta).encode())
        gz.write(b', "collections": {')
        first = True
        for name, docs in collections.items():
            if not first:
                gz.write(b", ")
            first = False
            gz.write(json.dumps(name).encode() + b": ")
            gz.write(json_util.dumps(docs).encode())
        gz.write(b"}}")
    return buf.getvalue()


def decode_dump(data: bytes) -> Dict:
    raw = gzip.decompress(data).decode()
    return json_util.loads(raw, json_options=json_util.JSONOptions(tz_aware=True))


async def _dump_collection(db, name: str) -> List[Dict]:
    docs: List[Dict] = []
    cursor = db[name].find({}, batch_size=BATCH)
    async for d in cursor:
        docs.append(d)
    return docs


async def run_backup(db, trigger: str = "auto") -> Dict:
    """Komplettes Backup: dumpen, hochladen, alte Dumps löschen, State schreiben."""
    cfg = merged_config(await db.settings.find_one({"_id": CONFIG_ID}))
    if not storage.configured():
        return {"status": "unconfigured",
                "hint": "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY fehlen – kein Backup-Ziel"}
    if not cfg["enabled"] and trigger == "auto":
        return {"status": "disabled"}
    t0 = _now()
    dumped: Dict[str, List[Dict]] = {}
    counts: Dict[str, int] = {}
    errors: List[str] = []
    for name in cfg["collections"]:
        try:
            docs = await _dump_collection(db, name)
            dumped[name] = docs
            counts[name] = len(docs)
        except Exception as e:  # noqa: BLE001 – eine Collection stoppt das Backup nicht
            errors.append(f"{name}: {str(e)[:120]}")
    meta = {"created_at": _now_iso(), "db": os.environ.get("DB_NAME"), "trigger": trigger,
            "collections": counts, "format": "json_util-v1"}
    blob = await asyncio.to_thread(encode_dump, dumped, meta)
    del dumped
    name = backup_name(t0)
    ok = await storage.ensure_bucket(BUCKET) and await storage.upload(BUCKET, name, blob, "application/gzip")
    removed = 0
    if ok:
        try:
            names = [o["name"] for o in await storage.list_objects(BUCKET) if o.get("name")]
            removed = await storage.delete(BUCKET, expired(names, cfg["retention_days"]))
        except Exception as e:  # noqa: BLE001
            errors.append(f"Aufräumen: {str(e)[:120]}")
    summary = {"at": _now_iso(), "trigger": trigger, "status": "ok" if ok else "upload_failed",
               "file": name if ok else None, "bytes": len(blob),
               "docs": int(sum(counts.values())), "collections": counts,
               "removed_old": removed, "errors": errors,
               "duration_s": round((_now() - t0).total_seconds(), 1)}
    try:
        doc = await db.settings.find_one({"_id": STATE_ID}) or {}
        history = ([summary] + list(doc.get("history") or []))[:HISTORY_KEEP]
        upd = {"history": history}
        if ok:
            upd["last_run"] = summary
        else:
            upd["last_error"] = summary
        await db.settings.update_one({"_id": STATE_ID}, {"$set": upd}, upsert=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Backup-State speichern: {e}")
    if ok:
        logger.info(f"Backup ({trigger}): {name} – {summary['docs']} Dokumente, "
                    f"{len(blob) / 1e6:.1f} MB, {removed} alte Dumps entfernt")
    else:
        logger.warning(f"Backup ({trigger}) fehlgeschlagen: {errors}")
    return summary


async def list_backups() -> List[Dict]:
    if not storage.configured():
        return []
    rows = await storage.list_objects(BUCKET)
    return [r for r in rows if str(r.get("name") or "").startswith("backup_")]


async def status(db) -> Dict:
    cfg = merged_config(await db.settings.find_one({"_id": CONFIG_ID}))
    state = await db.settings.find_one({"_id": STATE_ID}) or {}
    return {"configured": storage.configured(), "bucket": BUCKET, **cfg,
            "default_collections": DEFAULT_COLLECTIONS,
            "last_run": state.get("last_run"), "last_error": state.get("last_error"),
            "last_restore": state.get("last_restore"),
            "history": (state.get("history") or [])[:HISTORY_KEEP]}


async def restore(db, file: str, collections: Optional[List[str]] = None,
                  dry_run: bool = True) -> Dict:
    """Dump aus dem Bucket zurückspielen. dry_run=True zählt nur. Echte
    Wiederherstellung: replace_one per _id (upsert) – nichts wird gelöscht."""
    if not storage.configured():
        return {"status": "unconfigured"}
    data = await storage.download(BUCKET, file)
    if data is None:
        return {"status": "not_found", "file": file}
    dump = await asyncio.to_thread(decode_dump, data)
    result = await restore_from_dump(db, dump, collections, dry_run)
    result["file"] = file
    if not dry_run:
        try:
            await db.settings.update_one(
                {"_id": STATE_ID}, {"$set": {"last_restore": {"at": _now_iso(), **result}}}, upsert=True)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Restore-State speichern: {e}")
    return result


async def restore_from_dump(db, dump: Dict, collections: Optional[List[str]] = None,
                            dry_run: bool = True) -> Dict:
    colls = dump.get("collections") or {}
    wanted = [c for c in (collections or list(colls.keys())) if c in colls]
    per: Dict[str, Dict] = {}
    for name in wanted:
        docs = colls.get(name) or []
        if dry_run:
            per[name] = {"docs": len(docs)}
            continue
        upserted = matched = 0
        for d in docs:
            if "_id" not in d:
                await db[name].insert_one(d)
                upserted += 1
                continue
            r = await db[name].replace_one({"_id": d["_id"]}, d, upsert=True)
            matched += int(r.matched_count or 0)
            upserted += 1 if r.upserted_id is not None else 0
        per[name] = {"docs": len(docs), "replaced": matched, "inserted": upserted}
    return {"status": "dry_run" if dry_run else "restored", "meta": dump.get("meta"),
            "collections": per, "dry_run": dry_run}


async def run_loop(db) -> None:
    await asyncio.sleep(BOOT_DELAY_S)
    while True:
        try:
            state = await db.settings.find_one({"_id": STATE_ID}) or {}
            last = str(((state.get("last_run") or {}).get("at")) or "")
            due = True
            if last:
                try:
                    dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
                    due = (_now() - dt).total_seconds() >= BACKUP_EVERY_S
                except ValueError:
                    pass
            if due:
                await run_backup(db, trigger="auto")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Backup-Loop: {e}")
        await asyncio.sleep(CHECK_EVERY_S)
