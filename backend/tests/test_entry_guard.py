"""Audit 2.1: Zentraler Entry-Guard (services/entry_guard.py). Ohne Netzwerk."""
import asyncio
import inspect

from services import entry_guard
from services.bitunix_trade import AutoTradeManager


def _sig(**kw):
    return {"symbol": "BTCUSDT", "type": "LONG", **kw}


def test_entry_checks_record_and_to_list():
    c = entry_guard.EntryChecks()
    assert c.record("a") is True
    assert c.record("b", False, "x" * 500, mode="live", skipme=None) is False
    rows = c.to_list()
    assert rows[0] == {"name": "a", "ok": True}
    assert rows[1]["ok"] is False and len(rows[1]["reason"]) == entry_guard.REASON_MAX_LEN
    assert rows[1]["mode"] == "live" and "skipme" not in rows[1]
    rows[0]["name"] = "mutiert"  # to_list liefert Kopien
    assert c.to_list()[0]["name"] == "a"


def test_check_entry_blocks_when_trade_guard_blocks(monkeypatch):
    from services import trade_guard

    async def fake_guard(db, signal, tf, mode=None):
        return False, "Kill-Switch (live) aktiv"

    monkeypatch.setattr(trade_guard, "check_open_allowed", fake_guard)
    checks = entry_guard.EntryChecks()
    ok, why = asyncio.run(entry_guard.check_entry(None, _sig(), {}, "live", "1m", checks))
    assert not ok and "Kill-Switch" in why
    assert checks.to_list() == [{"name": "trade_guard", "ok": False,
                                 "reason": "Kill-Switch (live) aktiv", "mode": "live"}]


def test_check_entry_regime_gate_blocks_and_collection_skips(monkeypatch):
    from services import regime_gate, trade_guard

    async def ok_guard(db, signal, tf, mode=None):
        return True, ""

    async def block_regime(cfg, symbol):
        return False, "Regime: Seitwärtsmarkt"

    monkeypatch.setattr(trade_guard, "check_open_allowed", ok_guard)
    monkeypatch.setattr(regime_gate, "check_signal_allowed", block_regime)
    cfg = {"regime_filter_enabled": True}

    checks = entry_guard.EntryChecks()
    ok, why = asyncio.run(entry_guard.check_entry(None, _sig(), cfg, "paper", "1m", checks))
    assert not ok and "Regime" in why
    assert [r["name"] for r in checks.to_list()] == ["trade_guard", "regime_gate"]

    # Sammel-Trades: Regime-Gate wird wie bisher übersprungen
    checks = entry_guard.EntryChecks()
    ok, _ = asyncio.run(entry_guard.check_entry(None, _sig(), cfg, "paper", "1m",
                                                checks, collection=True))
    assert ok and [r["name"] for r in checks.to_list()] == ["trade_guard"]


def test_check_risk_budget_records_block_and_fail_open(monkeypatch):
    from services import risk_budget

    async def block(db, mode, symbol, new_risk, equity):
        return False, "Risikobudget: zu viel offenes Risiko"

    monkeypatch.setattr(risk_budget, "check_new_trade", block)
    checks = entry_guard.EntryChecks()
    ok, why = asyncio.run(entry_guard.check_risk_budget(
        None, "live", "BTCUSDT", 12.5, 1000.0, checks))
    assert not ok and "Risikobudget" in why
    row = checks.to_list()[0]
    assert row["name"] == "risk_budget" and row["ok"] is False
    assert row["new_risk_usdt"] == 12.5

    async def boom(db, mode, symbol, new_risk, equity):
        raise RuntimeError("db down")

    monkeypatch.setattr(risk_budget, "check_new_trade", boom)
    checks = entry_guard.EntryChecks()
    ok, why = asyncio.run(entry_guard.check_risk_budget(
        None, "live", "BTCUSDT", 12.5, 1000.0, checks))
    assert ok and why == ""  # fail-open
    row = checks.to_list()[0]
    assert row["ok"] is True and "fail-open" in row["reason"]


def test_wiring_in_on_signal_and_trade_doc():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    assert "entry_guard.EntryChecks()" in src
    assert "entry_guard.check_entry(" in src
    assert "entry_guard.check_risk_budget(" in src
    assert 'checks.record("fee_guard"' in src
    assert 'checks.record("low_vol_atr_block"' in src
    assert 'checks.record("capital_limit"' in src
    assert '"entry_checks": checks.to_list()' in src
    # Bausteine bleiben erhalten: entry_guard ruft trade_guard/risk_budget auf
    assert "trade_guard.check_open_allowed" in inspect.getsource(entry_guard.check_entry)
    assert "risk_budget.check_new_trade" in inspect.getsource(entry_guard.check_risk_budget)
