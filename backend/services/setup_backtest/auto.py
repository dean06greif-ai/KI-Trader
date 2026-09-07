"""Automatik des Backtest-Seedings: prüft periodisch, ob ein Lauf fällig ist,
und startet dann die Auto-Schleife (Varianten + Feintuning) für die gewählten
Anlageklassen – ohne Klick. Einstellungen liegen in
settings.setup_backtest_state.auto, der Lauf selbst nutzt runner.run_job
(gleicher Pfad wie der manuelle Start, gleicher Job-Status fürs UI).

Sicherheitsregeln: nie parallel zu einem laufenden Backtest/Seeding, nie bei
RAM-Knappheit (ram_queue), Fehler beenden die Schleife nicht.
"""
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from services import setup_asset_class as ac

logger = logging.getLogger(__name__)

STATE_ID = "setup_backtest_state"
DEFAULTS: Dict = {"enabled": False, "interval_hours": 24, "days": 90, "mode": "loop",
                  "asset_classes": list(ac.CLASSES)}
HISTORY_KEEP = 10
CHECK_EVERY_S = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse(ts) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def normalize(raw: Optional[Dict]) -> Dict:
    """Einstellungen auf das erlaubte Schema bringen (rein, testbar)."""
    raw = raw or {}
    cfg = dict(DEFAULTS)
    cfg["enabled"] = bool(raw.get("enabled", False))
    try:
        cfg["interval_hours"] = max(6, min(24 * 14, int(raw.get("interval_hours", 24))))
    except (TypeError, ValueError):
        pass
    try:
        cfg["days"] = max(14, min(365, int(raw.get("days", 90))))
    except (TypeError, ValueError):
        pass
    cfg["mode"] = "single" if raw.get("mode") == "single" else "loop"
    classes = [c for c in (raw.get("asset_classes") or []) if c in ac.CLASSES]
    cfg["asset_classes"] = classes or list(ac.CLASSES)
    for k in ("last_run_at", "last_summary", "history", "last_error"):
        if raw.get(k) is not None:
            cfg[k] = raw[k]
    return cfg


def next_run_at(cfg: Dict) -> Optional[str]:
    if not cfg.get("enabled"):
        return None
    last = _parse(cfg.get("last_run_at"))
    if not last:
        return _now().isoformat()
    return (last + timedelta(hours=int(cfg["interval_hours"]))).isoformat()


def is_due(cfg: Dict, now: Optional[datetime] = None) -> bool:
    """Fällig, wenn aktiviert und seit dem letzten Lauf >= interval_hours (rein)."""
    if not cfg.get("enabled"):
        return False
    last = _parse(cfg.get("last_run_at"))
    if not last:
        return True
    return (now or _now()) - last >= timedelta(hours=int(cfg["interval_hours"]))


def public_state(raw: Optional[Dict]) -> Dict:
    cfg = normalize(raw)
    cfg["next_run_at"] = next_run_at(cfg)
    cfg["due"] = is_due(cfg)
    return cfg


async def get(db) -> Dict:
    doc = await db.settings.find_one({"_id": STATE_ID}, {"auto": 1}) or {}
    return public_state(doc.get("auto"))


async def save(db, raw: Dict) -> Dict:
    cur = normalize(((await db.settings.find_one({"_id": STATE_ID}, {"auto": 1}) or {}).get("auto")))
    merged = normalize({**cur, **{k: v for k, v in (raw or {}).items()
                                  if k in ("enabled", "interval_hours", "days", "mode", "asset_classes")}})
    await db.settings.update_one({"_id": STATE_ID}, {"$set": {"auto": merged}}, upsert=True)
    return public_state(merged)


async def _record(db, cfg: Dict, summary: Optional[Dict], error: Optional[str]) -> None:
    hist = list(cfg.get("history") or [])
    entry = {"at": _now().isoformat(), "passed": (summary or {}).get("passed"),
             "tested": (summary or {}).get("tested"), "mode": cfg["mode"], "days": cfg["days"],
             "asset_classes": cfg["asset_classes"], "error": error}
    hist = (hist + [entry])[-HISTORY_KEEP:]
    upd = {"auto.last_run_at": entry["at"], "auto.history": hist, "auto.last_error": error}
    if summary is not None:
        upd["auto.last_summary"] = {k: summary.get(k) for k in ("passed", "tested", "finished_at", "mode", "days")}
    await db.settings.update_one({"_id": STATE_ID}, {"$set": upd}, upsert=True)


def _busy() -> bool:
    from services import backtester as bt
    from services.setup_backtest import runner
    return runner.running_job() is not None or any(j["status"] == "running" for j in bt.JOBS.values())


async def run_once(db, cfg: Optional[Dict] = None) -> Optional[Dict]:
    """Einen Automatik-Lauf ausführen (blockiert bis fertig). None = übersprungen."""
    from services import ram_queue
    from services.setup_backtest import runner
    cfg = normalize(cfg or (await db.settings.find_one({"_id": STATE_ID}, {"auto": 1}) or {}).get("auto"))
    if _busy():
        logger.info("Seeding-Automatik: übersprungen (anderer Backtest läuft)")
        return None
    if ram_queue.free_mb() < ram_queue.MIN_FREE_MB:
        logger.info("Seeding-Automatik: übersprungen (RAM knapp)")
        return None
    params = {"kind": "ai_seed", "asset_classes": cfg["asset_classes"], "days": cfg["days"],
              "mode": cfg["mode"], "setups": None, "trigger": "auto"}
    job_id = runner.create_job(params)
    logger.info(f"Seeding-Automatik: Lauf {job_id} gestartet ({cfg['asset_classes']}, "
                f"{cfg['days']} Tage, {cfg['mode']})")
    await runner.run_job(job_id, db, cfg["asset_classes"], cfg["days"], cfg["mode"], None,
                         trigger="auto")
    job = runner.JOBS.get(job_id) or {}
    summary = job.get("result") if job.get("status") == "done" else None
    await _record(db, cfg, summary, job.get("error") if job.get("status") == "error" else None)
    return job


async def run_loop(db) -> None:
    await asyncio.sleep(180)   # Boot abwarten (Kerzen-Bootstrap, Backfill)
    while True:
        try:
            cfg = await get(db)
            if cfg["due"]:
                await run_once(db, cfg)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Seeding-Automatik: {e}")
        await asyncio.sleep(CHECK_EVERY_S)
