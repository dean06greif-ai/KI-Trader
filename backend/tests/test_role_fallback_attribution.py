"""Regression: Fallback-Warnungen müssen dem RICHTIGEN Assistenten zugeordnet
werden (Bug: News-Wächter zeigte OpenRouter-Fallback, obwohl OpenRouter nicht
in seinem Team ist – der Call kam von einem parallel laufenden Deep-Analysten,
der das globale _current_role überschrieb)."""
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


def test_record_result_explicit_role_wins_over_global():
    # Global steht (fälschlich) news_watcher – der Call gehört aber dem Deep-Analysten
    ai_providers.set_current_role("news_watcher")
    ai_providers.record_result(
        "openrouter", "nvidia/nemotron-3-super-120b-a12b:free", "ok",
        key_index=0, role="deep_analyst",
        requested="nvidia/nemotron-3-ultra-550b-a55b:free")
    assert "deep_analyst" in ai_providers._role_fallbacks
    assert "news_watcher" not in ai_providers._role_fallbacks


def test_record_result_without_role_falls_back_to_global():
    ai_providers.set_current_role("chat")
    ai_providers.record_result("groq", "model-b", "ok", key_index=1,
                               requested="model-a")
    assert ai_providers._role_fallbacks.get("chat", {}).get("provider") == "groq"


def test_generate_chain_keeps_role_despite_concurrent_role_switch(monkeypatch):
    """Simuliert den Race: Während der (langsame) OpenRouter-Call des
    Deep-Analysten läuft, setzt der News-Wächter das globale _current_role um.
    Der Fallback muss trotzdem dem Deep-Analysten zugeordnet bleiben."""
    monkeypatch.setenv("MISTRAL_API_KEY", "test-key")

    calls = {"n": 0}

    async def fake_oai_generate(provider, model, key, prompt, system,
                                temperature, json_mode):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("primary model down")
        # anderer Task überschreibt die globale Rolle mitten im Call
        ai_providers.set_current_role("news_watcher")
        return "ok-text"

    async def silent_notify(*a, **k):
        return None

    monkeypatch.setattr(ai_providers, "_oai_generate", fake_oai_generate)
    from services import notifications
    monkeypatch.setattr(notifications, "notify_model_failure", silent_notify,
                        raising=False)
    monkeypatch.setattr(notifications, "notify_ai_failure", silent_notify,
                        raising=False)

    chain = [("mistral", "model-primary"), ("mistral", "model-fallback")]
    text, provider, model = asyncio.run(ai_providers.generate_chain(
        chain, "prompt", "system", role="deep_analyst"))

    assert text == "ok-text"
    assert model == "model-fallback"
    fb = ai_providers._role_fallbacks
    assert "deep_analyst" in fb, f"Fallback fehlt/falsch zugeordnet: {fb}"
    assert "news_watcher" not in fb
    assert fb["deep_analyst"]["provider"] == "mistral"
    assert fb["deep_analyst"]["requested_model"] == "model-primary"
