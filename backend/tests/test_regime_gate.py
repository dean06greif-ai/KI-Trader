"""Regressionstests: Regime-Gate (Marktphasen-Filter für Auto-Trades).

Unit-Tests ohne Netzwerk – die Regime-Erkennung wird gemockt, geprüft wird
die Gate-Logik (Blockieren/Erlauben, Fail-open, Default-Phasen)."""
import asyncio

from services import regime_gate


def test_phase_from_label_mapping():
    # kmeans-Engine-Labels
    assert regime_gate.phase_from_label("Aufwärtstrend · hohe Volatilität") == "bulle"
    assert regime_gate.phase_from_label("Leicht aufwärts · mittlere Volatilität") == "bulle"
    assert regime_gate.phase_from_label("Abwärtstrend · niedrige Volatilität") == "bär"
    assert regime_gate.phase_from_label("Leicht abwärts · hohe Volatilität") == "bär"
    assert regime_gate.phase_from_label("Seitwärtsmarkt · mittlere Volatilität") == "seitwärts"
    # v2-Engine-Labels (Modus 3/5/9)
    assert regime_gate.phase_from_label("Leichter Aufwärtstrend") == "bulle"
    assert regime_gate.phase_from_label("Starker Abwärtstrend") == "bär"
    assert regime_gate.phase_from_label("Seitwärtsmarkt") == "seitwärts"
    assert regime_gate.phase_from_label(None) is None
    assert regime_gate.phase_from_label("") is None
    assert regime_gate.phase_from_label("Unbekanntes Label") is None


def test_blocked_phases_default_and_validation():
    assert regime_gate.blocked_phases({}) == ["seitwärts"]
    assert regime_gate.blocked_phases({"regime_block_phases": ["bulle", "quatsch"]}) == ["bulle"]
    assert regime_gate.blocked_phases({"regime_block_phases": []}) == []
    assert regime_gate.blocked_phases({"regime_block_phases": ["Seitwärts", "BÄR"]}) \
        == ["seitwärts", "bär"]


def test_gate_disabled_allows(monkeypatch):
    called = {"n": 0}

    async def _boom(sym):
        called["n"] += 1
        raise AssertionError("darf bei deaktiviertem Filter nicht aufgerufen werden")

    monkeypatch.setattr(regime_gate, "current_phase", _boom)
    ok, reason = asyncio.run(regime_gate.check_signal_allowed({}, "BTCUSDT"))
    assert ok and reason == ""
    ok, _ = asyncio.run(regime_gate.check_signal_allowed(
        {"regime_filter_enabled": False}, "BTCUSDT"))
    assert ok
    assert called["n"] == 0


def test_gate_blocks_blocked_phase(monkeypatch):
    async def _fake(sym):
        return {"phase": "seitwärts", "label": "Seitwärtsmarkt · mittlere Volatilität",
                "confidence": 84.2}

    monkeypatch.setattr(regime_gate, "current_phase", _fake)
    cfg = {"regime_filter_enabled": True}  # Default blockiert 'seitwärts'
    ok, reason = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert not ok
    assert "Seitwärtsmarkt" in reason and "blockiert" in reason


def test_gate_allows_other_phase(monkeypatch):
    async def _fake(sym):
        return {"phase": "bulle", "label": "Aufwärtstrend · hohe Volatilität",
                "confidence": 91.0}

    monkeypatch.setattr(regime_gate, "current_phase", _fake)
    cfg = {"regime_filter_enabled": True, "regime_block_phases": ["seitwärts"]}
    ok, reason = asyncio.run(regime_gate.check_signal_allowed(cfg, "ETHUSDT"))
    assert ok and reason == ""


def test_gate_fail_open_on_error(monkeypatch):
    async def _fail(sym):
        raise RuntimeError("Datenquelle nicht erreichbar")

    monkeypatch.setattr(regime_gate, "current_phase", _fail)
    cfg = {"regime_filter_enabled": True}
    ok, reason = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert ok and reason == ""


def test_gate_empty_blocklist_allows(monkeypatch):
    async def _fake(sym):
        return {"phase": "seitwärts", "label": "Seitwärtsmarkt", "confidence": 80}

    monkeypatch.setattr(regime_gate, "current_phase", _fake)
    cfg = {"regime_filter_enabled": True, "regime_block_phases": []}
    ok, _ = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert ok


def test_default_coin_cfg_contains_filter_keys():
    from services.bitunix_trade import DEFAULT_COIN_CFG
    assert DEFAULT_COIN_CFG.get("regime_filter_enabled") is False
    assert DEFAULT_COIN_CFG.get("regime_block_phases") == ["seitwärts"]
