"""Regime-Autopilot: Timeframe-Kette (services/regime_tf_chain.py).

Regressionen: ohne Kette (Standard) verhält sich der Autopilot exakt wie
vorher (gleiche Ladevorgänge, gleiche Bewertungen, gleiches Ergebnis-Schema);
mit Kette werden Scoring (bester Start-Timeframe) und Fallback (Wechsel bei
Stillstand) geprüft – rein offline mit gefälschten Kerzen/Bewertungen."""
import asyncio

import pytest

from services import regime_autopilot as ap
from services import regime_lab as lab
from services import regime_tf_chain as ch


def _candles(n=400):
    return [{"timestamp": i * 3600000, "open": 100.0, "high": 101.0, "low": 99.0,
             "close": 100.0, "volume": 1.0} for i in range(n)]


def _metrics(score, holdout_bars=500):
    # score_metrics: (inner+train)/2 – ohne Referenz/Utility/Phasen = score
    return {"inner_direction_pct": float(score), "train_direction_pct": float(score),
            "holdout_direction_pct": float(score), "holdout_bars": holdout_bars}


def _patch(monkeypatch, tf_scores, no_data=(), holdout=None):
    """Fake-Daten je TF; Bewertung = Score des Timeframes (+ kleiner Zufall über
    die Konfiguration, damit Varianten unterscheidbar sind)."""
    fetched, evaluated = [], []

    async def fake_fetch(symbols, days, timeframe, job=None, **kw):
        fetched.append(timeframe)
        return {} if timeframe in no_data else {"BTCUSDT": _candles()}

    def fake_eval(cfg, histories, train_hist, bounds, inner_anchor, timeframe, stop=None):
        evaluated.append((timeframe, ap.config_key(cfg)))
        base = tf_scores.get(timeframe)
        if base is None:
            return None
        bump = float(cfg.get("ema_regime_days") or 0) * 0.0  # gleicher Score je TF -> Stillstand
        return _metrics(base + bump, (holdout or {}).get(timeframe, 500))

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    monkeypatch.setattr(ap, "evaluate_config", fake_eval)
    monkeypatch.setattr(ap, "mutate", lambda cfg, rng, sd, st: {
        "detector": "ema", "ema_regime_days": rng.randint(1, 100000)})
    return fetched, evaluated


def _run(body):
    job_id = lab.create_job("autopilot", {})
    asyncio.run(ap.run_autopilot(job_id, body, None))
    job = lab.JOBS[job_id]
    assert job["status"] == "done", job.get("error")
    return job, job["result"]


BASE_BODY = {"symbols": ["BTCUSDT"], "timeframe": "1h", "days": 120, "seed": 7,
             "engine_config": {"version": "v2", "detector": "ema"}}


# ---------------- reine Funktionen ----------------
@pytest.mark.parametrize("tf,expected", [
    ("1h", ["1h", "30m", "2h", "4h"]),
    ("15m", ["15m", "30m", "1h"]),
    ("4h", ["4h", "2h", "8h", "1d"]),
    ("1d", ["1d", "8h"]),
    ("24h", ["24h", "8h"]),
    ("5m", ["5m", "15m", "30m", "1h"]),
    ("unknown", ["unknown"]),
])
def test_build_chain_fixed_ladder(tf, expected):
    assert ch.build_chain(tf) == expected
    assert len(ch.build_chain(tf)) <= ch.MAX_CHAIN


def test_enabled_is_off_by_default():
    assert ch.enabled({}) is False
    assert ch.enabled(None) is False
    assert ch.enabled({"tf_chain": False}) is False
    assert ch.enabled({"tf_chain": "true"}) is False   # nur echtes True
    assert ch.enabled({"tf_chain": True}) is True


def test_rank_prefers_selected_with_hysteresis_and_drops_unusable():
    scores = {"1h": 60.0, "30m": 60.5, "2h": 62.0, "4h": None}
    assert ch.rank(scores, "1h") == ["2h", "1h", "30m"]          # 2h deutlich besser
    assert ch.rank({"1h": 60.0, "2h": 60.8}, "1h")[0] == "1h"    # unter Hysterese: bleibt
    # nicht nutzbar (Beweislage) fällt raus – gewählter TF bleibt immer
    assert ch.rank(scores, "1h", viable_tfs=["30m"]) == ["1h", "30m"]


def test_accepts_requires_margin_only_across_timeframes():
    assert ch.accepts(60.1, "1h", 60.0, "1h", 0.05)
    assert not ch.accepts(60.5, "2h", 60.0, "1h", 0.05)
    assert ch.accepts(61.1, "2h", 60.0, "1h", 0.05)
    assert ch.accepts(0.0, "1h", None, "1h", 0.05)


def test_switch_and_rotation():
    assert not ch.should_switch(ch.SWITCH_AFTER_STALE, ["1h"])
    assert not ch.should_switch(ch.SWITCH_AFTER_STALE - 1, ["1h", "2h"])
    assert ch.should_switch(ch.SWITCH_AFTER_STALE, ["1h", "2h"])
    assert ch.next_tf(["2h", "1h", "4h"], "2h") == "1h"
    assert ch.next_tf(["2h", "1h", "4h"], "4h") == "2h"
    assert ch.next_tf(["2h", "1h"], "8h") == "2h"


def test_seen_key_unchanged_without_chain():
    assert ch.seen_key("1h", "abc", False) == "abc"
    assert ch.seen_key("1h", "abc", True) == "1h|abc"


def test_viable_uses_holdout_evidence():
    assert not ch.viable(None)
    assert not ch.viable({"holdout_bars": 10})
    assert ch.viable({"holdout_bars": 500})


# ---------------- Rückwärtskompatibilität (Standard AUS) ----------------
def test_chain_off_loads_only_selected_timeframe_and_keeps_schema(monkeypatch):
    fetched, evaluated = _patch(monkeypatch, {"1h": 60.0, "4h": 90.0})
    job, res = _run({**BASE_BODY, "max_rounds": 5})
    assert fetched == ["1h"]
    assert {tf for tf, _ in evaluated} == {"1h"}
    assert res["timeframe"] == "1h"
    assert "tf_chain" not in res and "selected_timeframe" not in res
    assert "timeframe" not in res["best"] and "timeframe" not in res["baseline"]
    assert all("timeframe" not in h for h in res["history"])
    assert res["settings"]["tf_chain"] is False
    assert "tf_chain" not in job


def test_chain_off_is_deterministic_and_equal_to_explicit_false(monkeypatch):
    _patch(monkeypatch, {"1h": 60.0})
    _, a = _run({**BASE_BODY, "max_rounds": 8})
    _, b = _run({**BASE_BODY, "max_rounds": 8, "tf_chain": False})
    drop = ("created_at", "elapsed_seconds")
    assert {k: v for k, v in a.items() if k not in drop} == {k: v for k, v in b.items() if k not in drop}


def test_chain_off_no_model_still_raises_explained_error(monkeypatch):
    _patch(monkeypatch, {"1h": None})
    job_id = lab.create_job("autopilot", {})
    asyncio.run(ap.run_autopilot(job_id, {**BASE_BODY, "max_rounds": 2}, None))
    job = lab.JOBS[job_id]
    assert job["status"] == "error"
    assert job["error"].startswith("Ausgangs-Konfiguration liefert kein Modell")


# ---------------- Kette AN: Scoring + Fallback ----------------
def test_chain_scoring_picks_best_timeframe(monkeypatch):
    fetched, _ = _patch(monkeypatch, {"1h": 60.0, "30m": 55.0, "2h": 64.0, "4h": 70.0})
    job, res = _run({**BASE_BODY, "max_rounds": 3, "tf_chain": True})
    assert fetched == ["1h", "30m", "2h", "4h"]
    assert res["timeframe"] == "4h"
    assert res["selected_timeframe"] == "1h"
    assert res["best"]["timeframe"] == "4h"
    assert res["baseline"]["timeframe"] == "1h"         # Ausgangslage = gewählter TF
    assert res["baseline"]["score"] == pytest.approx(60.0)
    assert res["improved"] is True
    tc = res["tf_chain"]
    assert tc["order"][0] == "4h" and tc["changed_timeframe"] is True
    assert tc["per_tf"]["2h"]["start_score"] == pytest.approx(64.0)
    assert res["settings"]["tf_chain"] is True
    assert job["tf_chain"]["chain"] == ["1h", "30m", "2h", "4h"]


def test_chain_keeps_selected_timeframe_within_hysteresis(monkeypatch):
    _patch(monkeypatch, {"1h": 60.0, "30m": 59.0, "2h": 60.5, "4h": 60.9})
    _, res = _run({**BASE_BODY, "max_rounds": 3, "tf_chain": True})
    assert res["timeframe"] == "1h"
    assert res["tf_chain"]["changed_timeframe"] is False
    assert res["improved"] is False


def test_chain_fallback_switches_timeframe_when_stalled(monkeypatch):
    _, evaluated = _patch(monkeypatch, {"1h": 60.0, "30m": 50.0, "2h": 52.0, "4h": 54.0})
    _, res = _run({**BASE_BODY, "max_rounds": ch.SWITCH_AFTER_STALE * 3 + 10,
                   "plateau_rounds": 0, "tf_chain": True})
    tc = res["tf_chain"]
    assert tc["switches"] >= 2
    tested_tfs = {tf for tf, row in tc["per_tf"].items() if row["tested"]}
    assert len(tested_tfs) >= 3
    assert res["timeframe"] == "1h"                     # nirgends besser -> bleibt
    # die beste Erkennung wird je TF genau einmal bewertet (Duplikat-Cache je TF)
    best_key = ap.config_key(res["best_engine_config"])
    for tf in ("1h", "30m", "2h", "4h"):
        assert evaluated.count((tf, best_key)) == 1
    assert res["duplicates_skipped"] >= 1


def test_chain_skips_timeframes_without_data_or_evidence(monkeypatch):
    _patch(monkeypatch, {"1h": 60.0, "30m": 80.0, "2h": 90.0, "4h": 95.0},
           no_data=("4h",), holdout={"2h": 10})
    _, res = _run({**BASE_BODY, "max_rounds": 2, "tf_chain": True})
    tc = res["tf_chain"]
    assert tc["per_tf"]["4h"]["status"] == "no_data"
    assert tc["per_tf"]["2h"]["status"] == "weak_evidence"
    assert "2h" not in tc["order"] and "4h" not in tc["order"]
    assert res["timeframe"] == "30m"


def test_chain_uses_other_timeframe_when_selected_has_no_model(monkeypatch):
    _patch(monkeypatch, {"1h": None, "30m": 55.0, "2h": 58.0, "4h": None})
    _, res = _run({**BASE_BODY, "max_rounds": 2, "tf_chain": True})
    assert res["timeframe"] == "2h"
    assert res["baseline"]["timeframe"] == "2h"
    assert res["tf_chain"]["per_tf"]["1h"]["status"] == "no_model"


def test_chain_followup_analysis_uses_best_timeframe():
    res = {"improved": True, "adopt_recommended": True, "timeframe": "4h",
           "symbols": ["BTCUSDT"], "days": 360, "train_pct": 75,
           "best_engine_config": {"detector": "ema"}}
    body = ap.followup_analysis_body(res, {"timeframe": "1h"})
    assert body["timeframe"] == "4h"


# ---------------- API-/Worker-Verträge ----------------
def test_router_forwards_tf_chain_param():
    from routers import regime_lab as rl
    assert "tf_chain" in rl.AUTOPILOT_PARAM_KEYS


def test_local_worker_guard_requires_1_19(monkeypatch):
    import time as _t
    from services import local_exec as le
    monkeypatch.setattr(le, "WORKERS", {"w": {"version": "1.18.0", "last_seen": _t.time()}})
    assert le.worker_supports_autopilot() and not le.worker_supports_tf_chain()
    monkeypatch.setattr(le, "WORKERS", {"w": {"version": "1.19.0", "last_seen": _t.time()}})
    assert le.worker_supports_tf_chain()


def test_chain_end_to_end_with_real_engine(monkeypatch):
    """Echte Bewertung (evaluate_config + Regime-Engine) über die Kette 1h/30m/2h/4h."""
    import math
    import random
    from services.timeframes import aggregate_candles, tf_minutes

    rnd = random.Random(5)
    p, raw = 100.0, []
    for i in range(2 * 24 * 200):                       # 200 Tage 30m-Kerzen
        p *= math.exp(0.0006 * math.sin(i / 300.0) + rnd.gauss(0, 0.003))
        raw.append({"timestamp": i * 1800000, "open": p, "high": p * 1.002,
                    "low": p * 0.998, "close": p, "volume": 1.0})

    async def fake_fetch(symbols, days, timeframe, job=None, **kw):
        if tf_minutes(timeframe) == 30:
            return {"BTCUSDT": raw}
        return {"BTCUSDT": list(aggregate_candles(raw, timeframe, drop_partial=True))}

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    _, res = _run({**BASE_BODY, "days": 200, "max_rounds": 4, "tf_chain": True,
                   "search_detectors": False})
    tc = res["tf_chain"]
    assert tc["chain"] == ["1h", "30m", "2h", "4h"]
    assert res["timeframe"] in tc["order"]
    assert any(row["start_score"] is not None for row in tc["per_tf"].values())
    assert res["best"]["metrics"]
