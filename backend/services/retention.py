"""Datenbank-Retention (MongoDB-Atlas-Free-Tier, 512 MB Logical Size).

Hintergrund: Atlas-Alerts "Logical Size has gone above 440 MB" – die größten
Verbraucher (Stand 09/2026) waren ai_chat_archive (123 MB), ai_decisions
(90 MB), backtest_trades (45 MB), ai_market_snapshots (29 MB),
optimizer_trades (28 MB) und ml_gate_models (28 MB).

Lösung: EINE zentrale, konfigurierbare Retention-Policy statt verstreuter
Einzel-Caps. Ein täglicher Sweep löscht Dokumente über der Aufbewahrungszeit
(Alter) bzw. über der Behaltensanzahl (keep_last, für wenige-aber-riesige
Dokumente wie ml_gate_models/regime_analyses). ISO-Timestamps sind Strings –
lexikographischer Vergleich funktioniert, TTL-Indizes (BSON-Date) nicht.

Sicherheitsregeln:
  * NUR delete_many mit explizitem Filter – nie drop, nie Collections anlegen.
  * ai_market_snapshots hat bewusst 200 Tage (ML-Lookback 120 Tage) – die
    Policy hier greift erst DARÜBER (Backstop), der Observer-Prune bleibt.
  * Jede Stufe einzeln try/except – ein Fehler stoppt den Sweep nicht.
  * Konfiguration in settings.retention_config überschreibbar (Tage/keep_last
    pro Collection), Verlauf in settings.retention_state.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

CONFIG_ID = "retention_config"
STATE_ID = "retention_state"
BOOT_DELAY_S = 1800          # 30 min nach Boot (Backfill/Bootstrap nicht stören)
SWEEP_EVERY_S = 24 * 3600
HISTORY_KEEP = 14

# (collection, ts_field, max_age_days, keep_last)
#  max_age_days = None -> kein Alters-Limit; keep_last = None -> kein Anzahl-Limit
DEFAULT_POLICY: List[Dict] = [
    # Tages-Archiv des KI-Chats: Summaries/Lektionen leben separat weiter
    {"coll": "ai_chat_archive", "ts": "ts", "days": 45, "keep_last": None},
    # Entscheidungen werden beim Tages-Reset ohnehin ins Archiv gespiegelt
    {"coll": "ai_decisions", "ts": "ts", "days": 21, "keep_last": None},
    # Backstop über dem Observer-Prune (SNAPSHOT_RETENTION_DAYS=200)
    {"coll": "ai_market_snapshots", "ts": "ts", "days": 200, "keep_last": None},
    {"coll": "ai_news_events", "ts": "ts", "days": 30, "keep_last": None},
    {"coll": "ai_trade_actions", "ts": "ts", "days": 60, "keep_last": None},
    {"coll": "fee_guard_blocks", "ts": "ts", "days": 45, "keep_last": None},
    {"coll": "audit_log", "ts": "ts", "days": 90, "keep_last": None},
    {"coll": "ai_move_events", "ts": "ts", "days": 30, "keep_last": None},
    # Strategie-Backtests/Optimizer: Ergebnisse altern, Läufe bleiben begrenzt
    {"coll": "backtest_trades", "ts": "created_at", "days": 30, "keep_last": None},
    {"coll": "backtests", "ts": "created_at", "days": 45, "keep_last": 20},
    {"coll": "optimizer_trades", "ts": "created_at", "days": 30, "keep_last": None},
    {"coll": "optimizer_runs", "ts": "created_at", "days": 45, "keep_last": 20},
    # Wenige, riesige Dokumente: Anzahl-Limit ist hier das wirksame Kriterium
    {"coll": "regime_analyses", "ts": "created_at", "days": None, "keep_last": 8},
    {"coll": "ml_gate_models", "ts": "trained_at", "days": None, "keep_last": 12},
]
# Harte Untergrenzen gegen Fehlkonfiguration (nie aggressiver löschen als das)
MIN_DAYS = {"ai_market_snapshots": 130, "ai_decisions": 7, "ai_chat_archive": 14}
MIN_KEEP = 3


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def merged_policy(overrides: Optional[Dict]) -> List[Dict]:
    """Policy + Overrides aus settings.retention_config (rein, testbar).
    Overrides: {"ai_chat_archive": {"days": 60}, ...}; Untergrenzen greifen."""
    overrides = overrides or {}
    out = []
    for rule in DEFAULT_POLICY:
        r = dict(rule)
        ov = overrides.get(r["coll"]) or {}
        if "days" in ov:
            try:
                r["days"] = max(1, int(ov["days"])) if ov["days"] is not None else None
            except (TypeError, ValueError):
                pass
        if "keep_last" in ov:
            try:
                r["keep_last"] = max(MIN_KEEP, int(ov["keep_last"])) if ov["keep_last"] is not None else None
            except (TypeError, ValueError):
                pass
        floor = MIN_DAYS.get(r["coll"])
        if floor and r.get("days") is not None:
            r["days"] = max(floor, r["days"])
        out.append(r)
    return out


async def _sweep_rule(db, rule: Dict) -> Dict:
    coll, ts_field = rule["coll"], rule["ts"]
    deleted = 0
    if rule.get("days"):
        cutoff = (datetime.now(timezone.utc) - timedelta(days=int(rule["days"]))).isoformat()
        res = await db[coll].delete_many({ts_field: {"$lt": cutoff, "$type": "string"}})
        deleted += int(res.deleted_count or 0)
    keep = rule.get("keep_last")
    if keep:
        total = await db[coll].count_documents({})
        if total > keep:
            old = await db[coll].find({}, {"_id": 1}).sort(ts_field, -1) \
                .skip(int(keep)).to_list(length=None)
            ids = [o["_id"] for o in old]
            if ids:
                res = await db[coll].delete_many({"_id": {"$in": ids}})
                deleted += int(res.deleted_count or 0)
    return {"coll": coll, "deleted": deleted}


async def run_sweep(db, trigger: str = "auto") -> Dict:
    """Kompletter Retention-Sweep. Gibt Zusammenfassung zurück und persistiert
    sie in settings.retention_state."""
    cfg = await db.settings.find_one({"_id": CONFIG_ID}) or {}
    if cfg.get("enabled") is False and trigger == "auto":
        return {"status": "disabled"}
    policy = merged_policy(cfg.get("overrides"))
    results, total = [], 0
    for rule in policy:
        try:
            r = await _sweep_rule(db, rule)
            if r["deleted"]:
                results.append(r)
            total += r["deleted"]
        except Exception as e:  # noqa: BLE001 – ein Fehler stoppt den Sweep nicht
            logger.warning(f"Retention {rule['coll']}: {e}")
            results.append({"coll": rule["coll"], "error": str(e)[:120]})
    summary = {"at": _now_iso(), "trigger": trigger, "deleted_total": total,
               "collections": results}
    try:
        doc = await db.settings.find_one({"_id": STATE_ID}) or {}
        history = ([summary] + list(doc.get("history") or []))[:HISTORY_KEEP]
        await db.settings.update_one(
            {"_id": STATE_ID},
            {"$set": {"last_run": summary, "history": history}}, upsert=True)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Retention-State speichern: {e}")
    if total:
        parts = ", ".join(f"{r['coll']} {r['deleted']}" for r in results if r.get("deleted"))
        logger.info(f"Retention-Sweep ({trigger}): {total} Dokumente entfernt ({parts})")
    return {"status": "ok", **summary}


async def storage_stats(db) -> Dict:
    """Größen je Collection (logische Größe = Atlas-Quota-Maßstab)."""
    rows = []
    try:
        names = await db.list_collection_names()
        for n in names:
            try:
                s = await db.command("collstats", n)
                rows.append({"coll": n, "size_mb": round(s.get("size", 0) / 1e6, 2),
                             "count": int(s.get("count", 0)),
                             "index_mb": round(s.get("totalIndexSize", 0) / 1e6, 2)})
            except Exception:  # noqa: BLE001
                continue
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Retention storage_stats: {e}")
    rows.sort(key=lambda r: r["size_mb"], reverse=True)
    total = round(sum(r["size_mb"] for r in rows), 1)
    total_idx = round(sum(r["index_mb"] for r in rows), 1)
    return {"total_mb": total, "total_index_mb": total_idx,
            "quota_mb": 512, "used_pct": round((total + total_idx) / 512 * 100, 1),
            "collections": rows[:40]}


async def status(db) -> Dict:
    cfg = await db.settings.find_one({"_id": CONFIG_ID}) or {}
    state = await db.settings.find_one({"_id": STATE_ID}) or {}
    return {"enabled": cfg.get("enabled", True) is not False,
            "policy": merged_policy(cfg.get("overrides")),
            "last_run": state.get("last_run"),
            "history": (state.get("history") or [])[:HISTORY_KEEP]}


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
                    due = (datetime.now(timezone.utc) - dt).total_seconds() >= SWEEP_EVERY_S
                except ValueError:
                    pass
            if due:
                await run_sweep(db, trigger="auto")
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Retention-Loop: {e}")
        await asyncio.sleep(3600)
