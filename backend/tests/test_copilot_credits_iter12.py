"""Iter12: Copilot credits API + 402 chain edge cases (no-answer error, payment_alert wiring)."""
import asyncio
import os

import pytest
import requests

from services import key_credits as kc
from services import strategy_copilot as sc

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    with open("/app/frontend/.env") as f:
        for line in f:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")


# --- API: GET /api/copilot/credits
class TestCreditsAPI:
    def test_credits_shape_preview(self):
        r = requests.get(f"{BASE_URL}/api/copilot/credits", timeout=30)
        assert r.status_code == 200
        d = r.json()
        for k in ["key_source", "checked_at", "keys", "worst_level", "any_empty",
                  "warn_below_usd", "critical_below_usd"]:
            assert k in d, k
        assert isinstance(d["keys"], list)
        assert d["key_source"] == "none"
        assert d["worst_level"] == "ok" and d["any_empty"] is False
        assert isinstance(d["warn_below_usd"], (int, float))

    def test_status_still_ok(self):
        r = requests.get(f"{BASE_URL}/api/copilot/status", timeout=30)
        assert r.status_code == 200
        assert r.json()["ready"] is False


# --- view_for edge cases
def test_view_for_empty_snapshot():
    v = kc.view_for("Copilot", {"keys": [], "checked_at": None})
    assert v["worst_level"] == "ok" and v["keys"] == [] and v["any_empty"] is False


def test_view_for_warning_not_empty():
    snap = {"checked_at": "x", "keys": [{"label": "Copilot Haupt", "level": "warning",
                                         "remaining": 0.8, "total_credits": 5.0}]}
    v = kc.view_for("Copilot", snap)
    assert v["worst_level"] == "warning" and v["any_empty"] is False


def test_notify_payment_error_db_none():
    assert asyncio.run(kc.notify_payment_error(None, None, "C", "m", None)) is False


# --- chain: 402 on all keys and no other model answers -> 'Bezahl-Guthaben leer ... aufladen'
def test_chain_402_no_fallback_raises_payment_message(monkeypatch):
    alerts = []

    async def fake_call(self, key, model, prompt, timeout, system=None):
        if model == "paid/model":
            raise RuntimeError("Error code: 402 - Payment Required")
        raise RuntimeError("Error code: 500 - boom")

    async def fake_alert(self, models, fallback):
        alerts.append((models, fallback))

    async def fake_store(self, *a, **k):
        raise AssertionError("must not store on failure")
    monkeypatch.setattr(sc.StrategyCopilot, "_openrouter_call", fake_call)
    monkeypatch.setattr(sc.StrategyCopilot, "_payment_alert", fake_alert)
    monkeypatch.setattr(sc.StrategyCopilot, "_store", fake_store)
    with pytest.raises(RuntimeError) as ei:
        asyncio.run(sc.copilot._run_chain("hi", "optimizer", "s", "p", ["paid/model", "free/model"],
                                          ["k1", "k2"], 50.0, 20.0))
    msg = str(ei.value)
    assert "Bezahl-Guthaben leer" in msg and "aufladen" in msg
    assert alerts == [(["paid/model"], None)]


def test_chain_no_402_no_notice(monkeypatch):
    async def fake_call(self, key, model, prompt, timeout, system=None):
        return '{"reply": "ok", "proposal": null, "checks": ["a"]}'

    async def fake_store(self, *a, **k):
        return {"id": "m1"}
    called = []

    async def fake_alert(self, *a):
        called.append(a)
    monkeypatch.setattr(sc.StrategyCopilot, "_openrouter_call", fake_call)
    monkeypatch.setattr(sc.StrategyCopilot, "_store", fake_store)
    monkeypatch.setattr(sc.StrategyCopilot, "_payment_alert", fake_alert)
    res = asyncio.run(sc.copilot._run_chain("hi", "optimizer", "s", "p", ["m"], ["k1"], 50.0, 20.0))
    assert res["notice"] is None and res["checks"] == ["a"] and called == []


def test_payment_alert_calls_notify(monkeypatch):
    got = []

    async def fake_notify(db, tg, source, model, fallback):
        got.append((source, model, fallback))
        return True

    async def fake_check(db, tg):
        return {}
    monkeypatch.setattr(kc, "notify_payment_error", fake_notify)
    monkeypatch.setattr(kc, "check_once", fake_check)

    async def run():
        await sc.copilot._payment_alert(["a", "b"], "free/x")
        await asyncio.sleep(0.05)
    asyncio.run(run())
    assert got and got[0][1] == "a, b" and got[0][2] == "free/x"
