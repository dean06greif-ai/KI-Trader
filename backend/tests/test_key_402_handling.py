"""402-Payment-Keys (totes Gratis-Kontingent, z.B. Cerebras "Payment required")
dürfen weder die Same-Org-Heuristik auslösen (sperrte alle 16 Keys wegen 3
toter Keys) noch alle 10 min neu gehämmert werden (Cooldown bis UTC-Mitternacht)."""
import asyncio

from services import ai_providers

PAY_ERR = ("Error code: 402 - {'message': 'Payment required to access this "
           "resource. Visit your billing tab.', 'type': 'payment_required'}")


def test_payment_error_detected():
    assert ai_providers.is_payment_error(Exception(PAY_ERR)) is True
    assert ai_providers.is_payment_error(Exception("429 rate limit")) is False


def test_402_cooldown_lasts_until_utc_midnight():
    cd = ai_providers._quota_cooldown_s(PAY_ERR)
    assert cd >= 30 * 60  # mindestens 30 min, i.d.R. bis Mitternacht UTC
    # normales Minuten-Rate-Limit bleibt beim kurzen Key-Cooldown
    assert ai_providers._quota_cooldown_s("429 too many requests") \
        == ai_providers.KEY_LIMIT_COOLDOWN_S


def test_402_keys_do_not_block_healthy_keys(monkeypatch):
    """User-Bug: 3 tote 402-Keys in Folge markierten via Same-Org-Streak ALLE
    16 Cerebras-Keys als 'übersprungen – Kontingent gilt pro Konto' – der
    Analyst wich unnötig auf fremde Fallbacks aus."""
    keys = [f"key{i}" for i in range(6)]

    async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
        if keys.index(key) < 3:  # Keys 1-3 = totes Konto (402)
            raise Exception(PAY_ERR)
        return "antwort"

    monkeypatch.setattr(ai_providers, "_oai_generate", fake_oai)
    monkeypatch.setattr(ai_providers, "provider_keys",
                        lambda p: keys if p == "cerebras" else [])
    ai_providers._key_limited.clear()
    ai_providers._rr_start.clear()

    text, prov, model = asyncio.run(ai_providers.generate_chain(
        [("cerebras", "gpt-oss-120b")], "prompt", "system"))
    assert text == "antwort" and prov == "cerebras"

    limited = ai_providers._key_limited.get("cerebras", {})
    # nur die 3 toten Keys markiert (mit langem 402-Cooldown) …
    assert set(limited.keys()) == {0, 1, 2}
    assert all(float(v["cooldown_s"]) >= 30 * 60 for v in limited.values())
    # … und KEIN gesunder Key kollektiv als "übersprungen" gesperrt
    assert all("übersprungen" not in str(v.get("detail", ""))
               for v in limited.values())
    ai_providers._key_limited.clear()
    ai_providers._rr_start.clear()


def test_real_429_streak_still_skips_same_org(monkeypatch):
    """Regression: echte 429 in Folge müssen die Same-Org-Heuristik weiterhin
    auslösen (Keys desselben Kontos teilen sich EIN Kontingent)."""
    keys = [f"key{i}" for i in range(6)]

    async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
        raise Exception("Error code: 429 - too many requests")

    monkeypatch.setattr(ai_providers, "_oai_generate", fake_oai)
    monkeypatch.setattr(ai_providers, "provider_keys",
                        lambda p: keys if p == "cerebras" else [])
    ai_providers._key_limited.clear()
    ai_providers._rr_start.clear()

    try:
        asyncio.run(ai_providers.generate_chain(
            [("cerebras", "gpt-oss-120b")], "prompt", "system"))
    except Exception:
        pass  # gesamte Kette scheitert erwartungsgemäß

    limited = ai_providers._key_limited.get("cerebras", {})
    assert any("übersprungen" in str(v.get("detail", ""))
               for v in limited.values()), limited
    ai_providers._key_limited.clear()
    ai_providers._rr_start.clear()
