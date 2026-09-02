"""RAM-Guard für kleine Instanzen (Render Free, 512 MB).

glibc-Malloc gibt freigegebenen Speicher oft NICHT ans OS zurück
(Arena-Fragmentierung) – der RSS wächst bei normalem Gebrauch (Trades
ansehen, Settings ändern, Chart-Historie) stetig, bis Render den Service
killt. Zwei unsichtbare Gegenmaßnahmen ohne Leistungs-/Qualitätsverlust:

1. mallopt(M_ARENA_MAX=2): begrenzt die Anzahl der Malloc-Arenen
   (Default: 8 × CPU-Kerne) – reduziert Fragmentierung massiv.
2. Periodisches malloc_trim(0): gibt bereits freien Heap ans OS zurück
   (nur ein Syscall, kostet praktisch nichts).

Env-Schalter: RAM_GUARD_ENABLED=0 (aus), RAM_TRIM_INTERVAL_S (Default 180).
"""
import asyncio
import ctypes
import gc
import logging
import os

logger = logging.getLogger(__name__)

ENABLED = os.environ.get("RAM_GUARD_ENABLED", "1") != "0"
TRIM_INTERVAL_S = int(os.environ.get("RAM_TRIM_INTERVAL_S", "180"))
_M_ARENA_MAX = -8  # mallopt-Konstante M_ARENA_MAX


def _libc():
    try:
        return ctypes.CDLL("libc.so.6", use_errno=True)
    except OSError:
        return None  # z.B. macOS/Alpine – Guard einfach still deaktivieren


def container_limit_mb():
    """RAM-Limit des Containers (cgroup) in MB – None, wenn kein Limit gesetzt."""
    try:
        for p in ("/sys/fs/cgroup/memory.max",
                  "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
            if os.path.isfile(p):
                raw = open(p).read().strip()
                if raw == "max":
                    return None
                v = int(raw)
                if v > (1 << 40):  # >1 TB = kein echtes Limit
                    return None
                return v / (1024 * 1024)
    except (OSError, ValueError):
        pass
    return None


def is_big_ram(threshold_mb: float = 1536) -> bool:
    """True, wenn der Server >= ~1,5 GB RAM hat (z.B. Render Pro 2 GB).
    Schaltet die 512-MB-Sparlimits (Kerzen-Cache, Orderflow-Ticks,
    ML-Trainingsmenge) automatisch auf größere Werte um."""
    lim = container_limit_mb()
    if lim is None:
        try:
            import psutil
            lim = psutil.virtual_memory().total / (1024 * 1024)
        except Exception:  # noqa: BLE001
            return False
    return lim >= threshold_mb


def setup_malloc() -> bool:
    """So früh wie möglich aufrufen (vor großen Allokationen)."""
    if not ENABLED:
        return False
    libc = _libc()
    if libc is None:
        return False
    try:
        libc.mallopt(_M_ARENA_MAX, 2)
        logger.info("RAM-Guard: malloc-Arenen auf 2 begrenzt (M_ARENA_MAX)")
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"RAM-Guard mallopt fehlgeschlagen: {e}")
        return False


def trim_now() -> bool:
    libc = _libc()
    if libc is None:
        return False
    try:
        gc.collect()
        libc.malloc_trim(0)
        return True
    except Exception:  # noqa: BLE001
        return False


async def trim_loop():
    """Hintergrund-Loop: alle TRIM_INTERVAL_S freien Heap ans OS zurückgeben."""
    if not ENABLED or _libc() is None:
        return
    logger.info(f"RAM-Guard: malloc_trim-Loop aktiv (alle {TRIM_INTERVAL_S}s)")
    while True:
        await asyncio.sleep(TRIM_INTERVAL_S)
        trim_now()
        # Telemetrie: 1 Log-Zeile alle 3 min – zeigt im Render-Log, wie der
        # RAM sich entwickelt und WANN es eng wird (OOM-Diagnose).
        try:
            import psutil
            from services import ram_queue
            rss = psutil.Process().memory_info().rss // (1024 * 1024)
            logger.info(f"RAM-Guard: Prozess {rss} MB belegt, "
                        f"Container {int(ram_queue.free_mb())} MB frei")
        except Exception:  # noqa: BLE001
            pass
