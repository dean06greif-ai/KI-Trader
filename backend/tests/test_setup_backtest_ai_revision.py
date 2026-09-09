"""Regressionstests Setup-Backtest: KI-Revision (sanitize/param_ranges),
Modi (single/loop/ai_loop), KI-Optionen und Automatik-Schema."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.setup_backtest import auto as seed_auto  # noqa: E402
from services.setup_backtest import detectors, revise, runner  # noqa: E402


def test_param_ranges_span_all_variants():
    r = revise.param_ranges("breakout")
    assert set(r) == {"lookback", "range_atr", "sl_atr", "tp_r", "vol_mult"}
    lo, hi = r["lookback"]
    assert lo == 24 * revise.RANGE_LO and hi == 48 * revise.RANGE_HI
    assert "name" not in r


def test_sanitize_clamps_and_requires_change():
    base = dict(detectors.VARIANTS["breakout"][0])
    # Unveränderte Parameter -> kein Vorschlag
    assert revise.sanitize("breakout", {"sl_atr": base["sl_atr"]}, base, 1) is None
    # Unbekannte Schlüssel ignoriert, Extremwerte geklemmt, Ganzzahl bleibt Ganzzahl
    out = revise.sanitize("breakout", {"sl_atr": 99, "lookback": 7.6, "foo": 1, "tp_r": "abc"}, base, 2)
    assert out is not None
    assert out["sl_atr"] == revise.param_ranges("breakout")["sl_atr"][1]
    assert out["lookback"] == 12 and isinstance(out["lookback"], int)
    assert out["tp_r"] == base["tp_r"]
    assert "foo" not in out and out["name"] == "KI-Rev.2"
    assert revise.sanitize("breakout", None, base, 1) is None
    assert revise.sanitize("breakout", "kein dict", base, 1) is None


def test_sanitized_params_run_through_detector_interface():
    """Der geprüfte Satz muss alle Schlüssel enthalten, die der Detektor braucht."""
    base = dict(detectors.VARIANTS["mean_reversion"][1])
    out = revise.sanitize("mean_reversion", {"rsi_lo": 22}, base, 1)
    assert set(out) - {"name"} == set(base) - {"name"}


def test_build_prompt_contains_history_and_ranges():
    hist = [{"name": "standard", "is": {"trades": 12, "winrate": 40, "pnl": -3.2},
             "oos": {"trades": 5, "winrate": 60, "pnl": 1.1}, "passed": False}]
    p = revise.build_prompt("crypto", "breakout", "Breakout-Regel", detectors.VARIANTS["breakout"][0],
                            hist, runner.rules_for_prompt())
    assert "standard: IS 12T/WR 40%/-3.20" in p
    assert "sl_atr:" in p and "Krypto" in p and "Breakout-Regel" in p


def test_modes_and_ai_options():
    assert runner.normalize_mode("ai_loop") == "ai_loop"
    assert runner.normalize_mode("loop") == "loop"
    assert runner.normalize_mode("xyz") == "single" and runner.normalize_mode(None) == "single"
    d = runner.ai_options(None)
    assert d == {"ai_revise": True, "ai_rounds": revise.DEFAULT_ROUNDS,
                 "target_passed": revise.DEFAULT_TARGET}
    o = runner.ai_options({"ai_revise": False, "ai_rounds": 999, "target_passed": "abc"})
    assert o["ai_revise"] is False and o["ai_rounds"] == revise.MAX_ROUNDS
    assert o["target_passed"] == revise.DEFAULT_TARGET
    assert runner.ai_options({"ai_rounds": 0})["ai_rounds"] == 1


def test_auto_normalize_accepts_ai_loop_and_options():
    cfg = seed_auto.normalize({"mode": "ai_loop", "ai_rounds": 50, "target_passed": 2, "ai_revise": False})
    assert cfg["mode"] == "ai_loop" and cfg["ai_rounds"] == 10 and cfg["target_passed"] == 2
    assert cfg["ai_revise"] is False
    assert seed_auto.normalize({"mode": "kaputt"})["mode"] == "loop"
    assert seed_auto.normalize({})["ai_revise"] is True
    assert set(seed_auto.SAVE_KEYS) >= {"mode", "ai_rounds", "target_passed", "ai_revise"}


def test_legacy_state_without_ai_fields_still_valid():
    """Alt-Stände (vor KI-Revision) dürfen nichts brechen: next_variant + kein ai_proposal."""
    assert runner.next_variant(0, 3) == {"variant": 1, "status": "failed"}
    assert runner.next_variant(2, 3) == {"variant": 0, "status": "exhausted"}
    entry = {"variant": 1, "status": "failed", "history": []}
    assert not isinstance(entry.get("ai_proposal"), dict)
    assert runner.MODE_LABELS["ai_loop"] == "KI-Schleife"


class _FakeColl:
    async def find_one(self, *a, **k):
        return {}

    async def update_one(self, *a, **k):
        return None

    async def insert_one(self, *a, **k):
        return None


class _FakeDb:
    settings = _FakeColl()
    ai_chat = _FakeColl()


def test_propose_uses_llm_and_sanitizes(monkeypatch):
    """LLM-Antwort (gemockt) -> geprüfter Parameter-Satz + Meta; ohne Key -> None."""
    import asyncio
    from services import ai_engine as eng_mod

    async def fake_generate(role, prompt, system, temperature=0.4, json_mode=True):
        assert role == revise.ROLE and "breakout" in prompt
        return ('{"params": {"sl_atr": 1.6, "vol_mult": 1.8, "unknown": 5}, '
                '"desc": "Breakout nur mit Volumen > 1.8x und weiterem Stop hinter dem Level", '
                '"reason": "SL zu eng, zu viele Fehlausbrüche"}', "openrouter", "test-model")

    monkeypatch.setattr(type(eng_mod.ai_engine), "key", property(lambda self: "x"))
    monkeypatch.setattr(eng_mod.ai_engine, "generate_for_role", fake_generate, raising=False)
    entry = {"variant": 0, "status": "exhausted"}
    hist = [{"variant": 0, "name": "standard", "is": {"trades": 20, "winrate": 40, "pnl": -5},
             "oos": {"trades": 10, "winrate": 45, "pnl": -2}, "passed": False}]
    out = asyncio.run(revise.propose(_FakeDb(), "crypto", "breakout", entry, hist,
                                     runner.rules_for_prompt(), 1))
    assert out is not None
    assert out["params"]["sl_atr"] == 1.6 and out["params"]["vol_mult"] == 1.8
    assert "unknown" not in out["params"] and out["params"]["name"] == "KI-Rev.1"
    assert out["model"] == "test-model" and out["version"] == 1
    assert out["reason"].startswith("SL zu eng")
    # Live-Revision wurde versucht (Setup nicht rückgestuft -> abgelehnt, kein Fehler)
    assert out.get("live_revision") == "rejected"

    monkeypatch.setattr(type(eng_mod.ai_engine), "key", property(lambda self: None))
    assert asyncio.run(revise.propose(_FakeDb(), "crypto", "breakout", entry, hist,
                                      runner.rules_for_prompt(), 2)) is None
