"""RAM-Warteschlange für Cloud-Rechenjobs (Render 512 MB).

Backtests/Optimierungen/Regime-Lab-Läufe starteten bisher sofort – war der
Server-RAM knapp (z.B. anderer Nutzer schaut sich parallel Charts an), kam es
zum OOM-Kill und der Lauf war verloren. Jetzt: Reicht der freie RAM nicht,
wartet der Job in einer FIFO-Warteschlange und startet AUTOMATISCH, sobald
wieder genug frei ist (Watcher prüft alle 10s – läuft auch über Nacht ohne
Interaktion durch). Lokale Worker-Jobs sind nicht betroffen (rechnen nicht
auf dem Server).
"""
import asyncio
import logging
import os
import time
from collections import deque
from typing import Callable, Dict, Optional

from core.utils import _watch_job_task

logger = logging.getLogger(__name__)

MIN_FREE_MB = int(os.environ.get("RAM_QUEUE_MIN_FREE_MB", "150"))
CHECK_INTERVAL_S = int(os.environ.get("RAM_QUEUE_CHECK_S", "10"))
MAX_WAIT_S = int(os.environ.get("RAM_QUEUE_MAX_WAIT_S", str(12 * 3600)))

_QUEUE: deque = deque()
_watcher: Optional[asyncio.Task] = None


def _cgroup_free_mb() -> Optional[float]:
    """Freier RAM laut Container-Limit (cgroup v2/v1) – auf Render das echte
    512-MB-Limit; psutil sähe dort fälschlich den RAM des Host-Systems."""
    try:
        for lim_p, cur_p in (("/sys/fs/cgroup/memory.max",
                              "/sys/fs/cgroup/memory.current"),
                             ("/sys/fs/cgroup/memory/memory.limit_in_bytes",
                              "/sys/fs/cgroup/memory/memory.usage_in_bytes")):
            if os.path.isfile(lim_p) and os.path.isfile(cur_p):
                lim_raw = open(lim_p).read().strip()
                if lim_raw == "max":
                    return None
                lim = int(lim_raw)
                if lim > (1 << 40):  # >1 TB = kein echtes Limit gesetzt
                    return None
                cur = int(open(cur_p).read().strip())
                return max(0.0, (lim - cur) / (1024 * 1024))
    except (OSError, ValueError):
        pass
    return None


def free_mb() -> float:
    v = _cgroup_free_mb()
    if v is not None:
        return v
    try:
        import psutil
        return psutil.virtual_memory().available / (1024 * 1024)
    except Exception:  # noqa: BLE001
        return float(MIN_FREE_MB)  # unbekannt -> nicht blockieren


def queue_public():
    return [{"job_id": e["job_id"], "kind": e["kind"],
             "waiting_s": int(time.time() - e["ts"])} for e in _QUEUE]


def _wait_phase(free: float) -> str:
    return (f"Wartet auf freien Server-RAM ({int(free)} MB frei, benötigt "
            f"{MIN_FREE_MB} MB) – startet automatisch")


def submit(jobs: Dict, job_id: str, factory: Callable, kind: str = "job") -> bool:
    """Job sofort starten oder bei RAM-Knappheit einreihen.

    factory: Callable ohne Argumente, das die Job-Coroutine erzeugt (wird
    erst beim tatsächlichen Start aufgerufen). Rückgabe: True = eingereiht."""
    if not _QUEUE and free_mb() >= MIN_FREE_MB:
        task = asyncio.create_task(factory())
        _watch_job_task(task, jobs, job_id)
        return False
    job = jobs.get(job_id)
    if job is not None:
        job["phase"] = _wait_phase(free_mb())
    _QUEUE.append({"jobs": jobs, "job_id": job_id, "factory": factory,
                   "kind": kind, "ts": time.time()})
    logger.info(f"ram_queue: {kind} {job_id} eingereiht ({len(_QUEUE)} wartend)")
    _ensure_watcher()
    return True


def _try_start_next() -> bool:
    """Nächsten wartenden Job prüfen; True = gestartet oder aus Queue entfernt."""
    if not _QUEUE:
        return False
    e = _QUEUE[0]
    job = e["jobs"].get(e["job_id"])
    if job is None or job.get("cancel") or job.get("status") != "running":
        _QUEUE.popleft()
        if job is not None and job.get("status") == "running":
            job["status"] = "cancelled"
            job["phase"] = "Abgebrochen (wartete auf freien RAM)"
        return True
    if time.time() - e["ts"] > MAX_WAIT_S:
        _QUEUE.popleft()
        job["status"] = "error"
        job["error"] = (f"RAM-Warteschlange: auch nach {MAX_WAIT_S // 3600}h "
                        "nicht genug freier Server-RAM")
        job["phase"] = "Fehler"
        return True
    try:
        from services import ram_guard
        ram_guard.trim_now()  # freien Heap ans OS zurückgeben, dann messen
    except Exception:  # noqa: BLE001
        pass
    free = free_mb()
    if free >= MIN_FREE_MB:
        _QUEUE.popleft()
        task = asyncio.create_task(e["factory"]())
        _watch_job_task(task, e["jobs"], e["job_id"])
        logger.info(f"ram_queue: {e['kind']} {e['job_id']} gestartet "
                    f"({int(free)} MB frei)")
        return True
    job["phase"] = _wait_phase(free)
    return False


def _ensure_watcher():
    global _watcher
    if _watcher is not None and not _watcher.done():
        return

    async def _loop():
        global _watcher
        try:
            while _QUEUE:
                await asyncio.sleep(CHECK_INTERVAL_S)
                _try_start_next()
        finally:
            _watcher = None

    try:
        _watcher = asyncio.get_event_loop().create_task(_loop())
    except RuntimeError:
        _watcher = None  # kein Event-Loop (Tests)
