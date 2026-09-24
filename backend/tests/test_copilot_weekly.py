"""Regressionstests: Wöchentlicher Copilot-Report (Fälligkeits-Logik)."""
from datetime import datetime, timedelta, timezone

from services import copilot_weekly as cw


def _berlin(weekday: int, hour: int) -> datetime:
    # 2026-06-22 ist ein Montag (weekday=0)
    base = datetime(2026, 6, 22, hour, 5, tzinfo=cw.BERLIN)
    return base + timedelta(days=weekday)


def test_not_due_when_disabled():
    cfg = {"enabled": False, "weekday": 0, "hour": 9}
    assert not cw.is_due(cfg, _berlin(0, 10), None)


def test_due_on_correct_day_and_hour():
    cfg = {"enabled": True, "weekday": 0, "hour": 9}
    assert cw.is_due(cfg, _berlin(0, 9), None)
    assert cw.is_due(cfg, _berlin(0, 15), None)   # später am Tag auch noch
    assert not cw.is_due(cfg, _berlin(0, 8), None)  # zu früh
    assert not cw.is_due(cfg, _berlin(1, 10), None)  # falscher Tag


def test_min_gap_prevents_double_send():
    cfg = {"enabled": True, "weekday": 0, "hour": 9}
    recent = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    assert not cw.is_due(cfg, _berlin(0, 10), recent)
    old = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    assert cw.is_due(cfg, _berlin(0, 10), old)


def test_invalid_last_sent_is_ignored():
    cfg = {"enabled": True, "weekday": 0, "hour": 9}
    assert cw.is_due(cfg, _berlin(0, 10), "kein-datum")


def test_defaults():
    assert cw.DEFAULTS == {"enabled": False, "weekday": 0, "hour": 9}
    assert cw.MIN_GAP_HOURS >= 48
