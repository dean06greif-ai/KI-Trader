"""Regression: starke Setup-Änderung -> Anlageklassen-Wert wird neu bewertet.

Deckt Task "Reset bei starker Setup-Änderung" ab:
  * setup_lifecycle.is_strong_change (rein): >=30 % SL/TP/Hebel oder TF-Wechsel
    ist stark, kleinere Tunings NICHT.
  * evolve_versions markiert eine Rollback-Version mit "strong", wenn sich die
    Parameter stark unterscheiden.
"""
from services import setup_lifecycle as lc


def test_is_strong_change_thresholds():
    base = {"sl_pct": 1.0, "tp_ratio": 2.0, "timeframe": "5m"}
    assert lc.is_strong_change({**base, "sl_pct": 1.4}, base) is True      # +40 %
    assert lc.is_strong_change({**base, "tp_ratio": 2.6}, base) is True    # +30 %
    assert lc.is_strong_change({**base, "timeframe": "15m"}, base) is True  # TF-Wechsel
    assert lc.is_strong_change({**base, "max_leverage": 20}, {**base, "max_leverage": 10}) is True
    # Kleine Tunings sind NICHT stark:
    assert lc.is_strong_change({**base, "sl_pct": 1.15}, base) is False    # +15 %
    assert lc.is_strong_change(base, base) is False


def test_tuning_step_never_strong():
    """Das gedeckelte Feintuning (max. ±20 %) darf nie als stark gelten."""
    old = 1.0
    stepped = lc.clamp_step(1.5, old)          # auf +20 % gedeckelt -> 1.2
    assert lc.is_strong_change({"sl_pct": stepped}, {"sl_pct": old}) is False


def test_rollback_version_marked_strong():
    """Rollback auf eine stark abweichende ältere Version -> strong=True."""
    now = "2026-06-01T00:00:00+00:00"
    entry = {
        "versions": [
            {"v": 1, "since": "2026-01-01T00:00:00+00:00",
             "params": {"sl_pct": 1.0, "tp_ratio": 2.0, "timeframe": "5m"}},
            {"v": 2, "since": "2026-03-01T00:00:00+00:00",
             "params": {"sl_pct": 2.0, "tp_ratio": 2.0, "timeframe": "5m"}},
        ],
        "active": 2,
    }
    # v1 lief gut (viele profitable Trades), v2 schlecht -> Rollback auf v1.
    trades = []
    for i in range(8):  # v1-Fenster: profitabel
        trades.append({"opened_at": "2026-01-05T00:00:00+00:00", "realized_pnl": 5.0,
                       "entry": 100, "sl": 99, "tpf": 103, "leverage": 5, "timeframe": "5m"})
    for i in range(8):  # v2-Fenster: verlustreich
        trades.append({"opened_at": "2026-03-05T00:00:00+00:00", "realized_pnl": -5.0,
                       "entry": 100, "sl": 98, "tpf": 104, "leverage": 5, "timeframe": "5m"})
    new_entry, event = lc.evolve_versions(entry, trades, now_iso=now)
    active = new_entry["versions"][-1]
    assert active.get("rollback_of") == 1
    assert active.get("strong") is True   # 1.0 vs 2.0 SL = 100 % Sprung
