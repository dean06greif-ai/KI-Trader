"""Audit 2.7: Reward ohne Konfidenz-Anreiz (Schalter, Default aus) +
getrennte Kennzahl Kalibrierungsfehler. Ohne Netzwerk."""
from services import ai_rewards


def _trade(pnl, result, minutes=60):
    return {"realized_pnl": pnl, "max_capital": 100.0, "result": result,
            "risk_usdt": 10.0,
            "opened_at": "2026-06-01T10:00:00+00:00",
            "closed_at": f"2026-06-01T{10 + minutes // 60:02d}:{minutes % 60:02d}:00+00:00"}


def test_default_reward_has_no_confidence_component():
    r = ai_rewards.compute_reward(_trade(5.0, "win"), {"confidence": 95})
    assert not any("Konfidenz" in c["label"] for c in r["components"])
    assert r["confidence"] == 95  # Konfidenz wird trotzdem gespeichert (Kalibrierung)
    assert r["violations"] == []


def test_confidence_reward_switch_restores_old_behaviour():
    win = ai_rewards.compute_reward(_trade(5.0, "win"), {"confidence": 95},
                                    confidence_reward=True)
    assert any("Disziplin-Bonus" in c["label"] for c in win["components"])
    loss = ai_rewards.compute_reward(_trade(-5.0, "loss"), {"confidence": 60},
                                     confidence_reward=True)
    assert "Verlust bei Konfidenz <80%" in loss["violations"]


def test_violations_list_quick_stopout():
    r = ai_rewards.compute_reward(_trade(-3.0, "loss", minutes=5))
    assert r["violations"] == ["Sofort-Stop-Out (<15 min)"]


def _row(conf, result):
    return {"confidence": conf, "result": result}


def test_calibration_bins_and_error():
    rows = ([_row(85, "win")] * 4 + [_row(85, "loss")] * 6   # Bin 80–89: conf 85, WR 40 -> gap 45
            + [_row(65, "win")] * 5 + [_row(65, "loss")] * 5)  # Bin 60–69: conf 65, WR 50 -> gap 15
    cal = ai_rewards.calibration_from_rows(rows)
    assert cal["rated"] == 20
    b80 = next(b for b in cal["bins"] if b["bin"] == "80–89")
    assert b80["trades"] == 10 and b80["win_rate"] == 40.0 and b80["gap"] == 45.0
    assert cal["calibration_error"] == 30.0  # (45*10 + 15*10) / 20


def test_calibration_ignores_rows_without_confidence_or_result():
    rows = [{"confidence": None, "result": "win"}, _row(75, "breakeven"), _row(75, "win")]
    cal = ai_rewards.calibration_from_rows(rows)
    assert cal["rated"] == 1
    assert cal["bins"][0]["trades"] == 1


def test_calibration_empty():
    cal = ai_rewards.calibration_from_rows([])
    assert cal["calibration_error"] is None and cal["bins"] == []
