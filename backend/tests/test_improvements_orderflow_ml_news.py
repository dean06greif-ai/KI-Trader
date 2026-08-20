"""Regressionstests für die Verbesserungen vom Juni 2026:

1. FX-/Rohstoff-Orderflow-Näherung aus echtem CME-Futures-Volumen (fx_orderflow)
2. ML-Positionssteuerung: Positionsgröße -50% bei Gate-OOS-AUC < 0.55 (ml_gate)
3. News-Tiefenanalyse: nur NEUE Ereignisse triggern (Token-Optimierung)
4. OpenRouter Bezahl-Modelle: Retry statt Fallback bei transientem Rate-Limit
5. stream_chain: kein NameError mehr bei Rate-Limit (idxs/streak_429 definiert)
"""
import asyncio
import time

import pytest


# ---------------- 1) fx_orderflow ----------------
def _mk_candles(n, up=True, vol=100.0):
    out = []
    for i in range(n):
        o, c = (1.0, 1.001) if up else (1.001, 1.0)
        out.append({"timestamp": i * 60000, "open": o, "close": c, "volume": vol})
    return out


class TestFxOrderflow:
    def test_buyer_dominant_delta_positive(self):
        from services.fx_orderflow import futures_flow_stats
        stats = futures_flow_stats(_mk_candles(60, up=True))
        assert stats is not None
        assert stats["delta_15m"] == 1.0
        assert stats["delta_60m"] == 1.0

    def test_inverted_contract_flips_direction(self):
        from services.fx_orderflow import futures_flow_stats
        stats = futures_flow_stats(_mk_candles(60, up=True), inverted=True)
        assert stats["delta_15m"] == -1.0

    def test_too_few_candles_returns_none(self):
        from services.fx_orderflow import futures_flow_stats
        assert futures_flow_stats(_mk_candles(5)) is None
        assert futures_flow_stats([]) is None
        # Kerzen ohne Volumen (Spot-FX) zählen nicht als Datenbasis
        zero = [{**c, "volume": 0} for c in _mk_candles(60)]
        assert futures_flow_stats(zero) is None

    def test_flow_text_mentions_real_futures_volume(self):
        from services.fx_orderflow import futures_flow_stats, flow_text
        stats = futures_flow_stats(_mk_candles(60, up=False))
        txt = flow_text("EURUSD", "6E=F", stats)
        assert "6E=F" in txt and "Futures-Volumen" in txt and "Verkäufer" in txt

    def test_futures_map_covers_all_forex(self):
        from services.fx_orderflow import FUTURES_MAP
        from core import instruments
        fx = [i.symbol for i in instruments.INSTRUMENTS
              if i.group == instruments.GROUP_FOREX]
        for sym in fx:
            assert sym in FUTURES_MAP, f"{sym} fehlt in FUTURES_MAP"
        # invertierte Kontrakte korrekt markiert
        assert FUTURES_MAP["USDJPY"][1] is True
        assert FUTURES_MAP["EURUSD"][1] is False

    def test_snapshot_text_cache_expiry(self):
        from services.fx_orderflow import FxOrderflowProxy
        p = FxOrderflowProxy()
        p._cache["EURUSD"] = {"ts": time.time(), "stats": {}, "text": "abc"}
        assert p.snapshot_text("eurusd") == "abc"
        p._cache["EURUSD"]["ts"] = time.time() - 3600
        assert p.snapshot_text("EURUSD") is None


# ---------------- 2) ml_gate.size_factor ----------------
class TestMlGateSizeFactor:
    def _gate(self, auc):
        from services.ml_gate import MLGate
        g = MLGate()
        if auc is not None:
            g.model_meta = {"version": 3, "metrics": {"oos_auc": auc}}
        return g

    def test_low_auc_halves_position(self):
        f, why = self._gate(0.52).size_factor()
        assert f == 0.5
        assert "0.520" in why and "0.55" in why

    def test_good_auc_no_reduction(self):
        assert self._gate(0.61).size_factor() == (1.0, "")

    def test_no_model_or_no_auc(self):
        assert self._gate(None).size_factor() == (1.0, "")
        g = self._gate(0.52)
        g.model_meta = {"metrics": {}}
        assert g.size_factor() == (1.0, "")

    def test_scaling_can_be_disabled_and_tuned(self):
        g = self._gate(0.52)
        g.settings["auc_risk_scaling"] = False
        assert g.size_factor()[0] == 1.0
        g.settings["auc_risk_scaling"] = True
        g.settings["auc_risk_factor"] = 0.3
        assert g.size_factor()[0] == 0.3

    def test_status_exposes_size_factor(self):
        st = self._gate(0.52).status()
        assert st["size_factor"] == 0.5
        assert st["size_factor_reason"]

    def test_update_settings_roundtrip(self):
        g = self._gate(None)
        out = asyncio.run(g.update_settings(
            {"auc_min": 0.6, "auc_risk_factor": 0.4, "auc_risk_scaling": True}))
        assert out["auc_min"] == 0.6 and out["auc_risk_factor"] == 0.4


# ---------------- 3) News-Tiefenanalyse Dedupe ----------------
class TestNewsDeepDedupe:
    def test_normalize_event_titles(self):
        from services.ai_news_watcher import normalize_event_titles
        t = normalize_event_titles([{"title": "FOMC Minutes: Rates unchanged!"},
                                    {"title": ""}, None])
        assert t == {"fomcminutesratesunchanged"}

    def test_same_event_does_not_retrigger(self):
        from services.ai_news_watcher import normalize_event_titles
        seen = set()
        ev = [{"title": "FOMC Minutes released"}]
        first = normalize_event_titles(ev) - seen
        assert first
        seen |= normalize_event_titles(ev)
        second = normalize_event_titles(ev) - seen
        assert not second  # identisches Ereignis -> keine zweite Tiefenanalyse
        changed = normalize_event_titles([{"title": "FOMC: Powell signals cut"}]) - seen
        assert changed  # echte Änderung -> triggert

    def test_should_trigger_deep_regression(self):
        from services.ai_news_watcher import should_trigger_deep
        now = time.time()
        assert should_trigger_deep("high", True, 0, now)
        assert should_trigger_deep("medium", True, 0, now)
        assert not should_trigger_deep("low", True, 0, now)
        assert not should_trigger_deep("high", False, 0, now)
        assert not should_trigger_deep("high", True, now - 60, now)  # Cooldown


# ---------------- 4+5) ai_providers: Paid-Retry & stream_chain ----------------
PAID = "deepseek/deepseek-v4-pro-0813"


def _rate_limit_error():
    return RuntimeError("Error code: 429 - rate limit exceeded, try again later")


class TestPaidModelRetry:
    def test_paid_model_retries_same_key_before_fallback(self, monkeypatch):
        from services import ai_providers as ap
        monkeypatch.setattr(ap, "PAID_RATE_LIMIT_RETRIES", (0.01, 0.01))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        calls = {"n": 0}

        async def fake_gen(provider, model, key, prompt, system, temperature, json_mode):
            calls["n"] += 1
            if calls["n"] < 3:
                raise _rate_limit_error()
            return '{"ok": true}'

        monkeypatch.setattr(ap, "_oai_generate", fake_gen)
        ap._key_limited.clear()
        text, prov, model = asyncio.run(ap.generate_chain(
            [("openrouter", PAID)], "p", "s"))
        assert text == '{"ok": true}'
        assert model == PAID          # KEIN Fallback trotz 2x 429
        assert calls["n"] == 3        # 1 Versuch + 2 Retries auf demselben Key

    def test_paid_429_does_not_poison_key_for_free_models(self, monkeypatch):
        from services import ai_providers as ap
        monkeypatch.setattr(ap, "PAID_RATE_LIMIT_RETRIES", (0.01,))
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        monkeypatch.delenv("OPENROUTER_API_KEY_BACKUP", raising=False)

        async def always_429(provider, model, key, prompt, system, temperature, json_mode):
            raise _rate_limit_error()

        monkeypatch.setattr(ap, "_oai_generate", always_429)
        ap._key_limited.clear()
        with pytest.raises(Exception):
            asyncio.run(ap.generate_chain([("openrouter", PAID)], "p", "s"))
        # Key darf NICHT im 10-min-Cooldown landen (Limit war modell-, nicht kontobezogen)
        assert not ap._key_limited.get("openrouter")

    def test_free_model_still_marks_key_limited(self, monkeypatch):
        from services import ai_providers as ap
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

        async def always_429(provider, model, key, prompt, system, temperature, json_mode):
            raise _rate_limit_error()

        monkeypatch.setattr(ap, "_oai_generate", always_429)
        ap._key_limited.clear()
        with pytest.raises(Exception):
            asyncio.run(ap.generate_chain([("groq", "openai/gpt-oss-20b")], "p", "s"))
        assert ap._key_limited.get("groq")  # bisheriges Verhalten bleibt
        ap._key_limited.clear()

    def test_payment_error_402_still_falls_back(self, monkeypatch):
        """402 (kein Guthaben) auf Bezahl-Modell darf NICHT endlos retryn."""
        from services import ai_providers as ap
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
        calls = {"n": 0}

        async def err_402(provider, model, key, prompt, system, temperature, json_mode):
            calls["n"] += 1
            raise RuntimeError("Error code: 402 - insufficient credit")

        monkeypatch.setattr(ap, "_oai_generate", err_402)
        ap._key_limited.clear()
        with pytest.raises(Exception):
            asyncio.run(ap.generate_chain([("openrouter", PAID)], "p", "s"))
        n_keys = len(ap.provider_keys("openrouter"))
        assert calls["n"] <= n_keys  # kein Retry-Loop pro Key bei 402
        ap._key_limited.clear()


class TestStreamChainRateLimitRegression:
    def test_stream_chain_429_yields_error_not_nameerror(self, monkeypatch):
        """Vorher: NameError (streak_429/idxs undefiniert) bei 429 im Chat-Stream."""
        from services import ai_providers as ap
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")

        class _FakeCompletions:
            async def create(self, **kwargs):
                raise _rate_limit_error()

        class _FakeClient:
            class chat:
                completions = _FakeCompletions()

        monkeypatch.setattr(ap, "_oai_client", lambda provider, key: _FakeClient())
        ap._key_limited.clear()

        async def run():
            events = []
            async for kind, payload in ap.stream_chain(
                    [("groq", "openai/gpt-oss-20b")], "p", "s"):
                events.append((kind, payload))
            return events

        events = asyncio.run(run())
        assert events and events[-1][0] == "error"  # sauberer Fehler statt Crash
        ap._key_limited.clear()
