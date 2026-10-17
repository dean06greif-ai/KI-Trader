"""Regime-Lab-Copilot: eigener System-Prompt + eigener Kontext (kein Optimizer-Wissen).

Regression zu: „Der Copilot im Regime-Lab denkt, er sei im Strategie-Optimizer“ –
Strategie-Übersicht, Suchmodi, Min-Trades-Regel und Verläufe anderer Reiter
dürfen im Regime-Lab-Prompt NICHT mehr auftauchen. Reiner Unit-Test (kein
Netzwerk, kein Mongo): der OpenRouter-Call wird abgefangen.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__))))

from services import strategy_copilot as sc  # noqa: E402

OPTIMIZER_ONLY = ("Discovery", "Deep-Test", "MIN-TRADES", "STRATEGIE-ÜBERSICHT",
                  "Parameter-Optimierung", "Profit-Faktor", "bayes")


def test_regime_lab_system_prompt_has_no_optimizer_knowledge():
    p = sc.REGIME_LAB_SYSTEM_PROMPT
    for word in OPTIMIZER_ONLY:
        assert word not in p, f"Optimizer-Begriff im Regime-Lab-Prompt: {word}"
    for word in ("Shadow", "Ablation", "Autopilot", "95 %", "Wirksam", "Kalibrierung",
                 "unbewertet", "REGIME-LAB-STAND", '"type": "settings"'):
        assert word in p, f"Regime-Lab-Wissen fehlt: {word}"


def test_regime_lab_context_block_only_regime_data():
    ctx = {"panel": "regime_lab",
           "settings": {"coins": ["BTCUSDT"], "timeframe": "1d", "days": 1080, "train_pct": 75},
           "regime": {"detector": "ema", "calibration": {"after_pct": 48.9},
                      "ablation_last": {"best_variant": "full"},
                      "analysis": {"release": {"stage": "shadow", "shadow_trades": 3}}}}
    out = asyncio.run(sc.copilot._context_block(ctx))
    assert "REGIME-LAB-STAND" in out and "REGIME-LAB-EINSTELLUNGEN" in out
    assert "shadow_trades" in out and "48.9" in out
    for word in ("STRATEGIE-ÜBERSICHT", "MIN-TRADES", "ERLAUBTE INDIKATOREN", "ERLAUBTE OPERATOREN",
                 "GETEILTES KI-GEDÄCHTNIS", "LETZTES ERGEBNIS"):
        assert word not in out, f"Optimizer-Kontext im Regime-Lab: {word}"


def test_other_panels_still_use_shared_prompt_pieces():
    # Regression: Optimizer/Backtester/Builder unverändert
    assert "AKTUELLER REITER: STRATEGIE-OPTIMIZER" in sc.PANEL_PROMPTS["optimizer"]
    assert sc.normalize_panel("regime_lab") == "regime_lab"
    assert sc.normalize_panel("unbekannt") == sc.DEFAULT_PANEL


def test_chat_uses_dedicated_system_prompt_for_regime_lab(monkeypatch):
    captured = {}

    async def fake_call(self, key, model, prompt, timeout, system=None):
        captured["system"] = system
        captured["prompt"] = prompt
        return json.dumps({"reply": "ok", "proposal": None, "checks": []})

    async def fake_config(self):
        return {"model": None}

    async def no_history(self, limit=60, panel=None):
        return []

    async def no_store(self, role, content, extra=None, panel=None):
        return {"id": "x"}

    monkeypatch.setattr(sc.StrategyCopilot, "_openrouter_call", fake_call)
    monkeypatch.setattr(sc.StrategyCopilot, "config", fake_config)
    monkeypatch.setattr(sc.StrategyCopilot, "history", no_history)
    monkeypatch.setattr(sc.StrategyCopilot, "_store", no_store)
    monkeypatch.setattr(sc, "copilot_keys", lambda: ["k"])
    monkeypatch.setattr(sc.ai_providers, "allowed_models", lambda p: ["m1"])

    async def digest_guard(self, panel, per_panel=4, max_chars=160):
        raise AssertionError("Digest anderer Reiter darf im Regime-Lab nicht geladen werden")
    monkeypatch.setattr(sc.StrategyCopilot, "_other_panels_digest", digest_guard)

    res = asyncio.run(sc.StrategyCopilot().chat(
        "Was jetzt?", {"panel": "regime_lab", "regime": {"detector": "ema"}}))
    assert res["reply"] == "ok"
    assert captured["system"] == sc.REGIME_LAB_SYSTEM_PROMPT
    assert "VERLÄUFE DER ANDEREN REITER" not in captured["prompt"]
    assert "REGIME-LAB-STAND" in captured["prompt"]


def test_chat_optimizer_panel_keeps_legacy_prompt(monkeypatch):
    captured = {}

    async def fake_call(self, key, model, prompt, timeout, system=None):
        captured["system"] = system
        return json.dumps({"reply": "ok"})

    async def fake_config(self):
        return {"model": None}

    async def no_history(self, limit=60, panel=None):
        return []

    async def no_store(self, role, content, extra=None, panel=None):
        return {"id": "x"}

    async def no_digest(self, panel, per_panel=4, max_chars=160):
        return ""

    async def ctx_block(self, ctx):
        return "KONTEXT"

    monkeypatch.setattr(sc.StrategyCopilot, "_openrouter_call", fake_call)
    monkeypatch.setattr(sc.StrategyCopilot, "config", fake_config)
    monkeypatch.setattr(sc.StrategyCopilot, "history", no_history)
    monkeypatch.setattr(sc.StrategyCopilot, "_store", no_store)
    monkeypatch.setattr(sc.StrategyCopilot, "_other_panels_digest", no_digest)
    monkeypatch.setattr(sc.StrategyCopilot, "_context_block", ctx_block)
    monkeypatch.setattr(sc, "copilot_keys", lambda: ["k"])
    monkeypatch.setattr(sc.ai_providers, "allowed_models", lambda p: ["m1"])

    asyncio.run(sc.StrategyCopilot().chat("hi", {"panel": "optimizer"}))
    assert captured["system"].startswith(sc.SYSTEM_PROMPT)
    assert sc.PANEL_PROMPTS["optimizer"] in captured["system"]
