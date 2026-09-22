"""Regression: Kalibrierungs-Verlauf (vereint), Einstellungs-Snapshots,
Autopilot-Übernahmeregel (nie schlechter), Shadow-Backfill (rein) und
übersprungene Symbole in fetch_histories."""
import asyncio

from services import regime_autopilot as ap
from services import regime_calibration_history as hist
from services import regime_shadow_backfill as sb


# ---------------- Verlauf ----------------
def _cal(id_, pct, created):
    return {"id": id_, "created_at": created, "symbols": ["XAUUSDT"], "timeframe": "1d",
            "report": {"detector": "ema", "best_config": {"detector": "ema", "ema_regime_days": 12},
                       "baseline": {"balanced_direction_pct": 90.0},
                       "best": {"balanced_direction_pct": pct}, "improved": True, "total_days": 1080}}


def _run(id_, created, holdout_best=95.7, holdout_base=96.4, improved=True):
    return {"id": id_, "created_at": created,
            "result": {"kind": "autopilot", "improved": improved, "tested": 40, "days": 1080,
                       "symbols": ["XAUUSDT", "XAGUSDT"], "timeframe": "1d",
                       "holdout_regressed": holdout_best < holdout_base,
                       "best_engine_config": {"detector": "ema", "ema_regime_days": 20, "confidence_min": 0.6},
                       "best": {"detector": "ema", "score": 97.2, "changes": {"ema_regime_days": 20},
                                "metrics": {"holdout_direction_pct": holdout_best, "inner_direction_pct": 97.0}},
                       "baseline": {"score": 96.0, "metrics": {"holdout_direction_pct": holdout_base}}}}


def test_autopilot_row_has_config_and_metric():
    row = hist.autopilot_row(_run("r1", "2026-06-02T10:00:00+00:00"))
    assert row["source"] == "autopilot"
    assert row["report"]["metric"] == hist.METRIC_AUTOPILOT
    assert row["report"]["best_config"]["ema_regime_days"] == 20
    assert row["report"]["best"]["holdout_direction_pct"] == 95.7
    assert row["report"]["holdout_regressed"] is True
    assert row["report"]["changes"] == [{"key": "ema_regime_days", "to": 20}]


def test_autopilot_row_without_config_is_dropped():
    assert hist.autopilot_row({"id": "x", "result": {"kind": "autopilot"}}) is None


def test_merged_history_sorted_newest_first_and_mixed_sources():
    rows = hist.merged_history(
        [_cal("c1", 96.4, "2026-06-01T10:00:00+00:00")],
        [_run("r1", "2026-06-02T10:00:00+00:00"), {"id": "bad", "result": {}}], limit=10)
    assert [r["id"] for r in rows] == ["r1", "c1"]
    assert rows[1]["source"] == "calibration"
    assert rows[1]["report"]["metric"] == hist.METRIC_CALIBRATION
    # bestehende Felder bleiben (Rückwärtskompatibilität für die alte Oberfläche)
    assert rows[1]["report"]["best"]["balanced_direction_pct"] == 96.4


def test_merged_history_respects_limit():
    cals = [_cal(f"c{i}", 90 + i, f"2026-06-0{i}T10:00:00+00:00") for i in range(1, 6)]
    assert len(hist.merged_history(cals, [], limit=3)) == 3


def test_snapshot_doc_copies_and_truncates():
    cfg = {"detector": "ema"}
    snap = hist.snapshot_doc(cfg, {"best_pct": 96.4}, "x" * 300)
    cfg["detector"] = "kombi"
    assert snap["engine_config"]["detector"] == "ema"
    assert len(snap["reason"]) == 120
    assert snap["calib_applied"]["best_pct"] == 96.4
    assert hist.snapshot_doc(cfg, None, "r")["calib_applied"] is None


# ---------------- Autopilot: nie ein schlechteres Ergebnis ----------------
def test_holdout_regressed_rule():
    assert ap.holdout_regressed({"holdout_direction_pct": 95.7}, {"holdout_direction_pct": 96.4}) is True
    assert ap.holdout_regressed({"holdout_direction_pct": 96.4}, {"holdout_direction_pct": 96.4}) is False
    assert ap.holdout_regressed({"holdout_direction_pct": 97.0}, {"holdout_direction_pct": 96.4}) is False
    assert ap.holdout_regressed({}, {"holdout_direction_pct": 96.4}) is False
    assert ap.holdout_regressed(None, None) is False


def test_adopt_recommended_blocks_holdout_drop():
    worse = _run("r", "t")["result"]
    assert ap.adopt_recommended(worse) is False
    better = _run("r", "t", holdout_best=96.9)["result"]
    assert ap.adopt_recommended(better) is True
    assert ap.adopt_recommended({**better, "improved": False}) is False


def test_followup_not_chained_when_holdout_regressed():
    res = {**_run("r", "t")["result"], "adopt_recommended": False}
    assert ap.followup_analysis_body(res, {"auto_chain": True}) is None
    ok = {**_run("r", "t", holdout_best=97.0)["result"], "adopt_recommended": True}
    body = ap.followup_analysis_body(ok, {"auto_chain": True})
    assert body and body["engine_config"]["detector"] == "ema"
    # Rückwärtskompatibel: ohne Feld zählt weiterhin `improved`
    legacy = _run("r", "t", holdout_best=97.0)["result"]
    assert ap.followup_analysis_body(legacy, {}) is not None


# ---------------- Shadow-Backfill (rein) ----------------
def test_entry_ts_and_candles_until():
    assert sb.entry_ts_of({"opened_at": "2026-06-01T00:00:00+00:00"}) == 1780272000000
    assert sb.entry_ts_of({"opened_at": 1780272000}) == 1780272000000
    assert sb.entry_ts_of({}) is None
    candles = [{"timestamp": 1000}, {"timestamp": 2000}, {"timestamp": 3000}]
    assert [c["timestamp"] for c in sb.candles_until(candles, 2500)] == [1000, 2000]
    assert sb.candles_until(candles, 2000) == [{"timestamp": 1000}]   # Entry-Kerze selbst nie (kein Lookahead)


def test_plan_updates_uses_only_candles_before_entry(monkeypatch):
    seen = {}

    def fake_label(model, candles, timeframe, tf_sec):
        seen["n"] = len(candles)
        return "strukturell bulle"

    monkeypatch.setattr(sb, "structural_label", fake_label)
    candles = [{"timestamp": t * 3600000} for t in range(10)]
    rewards = [{"id": "rw1", "trade_id": "t1"}, {"id": "rw2", "trade_id": "missing", "ts": None}]
    trades = {"t1": {"id": "t1", "opened_at": "1970-01-01T05:00:00+00:00"}}
    out = sb.plan_updates(rewards, trades, {}, candles, "1h", 3600, "ra_1")
    assert [rid for rid, _ in out] == ["rw1"]
    assert seen["n"] == 5
    assert out[0][1] == {"structural_regime": "strukturell bulle", "structural_source": "backfill",
                         "structural_aid": "ra_1"}


def test_structural_label_none_on_thin_data():
    assert sb.structural_label({}, [], "1h", 3600) is None


# ---------------- fetch_histories: übersprungene Symbole ----------------
def test_fetch_histories_reports_skipped(monkeypatch):
    from services import regime_lab as lab
    import services.backtester as bt

    async def fake_fetch(session, sym, days, job=None):
        n = 50 if sym == "CLUSDT" else 400
        return [{"timestamp": 1_700_000_000_000 + i * 86400000, "open": 1, "high": 1, "low": 1,
                 "close": 1, "volume": 1} for i in range(n)]

    monkeypatch.setattr(bt, "fetch_history", fake_fetch)
    skipped = {}
    hist_ = asyncio.run(lab.fetch_histories(["XAUUSDT", "CLUSDT"], 1080, "1d", skipped=skipped))
    assert "XAUUSDT" in hist_ and "CLUSDT" not in hist_
    assert skipped["CLUSDT"]["bars"] <= 50
    assert "zu wenig Daten" in skipped["CLUSDT"]["reason"]
