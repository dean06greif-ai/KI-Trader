"""Pause / Fortsetzen für lange Rechen-Jobs (Backtester, Optimizer, Regime-Lab).

Design: Der Job bleibt während der Pause im Status "running" (belegt weiter
seinen Slot, alle bestehenden Status-/Active-/Cancel-Endpoints und die UI
funktionieren unverändert). Zusätzlich:
  job["pause"]  -> Pause angefordert (Flag, vom Nutzer gesetzt)
  job["paused"] -> Rechnung steht tatsächlich (an einem Checkpoint angehalten)

Checkpoints:
  - async:  `await wait_if_paused(job)` in den Orchestrierungs-Schleifen
            (blockiert nie den Event-Loop, nur den Job-Task)
  - sync:   `stop_check(job)` liefert die should_stop-Callback für
            simulate_pair & Co. In Worker-Threads (asyncio.to_thread) wartet
            sie blockierend – auf dem Event-Loop-Thread niemals.

Lokaler Worker: das Pause-Flag wandert über die Progress-Antwort zum Worker
(gleiches Modul dort) und wird in db.local_jobs gespiegelt, damit es einen
Server-Neustart überlebt (der Worker hält die Berechnung im RAM).
"""
import asyncio
import logging
import time
from typing import Callable, Dict, Optional

logger = logging.getLogger(__name__)

POLL_S = 0.5
PAUSE_PREFIX = "⏸ Pausiert · "


def request_pause(job: Dict) -> bool:
    if job.get("status") != "running" or job.get("cancel"):
        return False
    job["pause"] = True
    return True


def request_resume(job: Dict) -> bool:
    if job.get("status") != "running":
        return False
    job["pause"] = False
    _leave(job)
    return True


def pause_requested(job: Dict) -> bool:
    return bool(job.get("pause")) and not job.get("cancel")


def _pool_pause(flag: bool) -> None:
    """Multi-Core (lokaler Worker): Kind-Prozesse des Pools mit anhalten."""
    try:
        from services import parallel_sim
        parallel_sim.set_paused(flag)
    except Exception:  # noqa: BLE001
        pass


def _enter(job: Dict) -> None:
    if job.get("paused"):
        return
    job["paused"] = True
    job["paused_at"] = time.time()
    job["phase_before_pause"] = job.get("phase")
    job["phase"] = PAUSE_PREFIX + str(job.get("phase_before_pause") or "")
    _pool_pause(True)


def _leave(job: Dict) -> None:
    if not job.get("paused"):
        return
    _pool_pause(False)
    job["paused_total_s"] = round(
        float(job.get("paused_total_s") or 0.0)
        + time.time() - float(job.get("paused_at") or time.time()), 1)
    job["paused"] = False
    job.pop("paused_at", None)
    before = job.pop("phase_before_pause", None)
    if before:
        job["phase"] = before


def paused_seconds(job: Dict) -> float:
    """Gesamte Pausenzeit (für ETA-Korrektur), inkl. laufender Pause."""
    total = float(job.get("paused_total_s") or 0.0)
    if job.get("paused") and job.get("paused_at"):
        total += time.time() - float(job["paused_at"])
    return total


async def wait_if_paused(job: Dict) -> None:
    """Async-Checkpoint: hält den Job-Task an, solange Pause angefordert ist."""
    if not pause_requested(job):
        return
    _enter(job)
    while pause_requested(job):
        await asyncio.sleep(POLL_S)
    _leave(job)


def _on_event_loop_thread() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def stop_check(job: Dict) -> Callable[[], bool]:
    """should_stop-Callback: True = abbrechen. In Worker-Threads wartet sie
    während einer Pause blockierend (feine Granularität mitten in der
    Simulation); auf dem Event-Loop-Thread nur Abbruch-Prüfung."""
    def should_stop() -> bool:
        if pause_requested(job) and not _on_event_loop_thread():
            _enter(job)
            while pause_requested(job):
                time.sleep(POLL_S)
            _leave(job)
        return bool(job.get("cancel"))
    return should_stop


def public_state(job: Dict) -> Dict:
    return {"pause": bool(job.get("pause")), "paused": bool(job.get("paused")),
            "paused_total_s": round(paused_seconds(job), 1)}


async def persist_pause(db, job_id: str, pause: bool) -> None:
    """Pause-Flag in db.local_jobs spiegeln (nur wenn der Job dort existiert –
    also ein Job des lokalen Workers ist). Überlebt so Server-Neustarts."""
    if db is None:
        return
    try:
        await db.local_jobs.update_one({"_id": job_id}, {"$set": {"pause": bool(pause)}})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"persist_pause {job_id}: {e}")


def apply_remote_state(job: Dict, data: Dict) -> None:
    """Vom lokalen Worker gemeldeten Pausenzustand übernehmen (Anzeige)."""
    if "paused" in data:
        job["paused"] = bool(data.get("paused"))


def find_job(job_id: str) -> Optional[Dict]:
    """Job über alle Rechen-Stores finden (Backtester / Optimizer / Regime-Lab)."""
    from services import backtester as bt
    from services import optimizer as opt
    for store in (bt.JOBS, opt.JOBS):
        if job_id in store:
            return store[job_id]
    try:
        from services import regime_lab as rlab
        return rlab.JOBS.get(job_id)
    except Exception:  # noqa: BLE001
        return None
