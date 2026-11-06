"""Iteration 32 (Testing Agent): Review des Diagnose-Endpoints
GET /api/ai/openrouter/keys + Key-Status in /api/ai/status.

Prüft: Auth (401 ohne JWT), Antwortstruktur, Key-MASKIERUNG (kein Klartext-Key),
runtime_limits/note, und dass /api/ai/status 5 OpenRouter-Keys kennt.
"""
import os
import re

import pytest
import requests
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL")
            or _env.get("REACT_APP_BACKEND_URL")).rstrip("/")
TIMEOUT = 60
_benv = dotenv_values("/app/backend/.env")
REAL_KEYS = [v for k, v in _benv.items()
             if re.match(r"^OPENROUTER_API_KEY(_BACKUP\d*)?$", k) and v]


@pytest.fixture(scope="module")
def admin_jwt():
    r = requests.post(f"{BASE_URL}/api/auth/login", timeout=TIMEOUT,
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"})
    if r.status_code != 200:
        pytest.fail(f"Admin-Login fehlgeschlagen: {r.status_code} {r.text[:200]}")
    tok = r.json().get("token")
    assert tok, "Login-Antwort ohne token"
    return tok


class TestOpenRouterKeysEndpoint:
    def test_no_auth_401(self):
        r = requests.get(f"{BASE_URL}/api/ai/openrouter/keys", timeout=TIMEOUT)
        assert r.status_code == 401, r.text[:200]

    def test_bad_token_401(self):
        r = requests.get(f"{BASE_URL}/api/ai/openrouter/keys", timeout=TIMEOUT,
                         headers={"Authorization": "Bearer nonsense.token.value"})
        assert r.status_code == 401, r.text[:200]

    def test_structure_and_masking(self, admin_jwt):
        r = requests.get(f"{BASE_URL}/api/ai/openrouter/keys", timeout=180,
                         headers={"Authorization": f"Bearer {admin_jwt}"})
        assert r.status_code == 200, r.text[:300]
        d = r.json()
        assert "_id" not in str(d)
        assert "note" in d and isinstance(d["note"], str) and d["note"]
        assert "runtime_limits" in d and isinstance(d["runtime_limits"], dict)
        keys = d.get("keys")
        assert isinstance(keys, list) and len(keys) == len(REAL_KEYS) == 5, keys
        raw = r.text
        for i, k in enumerate(keys, start=1):
            assert k["index"] == i
            masked = k["key_masked"]
            assert masked.startswith("sk-or-v1-") and "…" in masked, masked
            assert len(masked) <= 25, masked
            assert "valid" in k
            if k.get("valid"):
                assert isinstance(k["is_free_tier"], bool)
                assert k["daily_free_requests"] in (50, 1000)
                assert isinstance(k["hint"], str) and k["hint"]
        # Kein vollständiger Key im Klartext in der Antwort
        for real in REAL_KEYS:
            assert real not in raw, "Klartext-Key in Antwort!"


class TestAiStatusKeyCount:
    def test_openrouter_total_five(self):
        r = requests.get(f"{BASE_URL}/api/ai/status", timeout=TIMEOUT)
        assert r.status_code == 200, r.text[:200]
        d = r.json()
        ks = ((d.get("providers_health") or {}).get("key_status") or {})
        assert "openrouter" in ks, list(ks)
        assert ks["openrouter"]["total"] == 5, ks["openrouter"]


# --- Unit: stream_chain (Chat) darf bei Upstream-Overload keine Keys sperren ---
class _FakeStream:
    def __aiter__(self):
        async def gen():
            class _D:
                content = "hi"

            class _C:
                delta = _D()

            class _Chunk:
                choices = [_C()]
            yield _Chunk()
        return gen()


class _FakeCompletions:
    def __init__(self, model_fail):
        self.model_fail = model_fail

    async def create(self, model=None, **kw):
        if model == self.model_fail:
            raise Exception("429: model is overloaded upstream")
        return _FakeStream()


class _FakeClient:
    def __init__(self, model_fail):
        self.chat = type("X", (), {"completions": _FakeCompletions(model_fail)})()


def test_stream_chain_upstream_overload_keeps_keys(monkeypatch):
    import asyncio
    from services import ai_providers as ap

    monkeypatch.setattr(ap, "_oai_client", lambda p, k: _FakeClient("m-bad"))
    monkeypatch.setenv("OPENROUTER_API_KEY", "k1")
    monkeypatch.setenv("OPENROUTER_API_KEY_BACKUP", "k2")
    ap._key_limited.pop("openrouter", None)
    ap._rr_start.pop("openrouter", None)

    async def run():
        out = []
        async for ev in ap.stream_chain([("openrouter", "m-bad"),
                                         ("openrouter", "m-ok")], "p", "s"):
            out.append(ev)
        return out

    events = asyncio.run(run())
    assert ("token", "hi") in events
    meta = [e for e in events if e[0] == "meta"]
    assert meta and meta[0][1][1] == "m-ok", events
    assert not ap._key_limited.get("openrouter"), ap._key_limited
    ap._key_limited.pop("openrouter", None)
