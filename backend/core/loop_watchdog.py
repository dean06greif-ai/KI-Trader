"""Event-Loop-Wächter: erkennt, wenn der (einzige) asyncio-Loop blockiert ist.

Alle API-Anfragen – auch „Optimierung starten“ / „Regime suchen“ – teilen sich
einen Event-Loop mit ~30 Hintergrund-Schleifen. Rechnet eine davon synchron
(ohne await/Thread), wartet jede Anfrage, bis sie fertig ist; für den Nutzer
sieht das aus wie ein „hängender“ Klick.

Ein Heartbeat-Task tickt im Loop; ein Daemon-Thread prüft, ob der Tick
ausbleibt. Bleibt er länger als THRESHOLD_S aus, wird EINMAL je Blockade der
Stack des Loop-Threads mitgeschnitten (sys._current_frames) – damit ist der
Verursacher im Log und unter /api/system/loop-health sichtbar. Kosten: ein
Timer alle 0,25 s im Loop und ein schlafender Thread.
"""
import asyncio
import logging
import os
import sys
import threading
import time
import traceback
from collections import deque
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

TICK_S = 0.25
THRESHOLD_S = float(os.environ.get("LOOP_WATCHDOG_THRESHOLD_S", "1.0"))
MAX_EVENTS = 30
_APP_MARKERS = ("/services/", "/routers/", "/core/", "/strategies/", "server.py")

_state: Dict = {"beat": None, "loop_thread": None, "started": False,
                "max_lag_s": 0.0, "blocks": 0}
_events: deque = deque(maxlen=MAX_EVENTS)
_current: Dict = {}


def app_frames(frame, limit: int = 6) -> List[str]:
    """Die innersten Frames aus dem App-Code (rein) – Bibliotheken ausgeblendet,
    damit der Verursacher (Datei:Zeile Funktion) direkt lesbar ist."""
    out = []
    for fs in reversed(traceback.extract_stack(frame)):
        path = fs.filename.replace("\\", "/")
        if any(m in path for m in _APP_MARKERS) and not path.endswith("core/loop_watchdog.py"):
            tail = path.split("/backend/")[-1]
            out.append(f"{tail}:{fs.lineno} {fs.name}")
            if len(out) >= limit:
                break
    return out


async def _heartbeat():
    while True:
        _state["beat"] = time.monotonic()
        await asyncio.sleep(TICK_S)


def _finish_block(now: float) -> None:
    lag = now - _current["since"]
    _state["max_lag_s"] = max(_state["max_lag_s"], lag)
    ev = {"at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(_current["wall"])),
          "blocked_s": round(lag, 2), "where": _current.get("where") or []}
    _events.append(ev)
    _state["blocks"] += 1
    logger.warning(f"Event-Loop {ev['blocked_s']}s blockiert – "
                   f"{' <- '.join(ev['where'][:3]) or 'unbekannt'}")
    _current.clear()


def _watch():
    while True:
        time.sleep(TICK_S)
        beat = _state["beat"]
        if beat is None:
            continue
        now = time.monotonic()
        stalled = now - beat > THRESHOLD_S + TICK_S
        if stalled and not _current:
            frame = sys._current_frames().get(_state["loop_thread"])
            _current.update(since=beat + TICK_S, wall=time.time() - (now - beat - TICK_S),
                            where=app_frames(frame) if frame else [])
        elif not stalled and _current:
            _finish_block(now)


def start() -> None:
    """Im laufenden Loop aufrufen (lifespan). Mehrfachaufruf ist harmlos."""
    if _state["started"] or os.environ.get("LOOP_WATCHDOG_DISABLE") == "1":
        return
    _state["started"] = True
    _state["loop_thread"] = threading.get_ident()
    asyncio.get_running_loop().create_task(_heartbeat())
    threading.Thread(target=_watch, name="loop-watchdog", daemon=True).start()


def snapshot() -> Dict:
    beat: Optional[float] = _state["beat"]
    return {"enabled": _state["started"], "threshold_s": THRESHOLD_S,
            "current_lag_s": round(max(time.monotonic() - beat - TICK_S, 0.0), 2) if beat else None,
            "max_lag_s": round(_state["max_lag_s"], 2), "blocks": _state["blocks"],
            "recent": list(_events)[::-1]}
