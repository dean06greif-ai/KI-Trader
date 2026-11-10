"""06/2026: Copilot-Guthaben – Anzeige-Ausschnitt, 402-Erkennung mit Key-Wechsel, Telegram-Dedupe."""
import asyncio

from services import key_credits as kc
from services import strategy_copilot as sc


def _snap():
    return {"checked_at": "x", "keys": [
        {"label": "Copilot Haupt", "level": "critical", "remaining": 0.0, "total_credits": 5.0},
        {"label": "Copilot Backup 1", "level": "ok", "remaining": 4.0, "total_credits": 5.0},
        {"label": "OpenRouter Haupt", "level": "free", "remaining": 0.0, "total_credits": 0.0}]}


def test_view_for_filters_group_and_flags_empty():
    v = kc.view_for("Copilot", _snap())
    assert v["worst_level"] == "critical" and v["any_empty"] is True and len(v["keys"]) == 2
    o = kc.view_for("OpenRouter", _snap())
    assert o["worst_level"] == "ok" and o["any_empty"] is False


class _FakeColl:
    def __init__(self):
        self.doc = {}

    async def find_one(self, q):
        return self.doc or None

    async def update_one(self, q, upd, upsert=False):
        self.doc = {**self.doc, **upd["$set"]}


class _FakeDB:
    def __init__(self):
        self.settings = _FakeColl()


def test_notify_payment_error_dedupes(monkeypatch):
    sent = []

    async def fake_notify(db, tg, ntype, text, **k):
        sent.append(text)
        return True
    from services import notifications
    monkeypatch.setattr(notifications, "telegram_notify", fake_notify)
    db = _FakeDB()
    assert asyncio.run(kc.notify_payment_error(db, None, "Copilot", "x-ai/grok", "free-m")) is True
    assert asyncio.run(kc.notify_payment_error(db, None, "Copilot", "x-ai/grok", "free-m")) is False
    assert len(sent) == 1 and "BEZAHL-GUTHABEN LEER" in sent[0] and "free-m" in sent[0]


def test_chain_402_tries_next_key_then_free_model_with_notice(monkeypatch):
    calls, alerts = [], []

    async def fake_call(self, key, model, prompt, timeout, system=None):
        calls.append((key, model))
        if model == "paid/model":
            raise RuntimeError("Error code: 402 - insufficient credits")
        return '{"reply": "ok", "proposal": null, "checks": []}'

    async def fake_alert(self, models, fallback):
        alerts.append((models, fallback))

    async def fake_store(self, role, content, extra=None, panel=None):
        return {"id": "m1"}
    monkeypatch.setattr(sc.StrategyCopilot, "_openrouter_call", fake_call)
    monkeypatch.setattr(sc.StrategyCopilot, "_payment_alert", fake_alert)
    monkeypatch.setattr(sc.StrategyCopilot, "_store", fake_store)
    res = asyncio.run(sc.copilot._run_chain("hi", "optimizer", "sys", "p", ["paid/model", "free/model"],
                                            ["k1", "k2"], 50.0, 20.0))
    assert [c for c in calls if c[1] == "paid/model"] == [("k1", "paid/model"), ("k2", "paid/model")]
    assert res["model"] == "free/model" and res["notice"] and res["checks"][0].startswith("💳")
    assert alerts == [(["paid/model"], "free/model")]
