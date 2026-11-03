"""Regressionstests: Preview-/Dev-Guard AI_TRADER_LOCAL_DISABLE
(core/config.py) – eine zweite Instanz neben der Render-Prod darf keine
LLM-Analysen fahren; auf Render (Env nicht gesetzt) keine Verhaltensänderung.
"""
import asyncio

import pytest

from core.config import local_engine_disabled


def test_guard_off_by_default(monkeypatch):
    monkeypatch.delenv("AI_TRADER_LOCAL_DISABLE", raising=False)
    assert local_engine_disabled() is False


@pytest.mark.parametrize("val,expected", [
    ("1", True), ("true", True), ("YES", True),
    ("0", False), ("", False), ("no", False),
])
def test_guard_env_values(monkeypatch, val, expected):
    monkeypatch.setenv("AI_TRADER_LOCAL_DISABLE", val)
    assert local_engine_disabled() is expected


def test_scheduled_runs_skipped_when_guard_active(monkeypatch):
    monkeypatch.setenv("AI_TRADER_LOCAL_DISABLE", "1")
    from services.ai_engine import AIEngine
    eng = AIEngine()
    res = asyncio.run(eng.run_analysis(manual=False))
    assert res["status"] == "skipped"
    res_deep = asyncio.run(eng.run_deep_analysis(manual=False))
    assert res_deep["status"] == "skipped"


def test_status_reports_engine_locally_off(monkeypatch):
    monkeypatch.setenv("AI_TRADER_LOCAL_DISABLE", "1")
    from datetime import datetime
    from services.ai_engine import AIEngine

    class _Scanner:
        def berlin_now(self):
            return datetime.now()

    eng = AIEngine()
    eng.scanner = _Scanner()
    st = eng.status()
    assert st["config"]["enabled"] is False
    assert st["config"]["local_disabled"] is True
