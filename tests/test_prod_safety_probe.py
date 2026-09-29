"""Unit-Tests für die reinen Kernfunktionen der Prod-Sicherheitsprobe (T3)."""
import os
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/app/scripts")
sys.path.insert(0, "/app/backend")

from prod_safety_probe import guard_state_view, diff_positions

NOW = datetime(2026, 6, 26, 12, 0, tzinfo=timezone.utc)


def _ident(s):
    return s


def test_guard_state_paused_active():
    until = (NOW + timedelta(hours=3)).isoformat()
    v = guard_state_view({"paused_until": until, "reason": "daily_loss"}, now=NOW)
    assert v["paused"] is True
    assert v["paused_until"] == until
    assert v["reason"] == "daily_loss"


def test_guard_state_expired_or_empty():
    past = (NOW - timedelta(hours=1)).isoformat()
    assert guard_state_view({"paused_until": past, "reason": "x"}, now=NOW)["paused"] is False
    v = guard_state_view(None, now=NOW)
    assert v == {"paused": False, "paused_until": None, "reason": None, "learning_required": False}
    assert guard_state_view({"paused_until": "kaputt"}, now=NOW)["paused"] is False


def test_guard_state_learning_required():
    assert guard_state_view({"learning_required": True}, now=NOW)["learning_required"] is True


def test_diff_positions_matched():
    ex = [{"symbol": "BTCUSDT", "qty": "0.5", "side": "BUY"}]
    local = [{"symbol": "BTCUSDT", "id": "t1", "opened_at": "2026-06-26"}]
    d = diff_positions(ex, local, _ident)
    assert d == {"exchange_only": [], "local_only": [], "matched": 1}


def test_diff_positions_orphan_and_ghost():
    ex = [{"symbol": "ETHUSDT", "qty": 2, "side": "SELL"},
          {"symbol": "BTCUSDT", "qty": 0, "side": "BUY"}]  # qty 0 = keine echte Position
    local = [{"symbol": "SOLUSDT", "id": "t9", "opened_at": "2026-06-25T10:00:00"}]
    d = diff_positions(ex, local, _ident)
    assert d["matched"] == 0
    assert [r["symbol"] for r in d["exchange_only"]] == ["ETHUSDT"]
    assert [r["symbol"] for r in d["local_only"]] == ["SOLUSDT"]


def test_diff_positions_symbol_mapping():
    ex = [{"symbol": "XAUUSDT", "qty": 1, "side": "BUY"}]
    local = [{"symbol": "GOLD", "id": "t2", "opened_at": "2026-06-26"}]
    d = diff_positions(ex, local, lambda s: {"GOLD": "XAUUSDT"}.get(s, s))
    assert d["matched"] == 1 and not d["exchange_only"] and not d["local_only"]


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"PASS {name}")
    print("Alle Tests grün.")
