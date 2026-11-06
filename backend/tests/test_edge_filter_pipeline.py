"""Edge-Filter im Einstiegspfad (AutoTradeManager._edge_filter_step) – ohne Netzwerk/DB."""
import asyncio

from services import entry_guard
from services.bitunix_trade import AutoTradeManager


class _Stub:
    db = None


def _run(mode, collection=False, ai_cfg=None, bypass=None, side="LONG"):
    sig = {"symbol": "BTCUSDT", "strategy_id": "ai_trader", "type": side}
    checks = entry_guard.EntryChecks()
    cfg = {"max_capital": 100, "leverage": 10, "fee_percent": 0.06}
    blocked = asyncio.run(AutoTradeManager._edge_filter_step(
        _Stub(), sig, [], ai_cfg or {}, cfg, "BTCUSDT", side, 100.0, 99.5, mode,
        collection, set(bypass or []), checks))
    return blocked, sig, checks.to_list()


def test_live_blocks_and_records_reason():
    blocked, sig, checks = _run("live")
    assert blocked is True
    assert sig["_reject_reason"].startswith("Edge-Filter:")
    assert checks[-1]["name"] == "edge_filter" and checks[-1]["ok"] is False


def test_paper_only_marks():
    blocked, sig, checks = _run("paper")
    assert blocked is False
    assert sig["edge_filter"]["ok"] is False
    assert "nur markiert" in checks[-1]["reason"]


def test_collection_never_blocked_even_live():
    blocked, sig, _ = _run("live", collection=True)
    assert blocked is False and "edge_filter" in sig


def test_disabled_filter_is_backward_compatible():
    blocked, sig, _ = _run("live", ai_cfg={"edge_filter_enabled": False})
    assert blocked is False


def test_shadow_bypass_skips_block():
    blocked, _, checks = _run("live", bypass=["edge_filter"])
    assert blocked is False and "übersprungen" in checks[-1]["reason"]


def test_pass_when_wide_sl_and_no_bias_requirement():
    cfg = {"edge_filter_require_bias": False}
    sig = {"symbol": "BTCUSDT"}
    checks = entry_guard.EntryChecks()
    blocked = asyncio.run(AutoTradeManager._edge_filter_step(
        _Stub(), sig, [], cfg, {"max_capital": 100, "leverage": 5}, "BTCUSDT", "LONG",
        100.0, 98.0, "live", False, set(), checks))
    assert blocked is False and sig["edge_filter"]["ok"] is True
