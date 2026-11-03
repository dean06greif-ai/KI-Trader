"""Audit 2.2: ML-Leckage schließen. Ohne Netzwerk.

1) nearest_snapshot nutzt nur Snapshots ts <= target (kein Blick in die Zukunft)
2) ai_ml_lab.train_sync validiert mit zeitlichen Folds statt gemischtem KFold
3) purged_walk_forward purgt zusätzlich nach label_ts (= Trade-Close)
4) ml_gate.train_sync bewertet die Kalibrierung verschachtelt (Fit nur auf
   früheren Folds) und liefert label_ts aus den Row-Buildern
"""
from datetime import datetime, timedelta, timezone

import pytest

from services import ai_ml_lab as mlab
from services import ml_gate as mg

T0 = datetime(2026, 6, 1, 12, 0, tzinfo=timezone.utc)


# ---------------- nearest_snapshot: Zukunft gesperrt ----------------
def test_nearest_snapshot_ignores_future_by_default():
    snaps = [{"ts": (T0 + timedelta(minutes=3)).isoformat(), "features": {"rsi": 99}},
             {"ts": (T0 - timedelta(minutes=10)).isoformat(), "features": {"rsi": 42}}]
    s = mlab.nearest_snapshot(snaps, T0)
    assert s["features"]["rsi"] == 42  # der 3-min-ZUKUNFTS-Snapshot wäre näher
    only_future = [{"ts": (T0 + timedelta(minutes=3)).isoformat(), "features": {"rsi": 99}}]
    assert mlab.nearest_snapshot(only_future, T0) is None
    # altes Verhalten nur explizit
    assert mlab.nearest_snapshot(only_future, T0, allow_future=True)["features"]["rsi"] == 99


# ---------------- temporal_folds: Expanding Window ----------------
def test_temporal_folds_train_strictly_before_test():
    splits = mlab.temporal_folds(120, n_folds=4, min_train=20, min_test=8)
    assert splits
    for train_idx, test_idx in splits:
        assert max(train_idx) < min(test_idx)
        assert train_idx == list(range(len(train_idx)))  # Expanding ab 0
    # zu wenig Daten -> keine Folds
    assert mlab.temporal_folds(10) == []


def test_ai_ml_train_sync_uses_temporal_cv():
    ok, _ = mlab.libs_available()
    if not ok:
        pytest.skip("optuna/xgboost nicht installiert")
    rows, labels, tss = [], [], []
    for i in range(90):
        win = i % 2 == 0
        rows.append(mlab.feature_row(
            {"confidence": 80 if win else 40, "action": "LONG",
             "ts": (T0 + timedelta(hours=i)).isoformat(), "sl_pct": 1.0, "tp1_pct": 2.0},
            {"features": {"rsi": 60 if win else 30, "trend_pct": 0.3 if win else -0.3,
                          "volatility_pct": 0.1, "atr_pct": 0.05, "volume_ratio": 1.2,
                          "range_pos": 60, "change_60m_pct": 0.5 if win else -0.5}}))
        labels.append(1 if win else 0)
        tss.append(T0 + timedelta(hours=i))
    out = mlab.train_sync(rows, labels, n_trials=5, timeout_sec=60, timestamps=tss)
    assert out["cv_mode"] == "temporal"
    assert out["samples"] == 90 and out["booster_b64"]
    # ohne Zeitstempel bleibt der alte Stratified-Fallback
    out2 = mlab.train_sync(rows, labels, n_trials=5, timeout_sec=60)
    assert out2["cv_mode"] == "stratified"


# ---------------- purged_walk_forward: Label-Purge ----------------
def test_purged_walk_forward_purges_by_label_ts():
    n = 80
    tss = [T0 + timedelta(hours=i) for i in range(n)]
    # Jeder Trade schließt erst 200h nach Eröffnung -> Label liegt für frühe
    # Folds in der Zukunft -> ohne genug "geschlossene" Trades keine Splits
    ltss_open = [t + timedelta(hours=200) for t in tss]
    assert mg.purged_walk_forward(tss, min_train=10, min_test=5,
                                  label_timestamps=ltss_open) == []
    # Sofort geschlossene Trades verhalten sich wie ohne Label-Purge
    ltss_now = [t + timedelta(minutes=30) for t in tss]
    with_labels = mg.purged_walk_forward(tss, min_train=10, min_test=5,
                                         label_timestamps=ltss_now)
    without = mg.purged_walk_forward(tss, min_train=10, min_test=5)
    assert with_labels and len(with_labels) <= len(without)
    for (tr_l, te_l) in with_labels:
        cutoff = tss[min(te_l)] - timedelta(hours=mg.EMBARGO_HOURS)
        assert all(ltss_now[i] < cutoff for i in tr_l)  # Label entstand vor Test


# ---------------- Row-Builder liefern label_ts ----------------
def test_row_builders_return_label_ts():
    dec = {"outcome": "win", "ts": T0.isoformat(), "action": "LONG", "confidence": 70,
           "sl_pct": 1.0, "tp1_pct": 2.0,
           "trade_closed_at": (T0 + timedelta(hours=5)).isoformat()}
    row, lbl, w, ts, lts = mg.row_from_decision(dec)
    assert lbl == 1 and ts == T0 and lts == T0 + timedelta(hours=5)
    dec.pop("trade_closed_at")
    dec["outcome_ts"] = (T0 + timedelta(hours=3)).isoformat()
    assert mg.row_from_decision(dec)[4] == T0 + timedelta(hours=3)
    dec.pop("outcome_ts")
    assert mg.row_from_decision(dec)[4] == T0  # Fallback: Entscheidungszeit

    sig = {"result": "loss", "timestamp": T0.isoformat(), "type": "SHORT",
           "entry_price": 100, "stop_loss": 101, "take_profit_1": 98,
           "result_ts": (T0 + timedelta(hours=2)).isoformat()}
    assert mg.row_from_signal(sig)[4] == T0 + timedelta(hours=2)

    g = {"result": "win", "opened_at": T0.isoformat(), "side": "LONG",
         "entry": 100, "sl": 99, "tp": 102,
         "closed_at": (T0 + timedelta(hours=1)).isoformat()}
    assert mg.row_from_ghost(g)[4] == T0 + timedelta(hours=1)


# ---------------- Gate-Training: verschachtelte Kalibrierung ----------------
def test_gate_train_sync_nested_calibration_and_label_purge_flag():
    ok, _ = mlab.libs_available()
    if not ok:
        pytest.skip("optuna/xgboost nicht installiert")
    rows, y, w, tss, ltss = [], [], [], [], []
    for i in range(400):
        win = (i * 7) % 10 < 5
        ts = T0 + timedelta(hours=i)
        rows.append(mg.gate_feature_row(
            "LONG" if i % 2 == 0 else "SHORT", 70 if win else 40, 1.0, 2.0, ts,
            {"rsi": 60 if win else 30, "trend_pct": 0.2 if win else -0.2,
             "volatility_pct": 0.1, "atr_pct": 0.05, "volume_ratio": 1.1,
             "range_pos": 55, "change_60m_pct": 0.3 if win else -0.3}, "decision"))
        y.append(1 if win else 0)
        w.append(1.0)
        tss.append(ts)
        ltss.append(ts + timedelta(hours=2))
    out = mg.train_sync(rows, y, w, tss, ltss)
    m = out["metrics"]
    assert m["label_purged"] is True and m["folds_used"] >= 1
    assert out["calibration"] is not None  # produktiver Kalibrator vorhanden
    if "oos_brier_calibrated" in m:
        # verschachtelt bewertet: weniger Punkte als das gesamte OOS-Set
        assert m["calibration_nested_samples"] < m["oos_samples"]
