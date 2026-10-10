"""Iteration 9 (Testing-Agent): zusätzliche Verifikation der Rollen-Attribution
für stream_chain + health_status(), analog zu test_role_fallback_attribution.py."""
import asyncio

import pytest

from services import ai_providers


@pytest.fixture(autouse=True)
def _clean_state():
    ai_providers._role_fallbacks.clear()
    ai_providers._last_call.clear()
    ai_providers.set_current_role(None)
    yield
    ai_providers._role_fallbacks.clear()
    ai_providers._last_call.clear()
    ai_providers.set_current_role(None)


def _silence_notifications(monkeypatch):
    async def silent(*a, **k):
        return None
    from services import notifications
    for name in ("notify_model_failure", "notify_ai_failure"):
        monkeypatch.setattr(notifications, name, silent, raising=False)
    monkeypatch.setattr(ai_providers, "notify_model_failure", silent, raising=False)
    monkeypatch.setattr(ai_providers, "notify_ai_failure", silent, raising=False)


def test_stream_chain_keeps_role_despite_concurrent_switch(monkeypatch):
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")
    _silence_notifications(monkeypatch)

    calls = {"n": 0}

    class _FakeStream:
        def __init__(self):
            self._done = False

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self._done:
                raise StopAsyncIteration
            self._done = True

            class _D:
                content = "hello"

            class _C:
                delta = _D()

            class _Chunk:
                choices = [_C()]
            return _Chunk()

    class _FakeCompletions:
        async def create(self, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("primary model down")
            # anderer Task überschreibt die globale Rolle mitten im Call
            ai_providers.set_current_role("news_watcher")
            return _FakeStream()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeClient:
        chat = _FakeChat()

    monkeypatch.setattr(ai_providers, "_oai_client", lambda *a, **k: _FakeClient(),
                        raising=False)

    chain = [("mistral", "model-primary"), ("mistral", "model-fallback")]

    async def _run():
        out = []
        async for kind, payload in ai_providers.stream_chain(
                chain, "prompt", "system", role="deep_analyst"):
            out.append((kind, payload))
        return out

    events = asyncio.run(_run())
    assert any(k == "token" for k, _ in events), events

    fb = ai_providers._role_fallbacks
    assert "deep_analyst" in fb, f"Fallback falsch zugeordnet: {fb}"
    assert "news_watcher" not in fb
    assert fb["deep_analyst"]["requested_model"] == "model-primary"


def test_health_status_active_fallbacks_shape():
    ai_providers.set_current_role("news_watcher")
    ai_providers.record_result("openrouter", "nemotron-x", "ok", key_index=1,
                               role="deep_analyst", requested="nemotron-ultra")
    h = ai_providers.health_status()
    afs = h.get("active_fallbacks")
    assert isinstance(afs, list) and afs
    entry = [e for e in afs if e.get("role") == "deep_analyst"]
    assert entry, afs
    e = entry[0]
    for field in ("role", "provider", "model", "requested_model", "key_index",
                  "ts", "age_s"):
        assert field in e, f"{field} fehlt in {e}"
    assert e["provider"] == "openrouter"
    assert e["requested_model"] == "nemotron-ultra"
    assert not [x for x in afs if x.get("role") == "news_watcher"]
    for key in ("models", "rate_limited", "recent_failures"):
        assert key in h
