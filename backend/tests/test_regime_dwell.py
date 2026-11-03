"""Regime-Haltedauer im Live-Betrieb (services/regime_dwell.py) – reine Regressionstests."""
from datetime import datetime, timedelta, timezone

from services import regime_dwell as rd

T0 = datetime(2026, 11, 3, 12, tzinfo=timezone.utc)
S = {"min_hold_days": 1, "runtime_confirm_refreshes": 2}


def _st(r):
    return {"regime": r, "label": f"R{r}", "active_config": {"r": r}}


def test_first_state_is_accepted():
    eff, sw = rd.decide(None, _st(1), S, T0)
    assert eff["regime"] == 1 and not sw and eff["since"] == T0.isoformat()


def test_switch_held_during_min_hold():
    prev = {**_st(1), "since": T0.isoformat()}
    eff, sw = rd.decide(prev, _st(4), S, T0 + timedelta(hours=2))
    assert not sw and eff["regime"] == 1 and eff["active_config"] == {"r": 1}
    assert eff["pending"]["regime"] == 4 and "Haltedauer" in eff["held"]


def test_switch_needs_confirmation_after_hold():
    prev = {**_st(1), "since": T0.isoformat()}
    later = T0 + timedelta(hours=30)
    eff, sw = rd.decide(prev, _st(4), S, later)
    assert not sw and eff["pending"]["count"] == 1
    eff2, sw2 = rd.decide(eff, _st(4), S, later + timedelta(hours=1))
    assert sw2 and eff2["regime"] == 4 and "pending" not in eff2


def test_ping_pong_resets_pending():
    prev = {**_st(1), "since": T0.isoformat(), "pending": {"regime": 4, "count": 1}}
    eff, sw = rd.decide(prev, _st(1), S, T0 + timedelta(hours=40))
    assert not sw and eff["regime"] == 1
    eff, sw = rd.decide(prev, _st(3), S, T0 + timedelta(hours=40))
    assert not sw and eff["pending"] == {"regime": 3, "label": "R3", "count": 1}


def test_disabled_is_old_behaviour():
    prev = {**_st(1), "since": T0.isoformat()}
    eff, sw = rd.decide(prev, _st(4), {**S, "runtime_dwell_enabled": False}, T0 + timedelta(minutes=5))
    assert sw and eff["regime"] == 4
