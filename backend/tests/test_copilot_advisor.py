"""Regressionstests: Strategie-Copilot als Berater/Einstellungs-Assistent.

Unit-Tests ohne Netzwerk: Key-Fallback, Vorschlags-Typen, Advisor-Prompt
und die bestehenden Sanity-Checks (Rückwärtskompatibilität)."""
from services import strategy_copilot as sc


def _clear_keys(monkeypatch):
    import os
    for name in list(os.environ):
        if name.startswith(sc.KEY_ENV) or name.startswith(sc.SHARED_KEY_ENV):
            monkeypatch.delenv(name, raising=False)


def test_copilot_keys_prefer_own_keys(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv(sc.KEY_ENV, "cop-key-1")
    monkeypatch.setenv(sc.KEY_ENV + "_BACKUP", "cop-key-2")
    monkeypatch.setenv(sc.SHARED_KEY_ENV, "trader-key")
    assert sc.copilot_keys() == ["cop-key-1", "cop-key-2"]
    assert sc.copilot_key_source() == "copilot"


def test_copilot_keys_fallback_to_trader_keys(monkeypatch):
    _clear_keys(monkeypatch)
    monkeypatch.setenv(sc.SHARED_KEY_ENV, "trader-key")
    monkeypatch.setenv(sc.SHARED_KEY_ENV + "_BACKUP2", "trader-key-b2")
    assert sc.copilot_keys() == ["trader-key", "trader-key-b2"]
    assert sc.copilot_key_source() == "shared"


def test_copilot_keys_none(monkeypatch):
    _clear_keys(monkeypatch)
    assert sc.copilot_keys() == []
    assert sc.copilot_key_source() == "none"


def test_settings_proposal_type_supported():
    assert "settings" in sc.PROPOSAL_TYPES
    # Bestehende Typen bleiben erhalten (Rückwärtskompatibilität)
    assert "definition" in sc.PROPOSAL_TYPES and "params" in sc.PROPOSAL_TYPES


def test_prompt_is_advisor_only():
    p = sc.SYSTEM_PROMPT
    assert "NIEMALS eigenmächtig" in p
    assert '"type": "settings"' in p
    assert "Dynamische Strategie" in p          # Seitwärtsmarkt-Beratung
    assert "regime_filter_enabled" in p         # kennt den Marktphasen-Filter


def test_sanity_check_regressions():
    # unveränderte Kernprüfungen des bestehenden Systems
    notes = sc.sanity_check({"trades": 10, "wins": 6, "losses": 3})
    assert any("inkonsistent" in n for n in notes)
    notes = sc.sanity_check({"trades": 10, "wins": 6, "losses": 4,
                             "win_rate": 60.0, "pnl": 10.0, "avg_pnl": 1.0})
    assert notes == []
    notes = sc.sanity_check({"wins": 7, "losses": 3, "win_rate": 90.0})
    assert any("Win-Rate" in n for n in notes)
    assert sc.sanity_check({}) == []
