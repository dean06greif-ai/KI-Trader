"""Event-Loop-Wächter (core/loop_watchdog.py): erkennt Blockaden und nennt den Verursacher."""
import asyncio
import time

from core import loop_watchdog as lw


def _blocking_section():
    time.sleep(1.2)   # simuliert synchrone Rechenarbeit im Loop


def test_detects_block_and_names_culprit(monkeypatch):
    monkeypatch.setattr(lw, "THRESHOLD_S", 0.2)
    monkeypatch.setattr(lw, "_state", {"beat": None, "loop_thread": None, "started": False,
                                       "max_lag_s": 0.0, "blocks": 0})
    monkeypatch.setattr(lw, "_APP_MARKERS", lw._APP_MARKERS + ("/tests/",))
    lw._events.clear()
    lw._current.clear()

    async def main():
        lw.start()
        await asyncio.sleep(0.6)
        _blocking_section()
        await asyncio.sleep(1.0)
        return lw.snapshot()

    snap = asyncio.run(main())
    assert snap["enabled"] and snap["blocks"] >= 1
    ev = snap["recent"][0]
    assert ev["blocked_s"] >= 0.3
    assert any("_blocking_section" in w for w in ev["where"])


def test_app_frames_filters_library_frames():
    import sys
    frames = lw.app_frames(sys._getframe(), limit=3)
    assert isinstance(frames, list)
    assert all("site-packages" not in f for f in frames)
