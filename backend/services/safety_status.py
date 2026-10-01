"""Dauerhafter Sicherheitsstatus (Audit 2.4).

Bündelt die kritischen Betriebszustände zu einer Ampel (ok | warn | critical):
  * sl_missing      – offene LIVE-Trades ohne bestätigten SL an der Börse (kritisch)
  * close_failed    – Trades, deren Live-Close fehlschlug (`live_close_failed`, kritisch)
  * sync_stale      – letzter Bitunix-Positionsabgleich zu alt (nur bei offenen Live-Trades)
  * watchdog_stale  – Positions-Watchdog-Heartbeat zu alt (nur wenn er je lief)
  * kill_switch     – Kill-Switch (live) aktiv (Warnung, blockt selbst bereits)

`critical` blockt über den zentralen Entry-Guard (Audit 2.1) neue LIVE-Risiken
(abschaltbar über `block_on_critical`). Status ist 20 s gecacht, damit der
Signal-Pfad keine zusätzliche DB-Last erzeugt. API: GET /api/safety/status.
"""
import logging
import os
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# MongoDB-Atlas-Speicher (logische Datengröße): Warnung BEVOR die Quota greift
# und Atlas alle Writes blockt. Quota über Env ATLAS_QUOTA_MB anpassbar
# (Free/M0 = 512, Flex = 5120); 0 = Check aus.
ATLAS_QUOTA_MB = float(os.environ.get("ATLAS_QUOTA_MB", "512") or 0)
STORAGE_WARN_PCT = 85.0
STORAGE_CRIT_PCT = 97.0

CONFIG_ID = "safety_status_config"
DEFAULT_CONFIG = {
    "enabled": True,
    "block_on_critical": True,       # Entry-Guard blockt neue LIVE-Trades bei critical
    "sync_stale_warn_min": 15.0,     # Bitunix-Abgleich älter -> warn (nur mit offenen Live-Trades)
    "sync_stale_crit_min": 60.0,     # ... älter -> critical
    "watchdog_stale_warn_min": 20.0, # Watchdog-Heartbeat älter -> warn
}
CACHE_TTL_S = 20.0
_cache: Dict = {"ts": 0.0, "data": None}
_cfg_cache: Optional[Dict] = None

LEVELS = ("ok", "warn", "critical")


def _ts(value) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def age_minutes(iso_ts, now: Optional[datetime] = None) -> Optional[float]:
    t = _ts(iso_ts)
    if not t:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0.0, (now - t).total_seconds() / 60.0)


def stale_level(age_min: Optional[float], warn_min: float,
                crit_min: Optional[float] = None) -> str:
    """rein: Alter in Minuten -> ok/warn/critical (crit_min=None: nie critical)."""
    if age_min is None:
        return "ok"
    if crit_min is not None and crit_min > 0 and age_min > crit_min:
        return "critical"
    if warn_min > 0 and age_min > warn_min:
        return "warn"
    return "ok"


def storage_level(used_mb: float, quota_mb: float,
                  warn_pct: float = STORAGE_WARN_PCT,
                  crit_pct: float = STORAGE_CRIT_PCT) -> str:
    """rein: DB-Füllstand -> ok/warn/critical (quota<=0: Check aus)."""
    if quota_mb <= 0:
        return "ok"
    pct = used_mb / quota_mb * 100.0
    if pct >= crit_pct:
        return "critical"
    if pct >= warn_pct:
        return "warn"
    return "ok"


def overall_level(checks: List[Dict]) -> str:
    """rein: schlechtestes Einzel-Level gewinnt."""
    worst = "ok"
    for c in checks or []:
        lvl = c.get("level", "ok")
        if lvl == "critical":
            return "critical"
        if lvl == "warn":
            worst = "warn"
    return worst


async def get_config(db) -> Dict:
    global _cfg_cache
    if _cfg_cache is not None:
        return _cfg_cache
    cfg = dict(DEFAULT_CONFIG)
    doc = await db.settings.find_one({"_id": CONFIG_ID}) if db is not None else None
    if doc:
        for k in DEFAULT_CONFIG:
            if k in doc:
                cfg[k] = doc[k]
    _cfg_cache = cfg
    return cfg


async def update_config(db, updates: Dict) -> Dict:
    global _cfg_cache
    clean = {}
    for k, v in (updates or {}).items():
        if k not in DEFAULT_CONFIG:
            continue
        if k in ("enabled", "block_on_critical"):
            clean[k] = bool(v)
        else:
            try:
                clean[k] = max(0.0, float(v))
            except (TypeError, ValueError):
                continue
    if clean:
        await db.settings.update_one({"_id": CONFIG_ID}, {"$set": clean}, upsert=True)
    _cfg_cache = None
    _cache["ts"] = 0.0
    return await get_config(db)


async def status(db, force: bool = False) -> Dict:
    """Ampel-Status mit Einzel-Checks. 20 s gecacht (Signal-Pfad-freundlich)."""
    now_mono = time.monotonic()
    if not force and _cache["data"] is not None \
            and now_mono - _cache["ts"] < CACHE_TTL_S:
        return _cache["data"]
    cfg = await get_config(db)
    checks: List[Dict] = []

    sl_missing = await db.auto_trades.count_documents(
        {"status": "open", "mode": "live", "sl_exchange_missing": True})
    checks.append({"name": "sl_missing", "level": "critical" if sl_missing else "ok",
                   "count": sl_missing,
                   "detail": (f"{sl_missing} offene Live-Trades ohne bestätigten Börsen-SL"
                              if sl_missing else "Alle Live-SL an der Börse bestätigt")})

    close_failed = await db.auto_trades.count_documents(
        {"status": "open", "live_close_failed": True})
    checks.append({"name": "close_failed", "level": "critical" if close_failed else "ok",
                   "count": close_failed,
                   "detail": (f"{close_failed} Trades mit fehlgeschlagenem Live-Close"
                              if close_failed else "Keine fehlgeschlagenen Closes")})

    open_live = await db.auto_trades.count_documents({"status": "open", "mode": "live"})
    sync_doc = await db.settings.find_one({"_id": "bitunix_sync_status"}) or {}
    sync_age = age_minutes(sync_doc.get("last_sync_at"))
    if open_live > 0:
        lvl = stale_level(sync_age if sync_age is not None else 1e9,
                          cfg["sync_stale_warn_min"], cfg["sync_stale_crit_min"])
        detail = (f"Letzter Bitunix-Abgleich vor {sync_age:.0f} min"
                  if sync_age is not None else "Noch kein Bitunix-Abgleich protokolliert")
    else:
        lvl, detail = "ok", "Keine offenen Live-Trades"
    checks.append({"name": "sync_stale", "level": lvl, "detail": detail,
                   "age_min": round(sync_age, 1) if sync_age is not None else None})

    wd_doc = await db.settings.find_one({"_id": "position_watchdog_status"}) or {}
    wd_age = age_minutes(wd_doc.get("last_run_at"))
    lvl = stale_level(wd_age, cfg["watchdog_stale_warn_min"]) if wd_age is not None else "ok"
    checks.append({"name": "watchdog_stale", "level": lvl,
                   "age_min": round(wd_age, 1) if wd_age is not None else None,
                   "detail": (f"Watchdog-Heartbeat vor {wd_age:.0f} min" if wd_age is not None
                              else "Watchdog lief noch nicht (lokal/ohne Keys normal)")})

    try:
        from services import trade_guard
        gs = await trade_guard.get_state(db, "live")
        checks.append({"name": "kill_switch",
                       "level": "warn" if gs.get("paused") else "ok",
                       "detail": (f"Kill-Switch (live) aktiv bis {gs.get('paused_until')}"
                                  if gs.get("paused") else "Kill-Switch (live) inaktiv")})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Sicherheitsstatus: Kill-Switch-Check übersprungen: {e}")

    # DB-Speicher (Atlas-Quota): warnen BEVOR der Cluster Writes blockt
    if ATLAS_QUOTA_MB > 0:
        try:
            st_db = await db.command("dbStats")
            used_mb = (st_db.get("dataSize", 0) + st_db.get("indexSize", 0)) / 1e6
            lvl = storage_level(used_mb, ATLAS_QUOTA_MB)
            pct = used_mb / ATLAS_QUOTA_MB * 100
            checks.append({
                "name": "db_storage", "level": lvl,
                "used_mb": round(used_mb, 1), "quota_mb": ATLAS_QUOTA_MB,
                "detail": (f"MongoDB-Speicher {used_mb:.0f}/{ATLAS_QUOTA_MB:.0f} MB "
                           f"({pct:.0f}%)"
                           + (" – bald blockt Atlas alle Writes! Alte Daten löschen "
                              "oder Cluster upgraden" if lvl != "ok" else ""))})
        except Exception as e:  # noqa: BLE001
            logger.debug(f"Sicherheitsstatus: Speicher-Check übersprungen: {e}")

    data = {"level": overall_level(checks) if cfg.get("enabled", True) else "ok",
            "enabled": bool(cfg.get("enabled", True)),
            "block_on_critical": bool(cfg.get("block_on_critical", True)),
            "checks": checks,
            "ts": datetime.now(timezone.utc).isoformat()}
    _cache["data"] = data
    _cache["ts"] = now_mono
    return data


async def entry_allowed(db) -> Tuple[bool, str]:
    """Für den Entry-Guard (2.1): blockt neue LIVE-Risiken bei critical.
    Fail-open bei internen Fehlern – Sicherheitsstatus darf den Handel nie
    durch eigene Bugs lahmlegen."""
    try:
        st = await status(db)
        if not (st["enabled"] and st["block_on_critical"]):
            return True, ""
        if st["level"] != "critical":
            return True, ""
        crit = [c["detail"] for c in st["checks"] if c["level"] == "critical"]
        return False, ("Sicherheitsstatus KRITISCH – neue Live-Trades gesperrt: "
                       + "; ".join(crit[:3]))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Sicherheitsstatus-Prüfung fehlgeschlagen (fail-open): {e}")
        return True, ""


def reset_cache():
    """Nur für Tests."""
    global _cfg_cache
    _cfg_cache = None
    _cache["ts"] = 0.0
    _cache["data"] = None
