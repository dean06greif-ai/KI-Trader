"""Regressionstests 30.09.2026 (Prüfbericht Regime-Lab ↔ dynamische Strategien):
Live-Historie = Detektor-Warmup, Regime-Suche auf Live-Abschnitten, Walk-Forward
ohne Trades in unbelegten Regimen, expliziter Multi-Modus beim Bau, gedrosselte
Log-Meldungen (Manifest/Dukascopy) und ruhiger Worker-Verbindungslog.
Rein (ohne DB/Netz)."""
import asyncio
import importlib
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from services import dynamic_live, regime_engine as eng, regime_lab as lab
from services import regime_opt, strategy_plan
from services import history_sources as hs
from strategies.registry import registry

KOMBI_9 = {"engine": "v2", "timeframe": "1h", "regimes": [{"id": i, "label": f"R{i}"} for i in range(3)],
           "config": {**eng.resolve_config({"detector": "kombi", "regime_mode": 9}, "1h")}}


# ---------------- 1. Live-Historie ----------------
def test_history_days_covers_detector_warmup():
    need = eng.required_history_days(KOMBI_9["config"], 30)
    assert need > 30, "kombi/9er braucht deutlich mehr als 30 Tage Warmup"
    assert dynamic_live.history_days(KOMBI_9, 30) == need
    assert dynamic_live.history_days(KOMBI_9, 400) == eng.required_history_days(KOMBI_9["config"], 400)


def test_history_days_legacy_kmeans_unchanged():
    assert dynamic_live.history_days({"centroids": [[0, 0, 0, 0]]}, 30) == 30


# ---------------- 2. Regime-Suche: Segment-Basis ----------------
def _doc(live=True, detector="kombi"):
    seg_final = [{"regime": 1, "from_ts": 1000, "to_ts": 5000, "end_exclusive_ts": 5001}]
    seg_live = [{"regime": 1, "from_ts": 3000, "to_ts": 6000, "end_exclusive_ts": 6001}]
    entry = {"segments": seg_final, **({"live_segments": seg_live} if live else {})}
    return {"id": "ra_x", "combined": {"model": {"config": {"detector": detector}},
                                       "per_symbol": {"BTCUSDT": entry}},
            "per_coin": {"BTCUSDT": dict(entry)}, "bounds": {"BTCUSDT": {"train_end_ts": 5500}}}


def test_regime_ranges_live_reads_live_segments_and_cuts_holdout():
    r = lab.regime_ranges(_doc(), "combined", None, "BTCUSDT", 1, True, "live")
    assert r == [{"from_ts": 3000, "to_ts": 5500, "end_exclusive_ts": 5501}]
    r_final = lab.regime_ranges(_doc(), "combined", None, "BTCUSDT", 1, True)
    assert r_final[0]["from_ts"] == 1000   # Standard-Aufruf unverändert
    r_pc = lab.regime_ranges(_doc(), "per_coin", "BTCUSDT", "BTCUSDT", 1, False, "live")
    assert r_pc[0]["from_ts"] == 3000


def test_regime_ranges_live_falls_back_without_live_view():
    r = lab.regime_ranges(_doc(live=False), "combined", None, "BTCUSDT", 1, True, "live")
    assert r[0]["from_ts"] == 1000


@pytest.mark.parametrize("body,live,det,expect", [
    ({}, True, "kombi", ("live", "causal_live")),
    ({"label_basis": "final"}, True, "kombi", ("final", "retrospective_reference")),
    ({}, False, "regression", ("final", "causal_live")),
    ({}, False, "kombi", ("final", "retrospective_reference")),
])
def test_label_basis_of(body, live, det, expect):
    assert regime_opt.label_basis_of(body, _doc(live, det), "combined", None) == expect


# ---------------- 3. Walk-Forward: keine Trades in unbelegten Regimen ----------------
def _candles(n=600):
    return [{"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": 100.0, "high": 101.0,
             "low": 99.0, "close": 100.0, "volume": 1.0} for i in range(n)]


def test_walkforward_skips_unassigned_regimes(monkeypatch):
    cs = _candles()
    train_end = cs[299]["timestamp"]
    doc = {"id": "ra_wf", "timeframe": "1h", "days": 30, "symbols": ["BTCUSDT"],
           "combined": {"model": {"regimes": [{"id": 0}, {"id": 1}, {"id": 2}]}},
           "bounds": {"BTCUSDT": {"train_end_ts": train_end}},
           "settings": {"train_pct": 50},
           "assignments": {"combined:0": {"regime_id": 0, "strategy_id": "nnfx_trend",
                                          "regime_label": "R0"}}}

    async def fake_hist(*a, **k):
        return {"BTCUSDT": cs}
    seen = []

    async def fake_eval(strategy, segments, *a, **k):
        seen.append({s["regime"] for ss in segments.values() for s in ss})
        return {"trades": 0, "pnl": 0.0, "win_rate": 0.0}, []
    labels = [(i // 50) % 3 for i in range(len(cs))]
    monkeypatch.setattr(lab, "fetch_histories", fake_hist)
    monkeypatch.setattr(regime_opt.rg, "classify_series", lambda *a, **k: labels)
    monkeypatch.setattr(regime_opt.dyn, "eval_dynamic", fake_eval)
    lab.JOBS["wf_t"] = {"id": "wf_t", "status": "running", "cancel": False}
    try:
        asyncio.run(regime_opt.run_walkforward("wf_t", {"analysis_doc": doc, "scope": "combined"},
                                               registry, {}, {"max_capital": 100}, None))
        job = lab.JOBS["wf_t"]
        assert job["status"] == "done", job.get("error")
        assert seen[0] == {0}, "dynamischer Test darf nur belegte Regime handeln"
        assert job["result"]["untraded_bars"] > 0
        assert job["result"]["switches"] > 0   # Wechsel zählen weiterhin alle Regime
    finally:
        lab.JOBS.pop("wf_t", None)


# ---------------- 3b. Dynamischer Backtest: Detektor-Warmup vor dem Fenster ----------------
def test_simulate_dynamic_loads_warmup_and_trades_only_in_window(monkeypatch):
    from services import dynamic_backtest as db_
    model = {**KOMBI_9}
    warm = db_._regime_warmup_days(model)
    assert warm > 30
    cs = _candles(24 * (30 + warm) + 50)
    asked = {}

    async def fake_hist(symbols, days, tf, job, **k):
        asked["days"] = days
        return {"BTCUSDT": cs}
    seen_labels = {}

    def fake_classify(m, candles, *a, **k):
        seen_labels["n"] = len(candles)
        return [0] * len(candles)
    captured = {}

    async def fake_eval(strategy, segments, *a, **k):
        captured.setdefault("segs", segments)
        return {"trades": 0, "pnl": 0.0, "win_rate": 0.0}, []
    monkeypatch.setattr(db_.lab, "fetch_histories", fake_hist)
    monkeypatch.setattr(db_.rg, "classify_series", fake_classify)
    monkeypatch.setattr(db_.dyn, "eval_dynamic", fake_eval)
    doc = {"id": "dyn_w", "name": "W", "timeframe": "1h", "model": model,
           "regime_strategies": {"0": "nnfx_trend"}, "settings": {}}
    res = asyncio.run(db_.simulate_dynamic(doc, ["BTCUSDT"], 30, {"max_capital": 100}, {},
                                           registry, {}, lambda: False, with_alternatives=False))
    assert asked["days"] == 30 + warm
    assert seen_labels["n"] == len(cs), "Regime auf voller (eingeschwungener) Historie"
    seg = captured["segs"]["BTCUSDT"][0]
    assert seg["start_ts"] >= cs[-1]["timestamp"] - 30 * 86_400_000
    assert res["per_pair"][0]["candles"] <= 30 * 24 + 1


# ---------------- 4. Bau: expliziter Multi-Modus ----------------
def test_regime_strategy_map_mixed_assignments():
    assignments = {0: {"strategy_id": "nnfx_trend"},                        # Registry
                   1: {"strategy_id": "custom_x", "definition": {"long_rules": []}},  # eigene Regeln
                   2: {"strategy_id": None}}                                # Basis-Parameter
    m = strategy_plan.regime_strategy_map(assignments, "custom_base", registry)
    assert m == {"0": "nnfx_trend", "1": "custom_base", "2": "custom_base"}


def test_mixed_doc_unmapped_regime_does_not_trade_and_registry_regime_keeps_strategy():
    doc = {"id": "dyn_mx", "strategy_id": "custom_base",
           "regime_strategies": {"0": "nnfx_trend", "1": "custom_base"},
           "sub_strategies": {"1": {"rules": ["r"], "definition": {"long_rules": [{"a": 1}]}}}}
    p0 = strategy_plan.resolve_symbol_plan(doc, "BTCUSDT", {"regime": 0})
    p1 = strategy_plan.resolve_symbol_plan(doc, "BTCUSDT", {"regime": 1})
    p2 = strategy_plan.resolve_symbol_plan(doc, "BTCUSDT", {"regime": 2})
    assert p0["strategy_id"] == "nnfx_trend" and not p0["unmapped"]
    assert p1["strategy_id"] == "custom_base" and p1["rules_override"]["long_rules"] == [{"a": 1}]
    assert p2["unmapped"] is True


# ---------------- 5. Log-Drosselung ----------------
def test_unrepairable_manifest_logged_once_per_ttl():
    lab._UNREPAIRABLE.clear()
    want = {"start_ts": 1, "end_ts": 2, "bars": 8759}
    prob = "HYPEUSDT: Kerzenanzahl 8644 ≠ Manifest 8759"
    assert not lab._known_unrepairable("HYPEUSDT", want, prob)
    assert lab._remember_unrepairable("HYPEUSDT", want, prob) is True
    assert lab._known_unrepairable("HYPEUSDT", want, prob)
    assert lab._remember_unrepairable("HYPEUSDT", want, prob) is False
    # anderer Befund (z.B. nach Auto-Update mehr Kerzen) -> neu prüfen/melden
    assert not lab._known_unrepairable("HYPEUSDT", want, prob.replace("8644", "8700"))
    lab._UNREPAIRABLE.clear()


def test_verify_or_repair_skips_known_unrepairable(monkeypatch):
    from services import candle_cache, research_dataset as rd
    lab._UNREPAIRABLE.clear()
    calls = []

    async def fake_repair(*a, **k):
        calls.append(1)
        return 0
    monkeypatch.setattr(candle_cache, "repair_gaps", fake_repair)
    monkeypatch.setattr(rd, "verify_symbol", lambda s, h, w: "abweichend" if s == "HYPEUSDT" else None)
    monkeypatch.setattr(rd, "min_verified", lambda n: 1)
    ds = {"per_symbol": {"BTCUSDT": {"bars": 1}, "HYPEUSDT": {"bars": 2, "start_ts": 0, "end_ts": 0}}}
    for _ in range(3):
        hist = {"BTCUSDT": [1], "HYPEUSDT": [1]}
        job = {}
        asyncio.run(lab._verify_or_repair(None, hist, ds, job, None, 3_600_000))
        assert "HYPEUSDT" not in hist and job["dataset_excluded"][0]["symbol"] == "HYPEUSDT"
    assert len(calls) == 1, "Reparatur nur beim ersten Job, danach gemerkt"
    lab._UNREPAIRABLE.clear()


def test_dukascopy_stop_log_throttled(caplog):
    hs._DUKA_LOGGED.clear()
    day = datetime(2025, 10, 1, tzinfo=timezone.utc)
    assert hs._log_duka_stop("XAGUSD", day, "HTTP 503 (Ratelimit)", now=1000.0) is True
    assert hs._log_duka_stop("XAGUSD", day, "TimeoutError", now=2000.0) is False
    assert hs._log_duka_stop("XAGUSD", day, "HTTP 503", now=1000.0 + hs.DUKA_LOG_TTL_S + 1) is True
    assert hs._log_duka_stop("EURUSD", day, "HTTP 503", now=2000.0) is True
    hs._DUKA_LOGGED.clear()


# ---------------- 6. Worker: ein Verbindungszustand, Gnadenfrist ----------------
@pytest.fixture
def worker_mod(tmp_path, monkeypatch):
    wdir = Path(__file__).resolve().parents[2] / "local_worker"
    cfg = wdir / "worker_config.json"
    existed = cfg.exists()
    monkeypatch.setenv("WORKER_SERVER_URL", "http://localhost:1")
    monkeypatch.setenv("WORKER_TOKEN", "t")
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["worker.py", "--data-dir", str(tmp_path / "data")])
    monkeypatch.syspath_prepend(str(wdir))
    sys.modules.pop("worker", None)
    mod = importlib.import_module("worker")
    yield mod
    sys.modules.pop("worker", None)
    if not existed and cfg.exists():
        os.remove(cfg)


def test_worker_short_outage_not_logged(worker_mod):
    lines = []
    c = worker_mod.Connection(grace_s=20, logger=lines.append, running=lambda: True)
    c.fail(Exception("NameResolutionError getaddrinfo failed"), now=0)
    c.fail(Exception("x"), now=7)
    c.ok(now=7)
    assert lines == []


def test_worker_long_outage_logged_once_then_reconnect(worker_mod):
    lines = []
    c = worker_mod.Connection(grace_s=20, logger=lines.append, running=lambda: True)
    for t in (0, 2, 4, 25, 27, 60):          # Poll + Fortschritts-Threads melden alle
        c.fail(Exception("Max retries exceeded (Caused by NameResolutionError)"), now=t)
    c.ok(now=75)
    c.ok(now=76)                             # zweiter Thread: keine doppelte Zeile
    assert len(lines) == 3
    assert "DNS" in lines[0] and "nicht erreichbar" in lines[0]
    assert "rechnen weiter" in lines[1]
    assert lines[2].endswith("(nach 75 s)")


def test_worker_short_error_texts(worker_mod):
    se = worker_mod.short_error
    assert "DNS" in se(Exception("Failed to resolve (getaddrinfo failed)"))
    assert se(Exception("Connection to x timed out. (connect timeout=30)")) == "Zeitüberschreitung"
    assert se(Exception("('Connection aborted.', ConnectionResetError(10054))")) == \
        "Verbindung vom Server getrennt"
