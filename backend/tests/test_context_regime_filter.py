"""Kurzfrist-Regime-Filter (services/context_regime_filter.py + entry_guard) – Regressionstests."""
import asyncio

from services import context_regime_filter as crf
from services import entry_guard


def test_labels_and_normalize():
    assert len(crf.LABELS) == 15 and "trend_up_volatil" in crf.LABELS
    assert crf.normalize(["trend_up_volatil", "quatsch"]) == ["trend_up_volatil"]
    assert crf.normalize("trend_up_volatil") == []


def test_check():
    cfg = {"context_regime_block": ["trend_up_volatil"]}
    assert crf.check(cfg, "trend_up_volatil")[0] is False
    assert crf.check(cfg, "trend_down_volatil")[0] is True
    assert crf.check(cfg, None)[0] is True          # fail-open
    assert crf.check({}, "trend_up_volatil")[0] is True


def test_entry_guard_blocks_with_regime(monkeypatch):
    async def ok(*a, **k):
        return True, ""
    from services import trade_guard
    monkeypatch.setattr(trade_guard, "check_open_allowed", ok)
    monkeypatch.setattr(crf, "current_regime", lambda sym, candles=None: "trend_up_volatil")
    checks = entry_guard.EntryChecks()
    cfg = {"context_regime_block": ["trend_up_volatil"]}
    allowed, why = asyncio.run(entry_guard.check_entry(None, {"symbol": "BTCUSDT"}, cfg, "paper", "5m", checks))
    assert allowed is False and "Kurzfrist-Regime" in why
    # Sammel-Trades und leere Liste: unverändert
    allowed, _ = asyncio.run(entry_guard.check_entry(None, {"symbol": "BTCUSDT"}, cfg, "paper", "5m",
                                                     entry_guard.EntryChecks(), collection=True))
    assert allowed is True
    allowed, _ = asyncio.run(entry_guard.check_entry(None, {"symbol": "BTCUSDT"}, {}, "paper", "5m",
                                                     entry_guard.EntryChecks()))
    assert allowed is True


def test_default_coin_cfg_has_empty_block():
    from services.bitunix_trade import DEFAULT_COIN_CFG
    assert DEFAULT_COIN_CFG["context_regime_block"] == []
