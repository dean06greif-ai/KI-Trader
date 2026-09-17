"""Regression: Key-Rotation-Verbesserungen (Minuten-Limit-Cooldown, Upstream-Overload).

- Minuten-Limits (20 req/min pro Konto) sperren Keys nur ~65s statt 10 min
- Tages-Limits sperren weiterhin bis UTC-Mitternacht
- Upstream-Überlastung eines Free-Modells verbrennt keine Keys mehr:
  generate_chain wechselt direkt zum nächsten Modell
- Diagnose-Endpoint /api/ai/openrouter/keys (Admin)
"""
import asyncio
import os

import pytest
import requests
from dotenv import dotenv_values

from services import ai_providers as ap

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
TIMEOUT = 60


class TestCooldownClassification:
    def test_minute_limit_short_cooldown(self):
        msgs = [
            "Rate limit exceeded: free-models-per-min",
            "429: Rate limit reached for model, limit 30 RPM",
            "requests per minute exceeded, try again",
        ]
        for m in msgs:
            assert ap._quota_cooldown_s(m) == ap.MINUTE_LIMIT_COOLDOWN_S, m

    def test_daily_limit_until_midnight(self):
        msgs = [
            "Rate limit exceeded: free-models-per-day. Add 10 credits",
            "429: tokens_per_day limit reached",
        ]
        for m in msgs:
            assert ap._quota_cooldown_s(m) > ap.KEY_LIMIT_COOLDOWN_S, m

    def test_unknown_429_default_cooldown(self):
        assert ap._quota_cooldown_s("429 too many requests") == ap.KEY_LIMIT_COOLDOWN_S

    def test_payment_until_midnight(self):
        assert ap._quota_cooldown_s("Error code: 402 payment required") \
            > ap.KEY_LIMIT_COOLDOWN_S


class TestUpstreamOverload:
    def test_detection(self):
        assert ap.is_upstream_overload(
            Exception("429: nvidia/nemotron-3.5 is temporarily rate-limited upstream"))
        assert ap.is_upstream_overload(Exception("no instances available"))
        assert not ap.is_upstream_overload(Exception("Rate limit exceeded: free-models-per-day"))

    def test_chain_skips_keys_on_upstream_overload(self, monkeypatch):
        """Upstream-429: kein Key darf gesperrt werden, nächstes Modell übernimmt."""
        calls = []

        async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
            calls.append((model, key))
            if model == "m-overloaded":
                raise Exception("429: m-overloaded is temporarily rate-limited upstream")
            return "ok-text"

        monkeypatch.setattr(ap, "_oai_generate", fake_oai)
        monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
        monkeypatch.setenv("OPENROUTER_API_KEY_BACKUP", "k2")
        monkeypatch.setenv("OPENROUTER_API_KEY_BACKUP1", "k3")
        ap._key_limited.pop("openrouter", None)
        ap._rr_start.pop("openrouter", None)

        chain = [("openrouter", "m-overloaded"), ("openrouter", "m-good")]
        text, prov, model = asyncio.run(ap.generate_chain(chain, "p", "s"))
        assert text == "ok-text" and model == "m-good"
        # Nur EIN Versuch auf dem überlasteten Modell (kein Key-Durchprobieren)
        assert len([c for c in calls if c[0] == "m-overloaded"]) == 1
        # KEIN Key wurde in den Cooldown geschickt
        assert not ap._key_limited.get("openrouter")

    def test_normal_429_still_rotates_keys(self, monkeypatch):
        """Konto-Limit (kein Upstream): weiterhin Key-Rotation wie bisher."""
        calls = []

        async def fake_oai(provider, model, key, prompt, system, temperature, json_mode):
            calls.append(key)
            if key == "k1":
                raise Exception("429: Rate limit exceeded: free-models-per-min")
            return "ok"

        monkeypatch.setattr(ap, "_oai_generate", fake_oai)
        monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
        monkeypatch.setenv("OPENROUTER_API_KEY_BACKUP", "k2")
        ap._key_limited.pop("openrouter", None)
        ap._rr_start.pop("openrouter", None)

        text, _, _ = asyncio.run(ap.generate_chain([("openrouter", "m")], "p", "s"))
        assert text == "ok"
        assert "k2" in calls
        # k1 (Index 0) ist im kurzen Minuten-Cooldown
        lim = ap._key_limited.get("openrouter", {})
        assert 0 in lim and lim[0]["cooldown_s"] == ap.MINUTE_LIMIT_COOLDOWN_S
        ap._key_limited.pop("openrouter", None)


class TestKeyStatusEndpoint:
    def test_requires_admin(self):
        r = requests.get(f"{BASE_URL}/api/ai/openrouter/keys", timeout=TIMEOUT)
        assert r.status_code == 401

    def test_returns_key_list(self):
        r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                          json={"username": os.environ.get("ADMIN_USER", "Admin"),
                                "password": os.environ.get("ADMIN_PASSWORD",
                                                           "Dean06Greif!/Admin")})
        assert r.status_code == 200
        jwt = r.json()["token"]
        r2 = requests.get(f"{BASE_URL}/api/ai/openrouter/keys", timeout=120,
                          headers={"Authorization": f"Bearer {jwt}"})
        assert r2.status_code == 200, r2.text[:300]
        d = r2.json()
        assert "keys" in d and isinstance(d["keys"], list) and d["keys"]
        for k in d["keys"]:
            assert "key_masked" in k and "index" in k
