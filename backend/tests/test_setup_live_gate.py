"""Regressionstests: Setup-Reife-Gate (Live nur für Setups mit genug Daten).

Gewünschtes Verhalten (User-Anforderung 06/2026): Der KI-Trader macht Live-
Trades nur für Setups, zu denen bereits gute Daten gesammelt wurden
('bewährt'/'neutral' im Playbook). Neue/unreife Setups laufen – auch über der
Live-Konfidenz-Schwelle – zuerst als Paper-Datensammlung weiter.
"""
from services.ai_playbook import (MIN_TRADES_FOR_VERDICT, live_ready,
                                  verdict_for)


def test_new_setup_not_live_ready():
    ok, why = live_ready(None)
    assert not ok and "keine echten Daten" in why
    ok, _ = live_ready({"trades": 0, "wins": 0, "pnl": 0})
    assert not ok


def test_immature_setup_collects_first():
    stats = {"trades": 3, "wins": 2, "pnl": 4.0,
             "verdict": verdict_for(3, 2, 4.0)}
    assert stats["verdict"] == "test"
    ok, why = live_ready(stats)
    assert not ok
    assert f"3/{MIN_TRADES_FOR_VERDICT}" in why


def test_proven_setup_is_live_ready():
    stats = {"trades": 10, "wins": 7, "pnl": 25.0,
             "verdict": verdict_for(10, 7, 25.0)}
    assert stats["verdict"] == "bewährt"
    assert live_ready(stats) == (True, "bewährt")


def test_neutral_setup_is_live_ready():
    stats = {"trades": 8, "wins": 4, "pnl": 1.0,
             "verdict": verdict_for(8, 4, 1.0)}
    assert stats["verdict"] == "neutral"
    assert live_ready(stats)[0] is True


def test_weak_setup_not_live_ready():
    stats = {"trades": 10, "wins": 2, "pnl": -20.0,
             "verdict": verdict_for(10, 2, -20.0)}
    assert stats["verdict"] == "schwach"
    assert live_ready(stats)[0] is False


def test_live_ready_computes_verdict_if_missing():
    assert live_ready({"trades": 10, "wins": 7, "pnl": 25.0})[0] is True
    assert live_ready({"trades": 2, "wins": 2, "pnl": 5.0})[0] is False
