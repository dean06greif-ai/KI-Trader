"""Regressionstests Nachanalyse (services/trade_postmortem.py) – reine Regeln,
ohne Backend/Netzwerk."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import trade_postmortem as pm  # noqa: E402

MIN = 60_000


def _candles(prices, start_ms=1_700_000_000_000, spread=0.5):
    """1m-Kerzen aus Close-Preisen (High/Low = Close ± spread)."""
    return [{"timestamp": start_ms + i * MIN, "open": p, "high": p + spread,
             "low": p - spread, "close": p, "volume": 1.0}
            for i, p in enumerate(prices)]


def _iso(ms):
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()


def _trade(entry=100.0, sl=99.0, tpf=102.0, side="LONG", exit_price=102.0,
           opened_ms=1_700_000_000_000, closed_ms=None, result="win", **extra):
    closed_ms = closed_ms or opened_ms + 10 * MIN
    t = {"id": "t1", "symbol": "BTCUSDT", "side": side, "entry": entry, "initial_sl": sl,
         "sl": sl, "tpf": tpf, "risk": abs(entry - sl), "exit_price": exit_price,
         "opened_at": _iso(opened_ms), "closed_at": _iso(closed_ms), "result": result,
         "realized_pnl": 1.0, "setup": "breakout", "mode": "paper", "horizon": "scalp"}
    t.update(extra)
    return t


# ---------------- Bausteine ----------------
def test_lookahead_clamped():
    assert pm.lookahead_minutes(0, 5 * MIN) == pm.LOOKAHEAD_MIN          # 2×5 min < 30 -> 30
    assert pm.lookahead_minutes(0, 60 * MIN) == 120                        # 2× Dauer
    assert pm.lookahead_minutes(0, 48 * 60 * MIN) == pm.LOOKAHEAD_MAX      # Deckel 24h


def test_r_multiple_both_sides():
    assert pm.r_multiple("LONG", 100, 102, 1.0) == 2.0
    assert pm.r_multiple("SHORT", 100, 102, 1.0) == -2.0
    assert pm.r_multiple("LONG", 100, 102, 0) == 0.0


def test_simulate_exit_conservative_sl_first():
    # Kerze berührt SL UND TP -> SL zählt (konservativ)
    c = [{"timestamp": 1, "open": 100, "high": 103, "low": 98, "close": 100}]
    assert pm.simulate_exit(c, "LONG", 99.0, 102.0)["reason"] == "sl"
    c2 = _candles([100, 101, 102.6])
    assert pm.simulate_exit(c2, "LONG", 99.0, 102.0)["reason"] == "tp"
    assert pm.simulate_exit(_candles([100, 100.2]), "LONG", 99.0, 105.0)["reason"] == "open"
    assert pm.simulate_exit([], "LONG", 99.0, 105.0)["reason"] == "no_data"


def test_excursions_long_short():
    c = _candles([100, 101, 99], spread=0)
    mfe, mae = pm.excursions(c, "LONG", 100.0, 1.0)
    assert (mfe, mae) == (1.0, 1.0)
    mfe_s, mae_s = pm.excursions(c, "SHORT", 100.0, 1.0)
    assert (mfe_s, mae_s) == (1.0, 1.0)


def test_trade_frame_rejects_incomplete():
    assert pm.trade_frame({"entry": 100}) is None
    assert pm.trade_frame(_trade(sl=100.0)) is None            # Risiko 0
    fr = pm.trade_frame(_trade())
    assert fr and fr["tp_ratio"] == 2.0 and fr["side"] == "LONG"


# ---------------- Einzel-Review ----------------
def test_review_trade_early_exit_detects_left_on_table():
    start = 1_700_000_000_000
    # 10 min bis TP (102), danach läuft der Kurs weiter bis 106 (Nachlauf 30 min)
    prices = [100, 100.5, 101, 101.5, 102.5, 102.5, 102.5, 102.5, 102.5, 102.5, 102.5] \
        + [103 + i * 0.1 for i in range(30)]
    c = _candles(prices, start, spread=0.1)
    t = _trade(opened_ms=start, closed_ms=start + 10 * MIN)
    rev = pm.review_trade(t, c)
    assert rev["base_r"] == 2.0
    assert rev["verdict"] == "early_exit"
    assert rev["after"]["mfe_r"] >= 1.0
    # weiter entfernter TP ×2 wäre gefüllt worden (104)
    assert rev["variants"]["tp_x2"]["reason"] == "tp"
    assert rev["variants"]["tp_x2"]["delta_r"] > 0
    # Runner-Variante endet zum letzten Close (offen) und ist besser als Baseline
    assert rev["variants"]["runner"]["reason"] == "open"
    assert rev["variants"]["runner"]["delta_r"] > 0
    assert rev["setup"] == "breakout"


def test_review_trade_stopped_before_move():
    start = 1_700_000_000_000
    prices = [100, 99.6, 98.9, 98.9] + [99 + i * 0.15 for i in range(40)]  # SL 99 -> danach +
    c = _candles(prices, start, spread=0.05)
    t = _trade(opened_ms=start, closed_ms=start + 3 * MIN, exit_price=99.0, result="loss")
    rev = pm.review_trade(t, c)
    assert rev["verdict"] == "stopped_before_move"
    assert rev["variants"]["sl_x1.5"]["reason"] in ("tp", "open")
    assert rev["variants"]["sl_x1.5"]["delta_r"] > 0


def test_review_trade_short_side():
    start = 1_700_000_000_000
    prices = [100, 99.5, 99, 98.4] + [98.4] * 30
    c = _candles(prices, start, spread=0.05)
    t = _trade(side="SHORT", sl=101.0, tpf=98.0, exit_price=98.5, opened_ms=start,
               closed_ms=start + 4 * MIN)
    rev = pm.review_trade(t, c)
    assert rev["side"] == "SHORT" and rev["base_r"] == 1.5
    assert rev["variants"]["tp_x0.75"]["reason"] == "tp"


def test_review_trade_no_data_returns_none():
    assert pm.review_trade(_trade(), []) is None
    assert pm.review_trade({"entry": 1}, _candles([1, 1])) is None


# ---------------- Limit-Order-Review ----------------
def _limit_row(created_ms=1_700_000_000_000, valid_min=30, limit=99.0, ref=100.0,
               sl=98.0, tp=103.0, side="LONG"):
    return {"id": "l1", "symbol": "BTCUSDT", "side": side, "limit_price": limit, "ref_price": ref,
            "dist_pct": 1.0, "valid_min": valid_min, "created_at": _iso(created_ms),
            "expires_at": _iso(created_ms + valid_min * MIN), "status": "expired",
            "setup": "pullback",
            "signal": {"type": side, "entry_price": limit, "stop_loss": sl, "take_profit_full": tp}}


def test_limit_review_missed_move():
    start = 1_700_000_000_000
    prices = [100 + i * 0.1 for i in range(70)]  # läuft ohne Rücksetzer nach oben (bis 106.9)
    rev = pm.review_limit_order(_limit_row(start), _candles(prices, start, spread=0.05))
    assert rev["verdict"] == "missed_move"
    assert rev["market_r"] >= 1.0
    assert rev["miss_pct"] > 0.9


def test_limit_review_avoided_loss():
    start = 1_700_000_000_000
    prices = [100 - i * 0.05 for i in range(70)]  # fällt langsam auf ~96.5 -> SL 98
    rev = pm.review_limit_order(_limit_row(start, limit=95.0, sl=94.0),
                                _candles(prices, start, spread=0.02))
    assert rev["verdict"] == "avoided_loss"


def test_limit_review_incomplete_returns_none():
    row = _limit_row()
    row["signal"] = {}
    assert pm.review_limit_order(row, _candles([100, 100])) is None


# ---------------- Overfitting-Schutz ----------------
def test_variant_verdict_requires_sample_consistency_split():
    good = [0.5, 0.4, 0.6, 0.3, 0.5, 0.7, 0.4, 0.6]
    v = pm.variant_verdict(good)
    assert v["robust"] and v["split_agree"] and v["consistency"] == 1.0
    # zu wenig Trades
    assert not pm.variant_verdict(good[:5])["robust"]
    assert "Trades" in pm.variant_verdict(good[:5])["reason"]
    # ein Ausreißer treibt den Mittelwert, Median/Konsistenz fallen durch
    outlier = [-0.1, -0.2, -0.1, 8.0, -0.1, -0.2, -0.1, -0.1]
    assert not pm.variant_verdict(outlier)["robust"]
    # ältere Hälfte gut, jüngere schlecht -> Split-Half schlägt an
    drift = [0.8, 0.9, 0.7, 0.8, -0.1, 0.1, -0.2, 0.05]
    vd = pm.variant_verdict(drift)
    assert not vd["split_agree"] and not vd["robust"]
    # Verbesserung unter Kosten-Schwelle
    tiny = [0.05] * 8
    assert not pm.variant_verdict(tiny)["robust"]
    assert pm.variant_verdict([])["n"] == 0


def test_recommended_step_capped():
    assert pm.recommended_step("tp", 2.0) == 1.2
    assert pm.recommended_step("sl", 0.75) == 0.8
    assert pm.recommended_step("tp", 1.1) == 1.1
    assert pm.recommended_step("runner", None) is None


def _rev(setup, deltas: dict, verdict="neutral", closed="2026-06-01T00:00:00+00:00", base=1.0):
    return {"setup": setup, "closed_at": closed, "base_r": base, "verdict": verdict,
            "after": {"mfe_r": 0.5}, "variants": {k: {"kind": "tp" if k.startswith("tp") else "sl",
                                                       "factor": float(k.split("x")[1]),
                                                       "delta_r": d} for k, d in deltas.items()}}


def test_aggregate_only_robust_findings_and_backtest_flag():
    rows = [_rev("breakout", {"tp_x1.5": 0.5, "sl_x0.75": -0.3},
                 closed=f"2026-06-{i + 1:02d}T00:00:00+00:00") for i in range(10)]
    agg = pm.aggregate(rows, {"breakout": {"status": "passed"}})
    assert len(agg) == 1
    s = agg[0]
    assert s["enough_data"] and s["best_variant"] == "tp_x1.5"
    assert "×1.2" in s["finding"] and "Backtest bestätigt" in s["finding"]
    assert s["variants"]["sl_x0.75"]["robust"] is False
    # ohne Backtest-Bestätigung: nur Hinweis
    agg2 = pm.aggregate(rows, {})
    assert "NICHT bestätigt" in agg2[0]["finding"]
    # zu wenig Trades -> kein Finding
    agg3 = pm.aggregate(rows[:4], {})
    assert agg3[0]["finding"] is None and not agg3[0]["enough_data"]


def test_context_lines_only_when_robust():
    assert pm.context_lines([], {"note": None}) == []
    rows = [_rev("breakout", {"tp_x1.5": 0.5}, closed=f"2026-06-{i + 1:02d}T00:00:00+00:00")
            for i in range(10)]
    lines = pm.context_lines(pm.aggregate(rows, {}), {"note": None})
    assert lines and "NACHANALYSE" in lines[0] and "breakout" in lines[1]
    # Nicht-robuste Setups tauchen NICHT auf
    noisy = [_rev("fade", {"tp_x1.5": (-1) ** i * 0.5}, closed=f"2026-06-{i + 1:02d}T00:00:00+00:00")
             for i in range(10)]
    assert pm.context_lines(pm.aggregate(noisy, {}), {"note": None}) == []


def test_aggregate_limits_note_thresholds():
    rows = [{"verdict": "missed_move", "miss_pct": 0.4}] * 6 + [{"verdict": "neutral", "miss_pct": 1.0}] * 2
    agg = pm.aggregate_limits(rows)
    assert agg["enough_data"] and "verpassten" in agg["note"]
    assert pm.aggregate_limits(rows[:3])["note"] is None
