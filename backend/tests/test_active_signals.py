"""Regressionstests: aktive Signale je Asset (mehrere Setups, Modus, Setup)."""
from datetime import datetime, timedelta, timezone

import pytest

from services import active_signals as asg

pytestmark = pytest.mark.unit
NOW = datetime(2026, 9, 25, 9, 0, tzinfo=timezone.utc)


def _sig(i, minutes_ago, **kw):
    return {"id": f"s{i}", "symbol": "BTCUSDT", "type": "SHORT", "strategy_id": "ai_trader",
            "timestamp": (NOW - timedelta(minutes=minutes_ago)).isoformat(), **kw}


LIB = {"vwap_reclaim": "VWAP-Reclaim: Rückeroberung des VWAP …", "fomc_event": "FOMC-Event: …"}


def test_multiple_open_trades_are_all_active_with_setup_and_mode():
    sigs = [_sig(1, 300, trade_id="t1", ai_setup="vwap_reclaim"),
            _sig(2, 30, trade_id="t2", trade_setup="fomc_event", type="LONG")]
    trades = {"t1": {"id": "t1", "status": "open", "mode": "live"},
              "t2": {"id": "t2", "status": "open", "mode": "paper", "data_collection": True}}
    out = asg.select_active(sigs, trades, True, now=NOW, library=LIB)
    assert [o["id"] for o in out] == ["s1", "s2"]
    assert out[0]["trade_mode"] == "live" and out[0]["setup"] == "vwap_reclaim"
    assert out[0]["setup_label"] == "VWAP-Reclaim"
    assert out[1]["trade_mode"] == "collection" and out[1]["setup"] == "fomc_event"


def test_closed_trade_and_stale_or_evaluated_signals_are_not_active():
    sigs = [_sig(1, 10, trade_id="t1"), _sig(2, 200), _sig(3, 5, status="closed", result="win"),
            _sig(4, 5, signal_class="PRE_SIGNAL")]
    trades = {"t1": {"id": "t1", "status": "closed", "mode": "live"}}
    assert asg.select_active(sigs, trades, False, now=NOW) == []


def test_untraded_fresh_signal_only_in_every_signal_mode():
    sigs = [_sig(1, 10, trade_reject_reason="Risikobudget: Rest-Budget 0.00")]
    assert asg.select_active(sigs, {}, True, now=NOW) == []
    out = asg.select_active(sigs, {}, False, now=NOW)
    assert out and out[0]["active_state"] == "no_trade" and out[0]["trade_mode"] is None
