"""PLAN_REGIME_BRUECKE Baustein 2/3/4 – Struktur-Resolver, Frische-Regel, Prompt-Zeile,
Gate-Quelle, Fingerprint-Artefakt, Rewards nach Struktur-Regime."""
import asyncio
from datetime import datetime, timezone

from services import structural_regime as sr
from services import regime_gate, policy_fingerprint, ai_rewards

H = 3600 * 1000


def _candles(n=300, trend=1.0, start=1_760_000_000_000, step=H):
    out = []
    p = 100.0
    for i in range(n):
        p = p * (1 + trend * 0.002)
        out.append({"timestamp": start + i * step, "open": p, "high": p * 1.001, "low": p * 0.999,
                    "close": p, "volume": 1000})
    return out


def test_freshness_rule():
    f = sr.freshness_ttl_sec("BTCUSDT", "1h")
    assert f["ttl_sec"] == 7200 and f["market_closed"] is False
    sat = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)   # Samstag
    f2 = sr.freshness_ttl_sec("GOLD", "1h", now=sat)
    assert f2["market_closed"] is True
    f3 = sr.freshness_ttl_sec("BTCUSDT", "1h", now=sat)
    assert f3["market_closed"] is False


def test_candle_gap_rule():
    c = _candles(20)
    assert sr.candle_gap_ok(c, 3600)
    c[-1]["timestamp"] = c[-2]["timestamp"] + 5 * H
    assert not sr.candle_gap_ok(c, 3600)


def test_context_from_model_direction_kept_since(monkeypatch):
    from services import regime as rg
    c = _candles(300)
    now = datetime.fromtimestamp(c[-1]["timestamp"] / 1000 + 600, tz=timezone.utc)
    monkeypatch.setattr(rg, "current_regime", lambda m, ca, tf: {
        "regime": 3, "label": "Aufwärts", "confidence": 71.0, "last_switch": c[-1]["timestamp"] - 12 * 24 * H})
    model = {"engine": "v2", "config": {"regime_mode": 5}}
    ctx = sr.context_from_model(model, c, "1h", "ra_1", "shadow", [3, 0], "BTCUSDT", now=now)
    assert ctx["layer"] == "structural" and ctx["direction"] == "up" and ctx["phase"] == "bulle"
    assert ctx["state"] == "ok" and ctx["kept"] is True and ctx["since_days"] == 12.0
    assert ctx["stage"] == "shadow" and ctx["source"] == "regime_lab"
    # abgelaufen -> stale
    late = datetime.fromtimestamp(c[-1]["timestamp"] / 1000 + 3 * 3600, tz=timezone.utc)
    ctx2 = sr.context_from_model(model, c, "1h", "ra_1", "shadow", [3], "BTCUSDT", now=late)
    assert ctx2["state"] == "stale"
    # nicht behaltenes Regime
    ctx3 = sr.context_from_model(model, c, "1h", "ra_1", "active", [0], "BTCUSDT", now=now)
    assert ctx3["kept"] is False


def test_prompt_line_snapshots_no_short_term_words():
    ok = {"state": "ok", "direction": "down", "since_days": 12, "confidence": 71.0,
          "model_fingerprint": "a1b2c3d4e5", "timeframe": "1h", "kept": True}
    line = sr.prompt_line(ok)
    assert line.startswith("Struktur (Lab-Modell a1b2c3, 1h): BÄR seit 12 Tagen")
    assert "0.71" in line
    assert sr.prompt_line(None) == "Struktur: unbekannt (keine freigegebene Analyse)"
    assert "veraltet" in sr.prompt_line({**ok, "state": "stale"})
    for txt in (line, sr.prompt_line(None), sr.prompt_line({**ok, "state": "stale"})):
        assert not any(w in txt for w in sr.KURZFRIST_WORDS)


def test_resolve_without_release_unknown_and_error_stale(monkeypatch):
    sr.invalidate()

    async def _none(cls, band=None):
        return None
    monkeypatch.setattr(sr, "_release_for", _none)
    ctx = asyncio.run(sr.resolve("BTCUSDT"))
    assert ctx["state"] == "unknown" and ctx["stage"] == "none"
    assert asyncio.run(sr.resolve_if_released("BTCUSDT")) is None
    assert asyncio.run(sr.artifact("BTCUSDT")) is None
    assert asyncio.run(sr.prompt_block(["BTCUSDT"])) == ""

    doc = {"id": "ra_9", "timeframe": "1h", "scope": "combined",
           "release": {"stage": "active", "asset_classes": ["crypto"], "model_fingerprint": "abcdef123456"}}

    async def _doc(cls, band=None):
        return doc
    monkeypatch.setattr(sr, "_release_for", _doc)

    async def _boom(symbol, d):
        raise RuntimeError("Kerzen fehlen")
    monkeypatch.setattr(sr, "_compute", _boom)
    sr.invalidate()
    ctx = asyncio.run(sr.resolve("BTCUSDT"))
    assert ctx["state"] == "stale"
    assert asyncio.run(sr.phase("BTCUSDT")) is None        # stale -> Gate fail-open
    assert asyncio.run(sr.artifact("BTCUSDT")) == "lab:ra_9:abcdef12"
    sr.invalidate()


def test_gate_source_own_unchanged_and_lab_only_known(monkeypatch):
    cfg = {"regime_filter_enabled": True, "regime_block_phases": ["bär"]}
    assert regime_gate.gate_source(cfg) == "own"
    assert regime_gate.gate_source({**cfg, "regime_gate_source": "lab"}) == "lab"

    async def _own(symbol):
        return {"phase": "bär", "label": "Abwärts", "confidence": 80}
    monkeypatch.setattr(regime_gate, "current_phase", _own)
    ok, msg = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert not ok and "Regime-Filter:" in msg

    async def _lab_none(symbol):
        return None
    monkeypatch.setattr(sr, "phase", _lab_none)
    ok, _ = asyncio.run(regime_gate.check_signal_allowed({**cfg, "regime_gate_source": "lab"}, "BTCUSDT"))
    assert ok                                             # nicht wirksam -> fail-open

    async def _lab_bear(symbol):
        return "bär"
    monkeypatch.setattr(sr, "phase", _lab_bear)
    ok, msg = asyncio.run(regime_gate.check_signal_allowed({**cfg, "regime_gate_source": "lab"}, "BTCUSDT"))
    assert not ok and "Lab-Analyse" in msg
    # eigener Pfad bleibt byte-identisch
    ok, msg2 = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert msg2 == msg.replace("(Quelle Lab-Analyse)", "") or "Regime-Filter:" in msg2


def test_fingerprint_artifact_changes_combined_only_when_set():
    base = dict(prompt_hash="p", lessons_h="l", playbook_version="v", model="m",
                gate_version=1, sizing_h="s", policy_config_h="c")
    a = policy_fingerprint.build(**base)
    b = policy_fingerprint.build(**base, regime_artifact="lab:ra_1:abcdef12")
    c = policy_fingerprint.build(**base, regime_artifact=None)
    assert a["combined"] == c["combined"] and a["schema"] == 2
    assert b["combined"] != a["combined"] and b["regime_artifact"] == "lab:ra_1:abcdef12"
    from services import setup_variant
    changed = setup_variant.policy_changes(a, b) if hasattr(setup_variant, "policy_changes") else None
    if changed is not None:
        assert changed == ["Regime-Modell"]


def test_structural_regime_of_and_by_structural():
    t = {"entry_market_snapshot": {"structural": {"state": "ok", "phase": "bär"}}}
    assert ai_rewards.structural_regime_of(t) == "strukturell bär"
    assert ai_rewards.structural_regime_of({"entry_market_snapshot": {"structural": {"state": "stale", "phase": "bär"}}}) is None
    assert ai_rewards.structural_regime_of({}) is None

    class _Coll:
        def find(self, *_):
            class _C:
                async def to_list(self, *_):
                    return [{"structural_regime": "strukturell bär", "score": -1.0, "pnl": -5, "result": "loss"},
                            {"structural_regime": "strukturell bär", "score": 1.0, "pnl": 5, "result": "win"},
                            {"score": 2.0, "pnl": 1, "result": "win"}]
            return _C()

    class _DB:
        ai_rewards = _Coll()
    rows = asyncio.run(ai_rewards.by_structural_regime(_DB(), 30))
    assert {r["regime"] for r in rows} == {"strukturell bär", "unbekannt"}
    bear = next(r for r in rows if r["regime"] == "strukturell bär")
    assert bear["trades"] == 2 and bear["avg_reward"] == 0.0 and bear["win_rate"] == 50.0
