"""Iteration 53 – Fallback-Spam & Ursachen:
  A) Nemotron (OpenRouter) leere Antwort: JSON aus Reasoning-Feld, Retry mit reasoning aus
  B) Mistral 1-RPS-Limit (Code 1300): kurzer Retry auf demselben Key, 65s-Cooldown
  C) Keine Glocken-Warnung bei erfolgreichem Fallback / Einzel-Ausfall
"""
import asyncio
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import ai_providers as ap  # noqa: E402
from services import notifications  # noqa: E402


def _msg(content=None, reasoning=None, finish="stop"):
    m = SimpleNamespace(content=content, reasoning=reasoning, model_extra={})
    return SimpleNamespace(choices=[SimpleNamespace(message=m, finish_reason=finish)])


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

        async def create(**kw):
            self.calls.append(kw)
            r = self.responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=create))


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_reasoning_helpers():
    m = SimpleNamespace(content="", reasoning="ich denke... {\"a\": 1}", model_extra={})
    assert ap._reasoning_text(m).startswith("ich denke")
    assert ap._json_from_reasoning(ap._reasoning_text(m)) == '{"a": 1}'
    assert ap._json_from_reasoning("nur text ohne json") == ""


def test_oai_generate_takes_json_from_reasoning(monkeypatch):
    fake = _FakeClient([_msg(content="", reasoning="Plan: … {\"bias\": \"long\"}")])
    monkeypatch.setattr(ap, "_oai_client", lambda p, k: fake)
    out = _run(ap._oai_generate("openrouter", "nvidia/x:free", "k", "p", "s", 0.3, True))
    assert out == '{"bias": "long"}'
    assert fake.calls[0]["max_tokens"] == ap.OPENROUTER_MAX_TOKENS
    assert "extra_body" not in fake.calls[0]


def test_oai_generate_reasoning_off_flag(monkeypatch):
    fake = _FakeClient([_msg(content="{\"ok\": true}")])
    monkeypatch.setattr(ap, "_oai_client", lambda p, k: fake)
    out = _run(ap._oai_generate("openrouter", "nvidia/x:free", "k", "p", "s", 0.3, True,
                                reasoning_off=True))
    assert out == '{"ok": true}'
    assert fake.calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    # Nicht-OpenRouter: weder max_tokens noch extra_body
    fake2 = _FakeClient([_msg(content="hi")])
    monkeypatch.setattr(ap, "_oai_client", lambda p, k: fake2)
    _run(ap._oai_generate("groq", "m", "k", "p", "s", 0.3, False, reasoning_off=True))
    assert "max_tokens" not in fake2.calls[0] and "extra_body" not in fake2.calls[0]


def test_oai_generate_empty_raises_with_hint(monkeypatch):
    fake = _FakeClient([_msg(content="", reasoning="nur gedanken", finish="length")])
    monkeypatch.setattr(ap, "_oai_client", lambda p, k: fake)
    with pytest.raises(RuntimeError) as ei:
        _run(ap._oai_generate("openrouter", "nvidia/x:free", "k", "p", "s", 0.3, True))
    assert ap.is_empty_response_error(ei.value)
    assert "finish_reason=length" in str(ei.value)


def test_chain_retries_openrouter_with_reasoning_off(monkeypatch):
    monkeypatch.setattr(ap, "provider_keys", lambda p: ["k1"] if p == "openrouter" else [])
    monkeypatch.setattr(ap.asyncio, "sleep", _noop_sleep)
    seen = []

    async def fake_oai(provider, model, key, prompt, system, temperature, json_mode,
                       reasoning_off=False):
        seen.append(reasoning_off)
        if not reasoning_off:
            raise RuntimeError("Leere Antwort von openrouter/x (Reasoning ohne Antwort)")
        return '{"ok": 1}'
    monkeypatch.setattr(ap, "_oai_generate", fake_oai)
    text, prov, model = _run(ap.generate_chain(
        [("openrouter", "nvidia/nemotron-3-super-120b-a12b:free")], "p", "s", role="analyst"))
    assert text == '{"ok": 1}' and prov == "openrouter"
    assert seen == [False, True]


async def _noop_sleep(_s):
    return None


def test_mistral_rps_retry_same_key(monkeypatch):
    monkeypatch.setattr(ap, "provider_keys", lambda p: ["k1", "k2"] if p == "mistral" else [])
    monkeypatch.setattr(ap.asyncio, "sleep", _noop_sleep)
    ap._key_limited.clear()
    keys_used = []
    err = RuntimeError("Error code: 429 - {'object': 'error', 'message': 'Rate limit exceeded', "
                       "'type': 'rate_limited', 'param': None, 'code': '1300', 'raw_status_code': 429}")
    state = {"n": 0}

    async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
        keys_used.append(key)
        state["n"] += 1
        if state["n"] == 1:
            raise err
        return "ok"
    monkeypatch.setattr(ap, "_oai_generate", fake_oai)
    text, prov, _ = _run(ap.generate_chain([("mistral", "mistral-small-latest")], "p", "s",
                                           role="news_watcher"))
    assert text == "ok" and prov == "mistral"
    assert len(set(keys_used)) == 1, "Retry muss auf DEMSELBEN Key erfolgen"
    assert not ap._key_limited.get("mistral"), "kein Key darf gesperrt werden"
    assert ap.is_mistral_rps_limit("mistral", err)
    assert not ap.is_mistral_rps_limit("groq", err)


def test_mistral_1300_cooldown_is_minute_class():
    detail = "Error code: 429 - {'message': 'Rate limit exceeded', 'code': '1300'}"
    assert ap._quota_cooldown_s(detail) == ap.MINUTE_LIMIT_COOLDOWN_S


def test_no_bell_on_successful_fallback(monkeypatch):
    """Primär (openrouter) + Fallback (gemini) fallen aus, groq übernimmt ->
    KEINE notify_ai_failure / notify_model_failure mehr."""
    monkeypatch.setattr(ap, "provider_keys", lambda p: ["k"])
    monkeypatch.setattr(ap.asyncio, "sleep", _noop_sleep)
    calls = []

    async def spy_ai_failure(*a, **kw):
        calls.append(("ai_failure", a))

    async def spy_model_failure(*a, **kw):
        calls.append(("model_failure", a))
    monkeypatch.setattr(notifications, "notify_ai_failure", spy_ai_failure)
    monkeypatch.setattr(notifications, "notify_model_failure", spy_model_failure)

    async def fake_oai(provider, model, key, prompt, system, temperature, json_mode,
                       reasoning_off=False):
        if provider == "groq":
            return "ok"
        raise RuntimeError("Leere Antwort von x")

    async def fake_gemini(*a, **kw):
        raise RuntimeError("boom 500")
    monkeypatch.setattr(ap, "_oai_generate", fake_oai)
    monkeypatch.setattr(ap, "_gemini_generate", fake_gemini)
    text, prov, _ = _run(ap.generate_chain(
        [("openrouter", "nvidia/nemotron-3-super-120b-a12b:free"),
         ("gemini", "gemini-3.5-flash"), ("groq", "openai/gpt-oss-120b")],
        "p", "s", role="analyst"))
    assert prov == "groq"
    _run(asyncio.sleep(0))
    assert calls == [], f"Glocke darf bei erfolgreichem Fallback nicht feuern: {calls}"
    # Ausfälle bleiben im KI-Status-Verlauf sichtbar
    recent = [f for f in ap._recent_failures if f.get("role") == "analyst"]
    assert any(f["provider"] == "openrouter" for f in recent)


def test_bell_only_on_complete_failure(monkeypatch):
    monkeypatch.setattr(ap, "provider_keys", lambda p: ["k"])
    monkeypatch.setattr(ap.asyncio, "sleep", _noop_sleep)
    calls = []

    async def spy_ai_failure(role, failed, fallback, failures=None):
        calls.append((role, fallback))
    monkeypatch.setattr(notifications, "notify_ai_failure", spy_ai_failure)

    async def fake_oai(*a, **kw):
        raise RuntimeError("boom 500")
    monkeypatch.setattr(ap, "_oai_generate", fake_oai)
    with pytest.raises(RuntimeError):
        _run(ap.generate_chain([("groq", "openai/gpt-oss-120b")], "p", "s", role="analyst"))
    assert calls == [("analyst", None)]
