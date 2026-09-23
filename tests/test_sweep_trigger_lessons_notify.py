"""Tests: Sweep-Trigger (Detektor, Budget/Cooldown, run_analysis-Kopplung),
Lektions-Qualitätsguards (Größen-Anweisungen, Mini-Stichproben, Prompt-Hinweis),
KI-Warnung nur für Team-Modelle (Bug 02.09.: nemotron-3.5-lightning aus der
Provider-Ersatzkette löste Website-Warnungen aus). Ohne Netzwerk."""
import asyncio
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


def _flat(n=120, p=100.0, ts0=1_700_000_000_000):
    out = []
    for i in range(n):
        o = p + (0.05 if i % 2 else -0.05)
        c = p + (-0.05 if i % 2 else 0.05)
        out.append({"timestamp": ts0 + i * 60_000, "open": o, "high": max(o, c) + 0.1,
                    "low": min(o, c) - 0.1, "close": c, "volume": 10})
    return out


# ------------------------------------------------------------------ Detektor
def test_detect_sweep_short_and_long():
    from services import sweep_trigger as st
    cs = _flat()
    atr = st._atr(cs[:-1])
    assert 0.2 <= atr <= 0.35
    ref_high = max(c["high"] for c in cs[-61:-1])
    # Bearish Sweep: Docht 2 ATR über das Referenz-Hoch, Close darunter, untere Hälfte
    cs[-1] = {**cs[-1], "open": 100.0, "high": ref_high + 2 * atr, "low": 99.9, "close": 99.95}
    hit = st.detect_sweep(cs, lookback=60, min_wick_atr=1.2)
    assert hit and hit["side"] == "SHORT" and hit["level"] == round(ref_high, 8)
    assert hit["wick_atr"] >= 1.9
    # Kein Reclaim (Close über dem Level) -> kein Sweep (echter Breakout)
    cs[-1] = {**cs[-1], "close": ref_high + 1.5 * atr, "open": ref_high + 1.6 * atr}
    assert st.detect_sweep(cs) is None
    # Docht zu kurz -> None
    cs[-1] = {**cs[-1], "open": 100.0, "high": ref_high + 0.5 * atr, "close": 99.95}
    assert st.detect_sweep(cs) is None
    # Bullish Sweep
    ref_low = min(c["low"] for c in cs[-61:-1])
    cs[-1] = {**cs[-1], "open": 100.0, "high": 100.1, "low": ref_low - 2 * atr, "close": 100.05}
    hit = st.detect_sweep(cs)
    assert hit and hit["side"] == "LONG" and hit["level"] == round(ref_low, 8)
    # zu wenig Daten
    assert st.detect_sweep(cs[:50]) is None


def test_budget_and_cooldown():
    from services import sweep_trigger as st
    b = st.SweepBudget()
    cfg = {"sweep_trigger_enabled": True, "sweep_trigger_daily_cap": 2,
           "sweep_trigger_cooldown_min": 30}
    now = 1_800_000_000.0
    assert b.allowed("BTCUSDT", cfg, now) is None
    b.fire("BTCUSDT", {"side": "SHORT"}, now)
    assert "Cooldown" in b.allowed("BTCUSDT", cfg, now + 60)
    assert b.allowed("ETHUSDT", cfg, now + 60) is None
    b.fire("ETHUSDT", {"side": "LONG"}, now + 60)
    assert "Tagesbudget" in b.allowed("SOLUSDT", cfg, now + 120)
    # neuer Tag -> Budget zurück, Cooldown BTC abgelaufen
    assert b.allowed("BTCUSDT", cfg, now + 86_400) is None
    assert b.allowed("X", {**cfg, "sweep_trigger_enabled": False}, now) == "Sweep-Trigger aus"
    s = b.status(cfg)
    assert s["daily_cap"] == 2 and len(s["recent"]) == 2 and s["recent"][0]["symbol"] == "ETHUSDT"


def test_clamps_and_defaults_in_engine():
    from services import sweep_trigger as st
    from services.ai_engine import DEFAULT_AI_CONFIG
    assert DEFAULT_AI_CONFIG["sweep_trigger_enabled"] is True
    assert DEFAULT_AI_CONFIG["sweep_trigger_daily_cap"] == 20
    cfg = dict(st.DEFAULTS)
    st.clamp_updates({"sweep_trigger_daily_cap": 999, "sweep_trigger_cooldown_min": 0,
                      "sweep_trigger_min_wick_atr": "x", "sweep_trigger_enabled": 0}, cfg)
    assert cfg["sweep_trigger_daily_cap"] == 200 and cfg["sweep_trigger_cooldown_min"] == 1
    assert cfg["sweep_trigger_min_wick_atr"] == 1.2 and cfg["sweep_trigger_enabled"] is False


def test_check_fires_targeted_analysis_once_per_candle():
    """check(): Sweep -> run_analysis(only_symbols=[sym], trigger=...) genau einmal
    pro Kerze; zweiter Sweep desselben Symbols im Cooldown feuert nicht."""
    from services import sweep_trigger as st
    st.budget = st.SweepBudget()
    st._last_seen_ts.clear()
    cs = _flat()
    atr = st._atr(cs[:-1])
    ref_high = max(c["high"] for c in cs[-61:-1])
    cs[-1] = {**cs[-1], "open": 100.0, "high": ref_high + 2 * atr, "low": 99.9, "close": 99.95}
    calls = []

    async def run_analysis(manual=False, only_symbols=None, trigger=None):
        calls.append((only_symbols, trigger))
        return {"status": "ok"}

    eng = SimpleNamespace(
        config={**st.DEFAULTS}, _analyzing=False, symbols=["BTCUSDT", "ETHUSDT"],
        toggle_check=None, run_analysis=run_analysis,
        scanner=SimpleNamespace(candle_buffer={"BTCUSDT": cs, "ETHUSDT": _flat()},
                                is_trading_session=lambda sid: True))
    res = asyncio.run(st.check(eng))
    assert res == {"status": "ok"} and len(calls) == 1
    assert calls[0][0] == ["BTCUSDT"] and "WICK-SWEEP BTCUSDT" in calls[0][1]
    # dieselbe Kerze erneut -> kein zweiter Call
    assert asyncio.run(st.check(eng)) is None and len(calls) == 1
    # neue Kerze mit Sweep, aber Cooldown -> kein Call
    cs.append({**cs[-1], "timestamp": cs[-1]["timestamp"] + 60_000})
    assert asyncio.run(st.check(eng)) is None and len(calls) == 1
    # laufende Analyse blockt
    st.budget.last_fire.clear()
    cs.append({**cs[-1], "timestamp": cs[-1]["timestamp"] + 60_000})
    eng._analyzing = True
    assert asyncio.run(st.check(eng)) is None
    # außerhalb der Session blockt
    eng._analyzing = False
    eng.scanner.is_trading_session = lambda sid: False
    assert asyncio.run(st.check(eng)) is None and len(calls) == 1


def test_run_analysis_signature_accepts_targeting():
    import inspect
    from services.ai_engine import AIEngine
    sig = inspect.signature(AIEngine.run_analysis)
    assert "only_symbols" in sig.parameters and "trigger" in sig.parameters


# ------------------------------------------------------------------ Lektionen
def test_lesson_quality_guards():
    from services import ai_lessons as L
    prod = [  # echte Prod-Lektionen (02.09.) – Größen-Anweisungen müssen erkannt werden
        ("Live-Trades defensiver behandeln als Paper-Trades",
         "bei Konfidenz <75% nicht mehr als 20 % der Marge einsetzen"),
        ("Bei Konfidenz <70% zusätzliche Bestätigung verlangen",
         "zwei unabhängige Bestätigungen verlangen und die Marge um 25% reduzieren."),
        ("Positionsgröße", "capital_pct auf 30 % begrenzen bei Non-Crypto"),
    ]
    for t, d in prod:
        assert L.is_sizing_rule(t, d), t
    keep = [
        ("SHORT nur bei bestätigtem Abwärtstrend", "SHORT-Signale 32 % Winrate (327 Signale)"),
        ("Bestätigungskerze vor Einstieg abwarten", "keine Position auf einzelne Abweisung"),
        ("Stop-Loss-Abstand zu eng – 1.5 ATR nötig", "SL mindestens 1,5× ATR(14)"),
    ]
    for t, d in keep:
        assert not L.is_sizing_rule(t, d), t
    assert L.small_sample_n("Konfidenz >=80% kritisch hinterfragen",
                            "5 entschiedene Trades mit Konfidenz >=80% zeigen 0% Winrate.") == 5
    assert L.small_sample_n("x", "374 entschiedene Trades mit Konfidenz <70%") is None
    assert L.small_sample_n("x", "3 Trades, aber 120 Signale insgesamt") is None
    assert L.small_sample_n("x", "ohne Zahlen") is None
    lessons = [{"id": "a", "title": prod[1][0], "detail": prod[1][1], "weight": 2},
               {"id": "b", "title": keep[0][0], "detail": keep[0][1], "weight": 2}]
    txt = L.lessons_text([dict(x) for x in lessons], risk_sizing=True)
    assert "gegenstandslos" in txt and txt.count("gegenstandslos") == 1
    assert "gegenstandslos" not in L.lessons_text([dict(x) for x in lessons])


# ------------------------------------------------------------------ KI-Warnung nur Team-Modelle
def test_notify_only_team_models():
    from services import notifications as nf
    from services.ai_roles import role_manager
    from services.ai_engine import ai_engine
    role_manager.config["analyst"] = {
        "enabled": True, "provider": "openrouter",
        "model": "nvidia/nemotron-3-super-120b-a12b:free",
        "fallback_provider": "groq", "fallback_model": "openai/gpt-oss-120b",
        "fallback2_provider": "openrouter", "fallback2_model": "deepseek/deepseek-v4-flash"}
    try:
        assert nf._is_team_model("analyst", "openrouter", "nvidia/nemotron-3-super-120b-a12b:free")
        assert nf._is_team_model("analyst", "groq", "openai/gpt-oss-120b")
        assert not nf._is_team_model("analyst", "openrouter", "nvidia/nemotron-3.5-lightning:free")
        assert not nf._is_team_model("analyst", "openrouter", "google/gemma-4-31b-it:free")
        assert ai_engine is not None
    finally:
        role_manager.config.pop("analyst", None)

    async def run():
        from core import state
        from motor.motor_asyncio import AsyncIOMotorClient
        import os
        client = AsyncIOMotorClient(os.environ["MONGO_URL"], serverSelectionTimeoutMS=5000)
        db = client[os.environ["DB_NAME"] + "_test_notify_team"]
        await client.drop_database(db.name)
        old_db = state.db
        state.db = db
        role_manager.config["analyst"] = {"enabled": True, "provider": "openrouter",
                                          "model": "nvidia/nemotron-3-super-120b-a12b:free"}
        nf._pending_fail.clear()
        try:
            await nf.notify_model_failure("analyst", "openrouter",
                                          "nvidia/nemotron-3.5-lightning:free", "error", "leer")
            assert nf._pending_fail == [], "Ersatzmodell darf keine Warnung erzeugen"
            await nf.notify_model_failure("analyst", "openrouter",
                                          "nvidia/nemotron-3-super-120b-a12b:free", "error", "leer")
            assert len(nf._pending_fail) == 1, "Team-Modell muss weiterhin melden"
        finally:
            nf._pending_fail.clear()
            role_manager.config.pop("analyst", None)
            state.db = old_db
            await client.drop_database(db.name)
    asyncio.run(run())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            print(f"- {name}")
            fn()
    print("ALLE TESTS GRÜN")
