"""Regressionstests: Trade-Management im Backtester (simulate_pair).

Prüft, dass die Trade-Einstellungen wirklich simuliert werden:
TP1-Teilschließung (tp1_close_percent), Break-Even (tp1 / crv / off),
ATR-Trailing nach TP1 (an/aus, Abstand), Gewinnsicherung, Ergebnis-Label
nach realisiertem PnL. Reine Unit-Tests ohne Backend/Netzwerk.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.backtester import simulate_pair, WARMUP  # noqa: E402

ENTRY = 100.0


class _LongOnce:
    """Signalisiert genau einmal LONG (erste Prüfung nach dem Warmup)."""
    STRATEGY_ID = "test_long_once"

    def __init__(self):
        self.fired = False

    def check_signal(self, window, symbol, settings):
        if self.fired:
            return None
        self.fired = True
        return {"type": "LONG", "entry_price": ENTRY}


def _candles(path, n_flat=WARMUP + 1, rng=0.1):
    """Flache Kerzen (Warmup) + explizite Schlusskurs-Pfad-Kerzen (±rng)."""
    out = []
    ts = 1_700_000_000_000
    for i in range(n_flat):
        out.append({"timestamp": ts + i * 60000, "open": ENTRY, "high": ENTRY + 0.2,
                    "low": ENTRY - 0.2, "close": ENTRY, "volume": 10.0})
    for j, c in enumerate(path):
        out.append({"timestamp": ts + (n_flat + j) * 60000, "open": c, "high": c + rng,
                    "low": c - rng, "close": c, "volume": 10.0})
    # Puffer, damit die Simulation (n >= 2*WARMUP) den festen Warmup nutzt
    while len(out) < WARMUP * 2 + 10:
        j = len(out)
        c = path[-1]
        out.append({"timestamp": ts + j * 60000, "open": c, "high": c + rng,
                    "low": c - rng, "close": c, "volume": 10.0})
    return out


def _cfg(**over):
    cfg = {"max_capital": 100.0, "leverage": 5, "fee_percent": 0.0,
           "sl_mode": "fixed", "sl_fixed_percent": 1.0,   # SL 99, Risiko 1.0
           "tp_mode": "crv", "tp1_crv": 1.0, "tp_full_crv": 3.0,  # TP1 101, TPF 103
           "tp1_close_percent": 50, "be_mode": "tp1",
           "trail_after_tp1": True, "trail_atr_mult": 1.5,
           "profit_secure_enabled": False, "maintenance_margin_rate": 0.5,
           "min_risk_percent": 0.1}
    cfg.update(over)
    return cfg


def _run(path, **over):
    res = simulate_pair(_LongOnce(), _candles(path), "BTCUSDT", {}, _cfg(**over),
                        collect_trades=True)
    assert res["trades"] == 1, res
    return res, res["all_trades"][0]


# Pfad: hoch durch TP1 (101) bis 102.5 (unter TPF 103), dann Absturz auf 99.
RUN_UP_THEN_DROP = [100.3, 100.6, 101.2, 101.8, 102.5, 102.5, 101.5, 100.5, 99.0, 99.0]


def test_tp1_partial_close_and_breakeven():
    res, t = _run(RUN_UP_THEN_DROP)
    assert t["tp1_done"] is True
    assert t["breakeven_moved"] is True
    # Halbe Position an TP1: 50 % der Menge realisiert
    qty = t["qty"]
    assert abs(qty - 100.0 * 5 / ENTRY) < 1e-6
    assert t["pnl"] > 0
    # Rest-Exit über dem Entry (BE oder getrailter SL) – nie ein voller Verlust
    assert t["exit"] > ENTRY
    assert t["result"] == "win"


def test_trailing_stop_locks_more_profit_than_breakeven_only():
    _, with_trail = _run(RUN_UP_THEN_DROP, trail_after_tp1=True)
    _, no_trail = _run(RUN_UP_THEN_DROP, trail_after_tp1=False)
    # Ohne Trailing bleibt der SL am Break-Even -> Exit nahe Entry (+Gebühren)
    assert abs(no_trail["exit"] - ENTRY) < 0.05
    # Mit Trailing wird der SL hinter das Hoch gezogen -> deutlich höherer Exit
    assert with_trail["exit"] > no_trail["exit"] + 0.5
    assert with_trail["pnl"] > no_trail["pnl"]
    assert with_trail["sl_final"] > with_trail["sl_initial"]


def test_trailing_distance_is_respected():
    _, tight = _run(RUN_UP_THEN_DROP, trail_atr_mult=0.5)
    _, wide = _run(RUN_UP_THEN_DROP, trail_atr_mult=3.0)
    # Enger Trail (0.5 ATR) stoppt näher am Hoch als ein weiter (3 ATR)
    assert tight["exit"] >= wide["exit"]
    assert tight["sl_final"] > wide["sl_final"]


def test_tp1_close_percent_changes_realized_share():
    _, small = _run(RUN_UP_THEN_DROP, tp1_close_percent=20, trail_after_tp1=False)
    _, big = _run(RUN_UP_THEN_DROP, tp1_close_percent=80, trail_after_tp1=False)
    # Ohne Trailing: Rest schließt am BE (~0) -> PnL ≈ TP1-Anteil × 1R
    assert big["pnl"] > small["pnl"]
    assert abs(big["pnl"] / small["pnl"] - 4.0) < 0.3


def test_breakeven_off_keeps_original_stop():
    # Pfad: TP1 wird erreicht, danach fällt der Kurs unter den Entry bis knapp
    # über den Original-SL (99) -> ohne BE/Trail bleibt der Trade offen bzw.
    # schließt erst am ursprünglichen SL.
    path = [100.4, 100.8, 101.3, 100.6, 99.6, 99.6, 98.8, 98.8]
    _, t = _run(path, be_mode="off", trail_after_tp1=False)
    assert t["tp1_done"] is True
    assert t["breakeven_moved"] is False
    assert abs(t["exit"] - 99.0) < 1e-6  # Original-SL
    # Label folgt dem PnL: +0.5R (50 %) − 0.5R (50 %) ≈ 0 -> breakeven/loss, nie "win"
    assert t["result"] in ("breakeven", "loss")


def test_breakeven_crv_mode_triggers_before_tp1():
    # BE ab 0.5 R (100.5) – Kurs erreicht 100.7, fällt dann auf 99.0:
    # BE-Stop (~Entry) greift, kein voller Verlust
    path = [100.3, 100.7, 100.7, 100.2, 99.7, 99.0, 99.0]
    _, t = _run(path, be_mode="crv", be_trigger_crv=0.5)
    assert t["tp1_done"] is False
    assert t["breakeven_moved"] is True
    assert abs(t["exit"] - ENTRY) < 0.05
    assert t["result"] == "breakeven"


def test_full_stop_loss_without_management_is_a_loss():
    path = [99.8, 99.5, 99.2, 98.8, 98.8]
    _, t = _run(path)
    assert t["tp1_done"] is False and t["breakeven_moved"] is False
    assert abs(t["exit"] - 99.0) < 1e-6
    assert t["result"] == "loss"
    assert t["pnl"] < 0


def test_profit_secure_moves_stop_into_profit_before_tp1():
    # Gewinnsicherung ab 2 % auf die Marge (Hebel 5: +0.4 % Kurs = 100.4),
    # sichert 50 % des Gewinns; Kurs steigt auf 100.8, fällt dann auf 99.
    path = [100.3, 100.8, 100.8, 100.5, 100.0, 99.0, 99.0]
    _, t = _run(path, profit_secure_enabled=True, profit_secure_trigger_pct=2.0,
                profit_lock_pct=50.0, be_mode="off", trail_after_tp1=False)
    assert t["profit_secured"] is True
    assert t["exit"] > ENTRY
    assert t["pnl"] > 0 and t["result"] == "win"


def test_result_label_follows_realized_pnl_after_be_move():
    res, t = _run(RUN_UP_THEN_DROP)
    assert t["result"] == "win" and t["pnl"] > 0
    assert res["wins"] == 1 and res["losses"] == 0
    assert res["be_moved"] == 1
