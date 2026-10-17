"""Regressionstests 12.06: RAM-Deckel Optimizer/FastSeries, dotenv optional,
Dynamik-Werkbank (Variations-Runden statt Leerlauf, Overfitting-Schutz,
Ausreißer-/Edge-Coins, Beste Strategie je Phase, Empfehlungen)."""
import asyncio
import importlib
import sys

import numpy as np

from services import array_lru, dynamic_advisor as adv, dynamic_matcher as dm
from services import dynamic_workbench as wb
from services import indicator_cache, optimizer, regime_outliers as ro


# ---------------- RAM-Deckel ----------------
def test_array_lru_global_budget_evicts_oldest():
    array_lru.set_budget_mb(1)
    try:
        a, b = array_lru.ArrayLRU(), array_lru.ArrayLRU()
        for i in range(6):
            a[("x", i)] = np.zeros(40_000)       # 320 KB
        for i in range(6):
            b[("y", i)] = np.zeros(40_000)
        assert array_lru.stats()["mb"] <= 1.6
        assert ("y", 5) in b and ("x", 0) not in a
    finally:
        array_lru.set_budget_mb(512)


def test_array_lru_keeps_newest_entries_of_same_series():
    array_lru.set_budget_mb(1)
    try:
        c = array_lru.ArrayLRU()
        for i in range(4):
            c[("big", i)] = np.zeros(200_000)    # 1.6 MB je Serie > Budget
        assert all(("big", i) in c for i in range(4))
    finally:
        array_lru.set_budget_mb(512)


def test_fast_series_results_identical_after_eviction():
    from services.fast_sim import FastSeries
    candles = [{"open": 1 + i * .01, "high": 1.1 + i * .01, "low": .9 + i * .01, "close": 1 + i * .01,
                "volume": 10, "timestamp": 1_700_000_000_000 + i * 60_000} for i in range(500)]
    fs = FastSeries(candles)
    ref = fs.get("rsi", {"rsi_period": 14}).copy()
    array_lru.set_budget_mb(1)
    try:
        for p in range(5, 400):
            fs.get("rsi", {"rsi_period": p})
        assert np.allclose(np.nan_to_num(fs.get("rsi", {"rsi_period": 14})), np.nan_to_num(ref))
    finally:
        array_lru.set_budget_mb(512)


def test_indicator_cache_mem_byte_cap(monkeypatch):
    monkeypatch.setattr(indicator_cache, "_DISK_ENABLED", False)
    monkeypatch.setattr(indicator_cache, "_MEM_MAX_BYTES", 1_000_000)
    indicator_cache.clear()
    for i in range(20):
        indicator_cache.put("fp", ("k", i), np.zeros(25_000))   # 200 KB
    assert indicator_cache._MEM_BYTES <= 1_000_000 and len(indicator_cache._MEM) <= 5
    indicator_cache.clear()


def test_optimizer_trim_keeps_best():
    results = [{"score": float(i)} for i in range(1000)]
    history = [{"flat": {}, "score": float(i)} for i in range(5000)]
    optimizer._trim_search_state(results, history)
    assert len(results) == optimizer.RESULTS_KEEP and results[0]["score"] == 999
    assert len(history) <= optimizer.TPE_HISTORY_MAX and max(h["score"] for h in history) == 4999


def test_config_without_dotenv(monkeypatch):
    monkeypatch.setitem(sys.modules, "dotenv", None)   # ImportError beim Import
    import core.config as cfg
    importlib.reload(cfg)
    assert cfg.load_dotenv() is False
    monkeypatch.undo()
    importlib.reload(cfg)


def test_worker_requirements_ship_dotenv():
    from pathlib import Path
    req = (Path(__file__).resolve().parents[2] / "local_worker" / "requirements.txt").read_text()
    assert "python-dotenv" in req


# ---------------- Overfitting / Coins ----------------
def test_overfit_and_sufficiency():
    m, v = {"pnl": 100, "trades": 20}, {"pnl": 2, "trades": 10}
    assert ro.overfit_gap(m, v, 10)
    assert not ro.overfit_gap(m, {"pnl": 40, "trades": 10}, 10)
    assert not ro.overfit_gap(m, {"pnl": 1, "trades": 2}, 10)   # zu wenig Test-Trades: keine Aussage
    assert ro.trade_sufficient({"metrics": m, "validation": v}, 10)
    assert not ro.trade_sufficient({"metrics": m, "validation": {"trades": 2}}, 10)
    assert "few_test_trades" in ro.candidate_flags({"metrics": m, "validation": {"trades": 2, "pnl": 1}}, 10)


def test_coin_edge_report():
    train = {"A": {"pnl": 50, "trades": 10}, "B": {"pnl": -20, "trades": 8},
             "C": {"pnl": 30, "trades": 9}, "D": {"pnl": 25, "trades": 7}}
    test = {"A": {"pnl": 10, "trades": 4}, "C": {"pnl": -5, "trades": 3}}
    rep = ro.coin_edge_report(train, test)
    assert set(rep["no_edge"]) == {"B", "C"} and "A" in rep["edge"]
    assert ro.coin_edge_report({}, {}) is None


def test_candidate_rank_prefers_sufficient_not_overfit():
    base = {"validation_passed": True, "metrics": {"pnl": 10, "trades": 20}}
    few = {**base, "score": 99, "trade_sufficient": False}
    over = {**base, "score": 98, "overfit": True}
    good = {**base, "score": 10}
    assert wb.candidate_rank(good) > wb.candidate_rank(over) > wb.candidate_rank(few)
    assert wb.candidate_rank({**base, "score": 10}) == wb.candidate_rank(good)   # alte Kandidaten unverändert


# ---------------- Endlos-Suche ----------------
def test_plan_round_variation_instead_of_idle():
    states = {0: {"stale": 2}, 1: {"stale": 3}, 2: {"dead": True}}
    assert wb.plan_round(states, 5) == ([0, 1], True)
    assert wb.plan_round(states, 4) == ([0, 1], False)          # reguläre 4.-Runde
    assert wb.plan_round({0: {"stale": 0}, 1: {"stale": 2}}, 5) == ([0], False)
    assert wb.plan_round({0: {"dead": True}}, 1) == ([], False)


def test_search_body_variants_and_tf_filters():
    p = {"iterations": 40, "_tf_filters": ["1h", "4h"]}
    b1 = wb._search_body(p, "a", 0, "discovery", None, 1)
    b2 = wb._search_body(p, "a", 0, "discovery", None, 2)
    assert "tf_options" not in b1 and b2["tf_options"] == ["1h", "4h"]
    assert b1["max_rules"] in wb.RULE_DEPTHS
    assert "tf_options" not in wb._search_body(p, "a", 0, "params", "s", 2)
    simple = wb._search_body(p, "a", 0, "params", "s", 3, variant=1)
    assert simple["max_rules"] == 2 and simple["iterations"] > b1["iterations"]
    wide = wb._search_body(p, "a", 0, "discovery", None, 3, variant=2)
    assert wide["deep_test"] is True


class _Coll:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, q, proj=None):
        return dict(self.doc)

    async def update_one(self, q, upd):
        self.doc.update(upd["$set"])


class _DB:
    def __init__(self, doc):
        self.regime_analyses = _Coll(doc)


def _analysis():
    return {"id": "ra1", "symbols": ["BTCUSDT"], "days": 30, "timeframe": "1h", "settings": {"train_pct": 100},
            "combined": {"model": {"regimes": [{"id": 0, "label": "Bulle"}]}}}


def test_endless_stops_after_fruitless_variation_rounds(monkeypatch):
    calls = []

    async def fake_run_lab(job, kind_fn, body, analysis_doc, deps):
        calls.append(body)
        return {"mode": "discovery", "definition": {"rules": []},
                "top5": [{"score": 5.0, "validation_passed": False, "metrics": {"pnl": 1, "trades": 20}}]}

    async def fake_build(aid, body):
        return {"id": "dyn_x"}

    monkeypatch.setattr(wb, "_run_lab", fake_run_lab)
    p = {"analysis_id": "ra1", "scope": "combined", "endless": True, "walkforward": False,
         "result_backtest": False, "targets": {"0": {"mode": "discovery"}}}
    jid = wb.create_job("discover", p)
    asyncio.run(wb.run(jid, p, {"db": _DB(_analysis()), "registry": None, "build": fake_build}))
    job = wb.JOBS[jid]
    assert job["status"] == "done"
    # 1 Verbesserung + 2 Plateau-Runden + 3 Variations-Runden (nicht 10.000 Runden Leerlauf)
    assert len(calls) <= 8 and job["round"] <= 8
    assert any("Variations-Runde" in e["msg"] for e in job["log"])


class _Strat:
    def __init__(self, sid):
        self.STRATEGY_ID, self.STRATEGY_NAME = sid, sid.upper()


class _Reg:
    def get(self, sid):
        return _Strat(sid) if sid in ("s1", "s2", "s3") else None


def test_match_compares_then_refines(monkeypatch):
    seen = []

    async def fake_run_lab(job, kind_fn, body, analysis_doc, deps):
        sid = body.get("strategy_id")
        seen.append((sid, body.get("optimize"), body.get("iterations")))
        pnl = {"s1": 5, "s2": 50, "s3": -10}[sid]
        top = {"score": float(pnl), "validation_passed": pnl > 0, "trade_sufficient": True,
               "metrics": {"pnl": pnl, "trades": 25}, "validation": {"pnl": pnl / 2, "trades": 8},
               "coin_report": {"no_edge": ["ETHUSDT"], "edge": ["BTCUSDT"], "outlier_assets": []}}
        return {"mode": "params", "strategy_id": sid, "strategy_name": sid.upper(), "top5": [top]}

    async def fake_build(aid, body):
        return {"id": "dyn_m"}

    monkeypatch.setattr(wb, "_run_lab", fake_run_lab)
    p = {"analysis_id": "ra1", "scope": "combined", "rounds": 1, "iterations": 20, "walkforward": False,
         "result_backtest": False, "targets": {"0": {"mode": "params", "candidates": ["s1", "s2", "s3"]}}}
    jid = wb.create_job("match", p)
    asyncio.run(wb.run(jid, p, {"db": _DB(_analysis()), "registry": _Reg(), "build": fake_build}))
    job = wb.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    comp = job["regimes"]["0"]["comparison"]
    assert comp[0]["strategy_id"] == "s2" and len(comp) == 3
    assert job["regimes"]["0"]["winner"] == "S2"
    # Vergleich ohne Suche, danach Anpassung des Finalisten mit Parametersuche
    assert seen[0][1] == {"tpsl": False}
    assert seen[-1][0] in ("s2", "s1") and seen[-1][1] != {"tpsl": False}


def test_pick_finalists_needs_trades():
    rank = wb.candidate_rank
    sc = [("a", {"score": 1, "metrics": {"trades": 0}}), ("b", {"score": 2, "metrics": {"trades": 5}})]
    assert [s for s, _ in dm.pick_finalists(sc, rank)] == ["b"]


# ---------------- Empfehlungen ----------------
def test_timeframe_advice():
    assert adv.higher_tf_options("15m") == ["1h", "4h"]
    assert adv.recommended_strategy_tf("1h") == "15m"
    assert adv.recommended_strategy_tf("15m") == "5m"
    assert adv.recommended_strategy_tf("4h") == "1h"
    a = adv.timeframe_advice("1h", "4h")
    assert a["recommended"] == "15m" and a["notes"]


def test_analysis_suitability_flags_unsuitable():
    one = {"id": "x", "combined": {"model": {"regimes": [{"id": 0}]}}, "settings": {"train_pct": 70}}
    r = adv.analysis_suitability(one)
    assert r["verdict"] == "ungeeignet" and not r["suitable"]
    good = {"id": "y", "timeframe": "1h", "settings": {"train_pct": 70},
            "combined": {"model": {"regimes": [{"id": 0}, {"id": 1}]}}}
    q = {"overall": {"grade": "gut", "avg_segment_days": 7}}
    r2 = adv.analysis_suitability(good, q, True)
    assert r2["suitable"] and r2["score"] > r["score"]
    nohold = adv.analysis_suitability({**good, "settings": {"train_pct": 100}}, q, None)
    assert nohold["score"] < r2["score"] and any("Holdout" in x["text"] for x in nohold["reasons"])
    ranked = adv.rank_analyses([r, r2])
    assert ranked[0]["id"] == "y" and ranked[0]["recommended"] and not ranked[1]["recommended"]


def test_apply_edge_history_marks_no_edge_coins():
    res = {"recommended": ["A", "B", "C"], "per_regime": [{"regime_id": 0, "label": "Bulle", "assets": [
        {"symbol": "A", "pick": True, "reasons": []}, {"symbol": "B", "pick": True, "reasons": []},
        {"symbol": "C", "pick": True, "reasons": []}]}]}
    hist = adv.edge_history([{"regime_id": 0, "top5": [{"coin_report": {"no_edge": ["B"], "edge": ["A"]}}]},
                             {"regime_id": 0, "top5": [{"coin_report": {"no_edge": ["B"], "edge": []}}]}])
    out = adv.apply_edge_history(res, hist, ["C"])
    assert out["recommended"] == ["A"]
    assert {a["symbol"] for a in out["avoid"]} == {"B", "C"}
    assert out["per_regime"][0]["picks"] == ["A", "C"]


# ---------------- regime_opt Ende-zu-Ende (synthetische Kerzen) ----------------
def _candles(n, seed):
    rng = np.random.default_rng(seed)
    px, out = 100.0, []
    for i in range(n):
        o = px
        px = max(1.0, px * (1 + rng.normal(0, 0.004)))
        out.append({"open": o, "high": max(o, px) * 1.002, "low": min(o, px) * 0.998, "close": px,
                    "volume": 100 + float(rng.integers(0, 50)), "timestamp": 1_700_000_000_000 + i * 900_000})
    return out


def test_regime_opt_coin_report_and_tf_filters(monkeypatch):
    from services import dynamic_strategy as dyn
    from services import regime_lab as lab
    from services import regime_opt
    from strategies.registry import registry
    syms = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    hist = {s: _candles(1500, i) for i, s in enumerate(syms)}
    labels = {s: ([0] * 250 + [1] * 250) * 3 for s in syms}
    segs = dyn.build_segments(hist, labels)

    async def fake_segments(*a, **k):
        return segs

    monkeypatch.setattr(regime_opt, "_build_regime_segments", fake_segments)
    monkeypatch.setattr(lab, "model_for", lambda d, s, y: {"regimes": [{"id": 0, "label": "A"}, {"id": 1, "label": "B"}]})
    doc = {"id": "ra", "symbols": syms, "timeframe": "15m", "days": 15, "settings": {"train_pct": 100}}
    jid = lab.create_job("regime_opt", {})
    body = {"analysis_doc": doc, "regime_id": 0, "mode": "discovery", "iterations": 3, "min_trades": 3,
            "max_rules": 2, "indicators": ["rsi", "ema_fast"], "tf_options": ["1h", "bogus"],
            "timeframe": "15m", "regime_train_pct": 70}
    from services.bitunix_trade import DEFAULT_COIN_CFG
    asyncio.run(regime_opt.run_regime_optimizer(jid, body, registry, {}, dict(DEFAULT_COIN_CFG), None))
    job = lab.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    res = job["result"]
    assert res["tf_options"] == ["1h"]
    assert res["top5"] and any(e.get("coin_report") for e in res["top5"])
    for e in res["top5"]:
        assert "trade_sufficient" in e and "overfit" in e and "_val_ps" not in e
        if e.get("coin_report"):
            assert {r["symbol"] for r in e["coin_report"]["symbols"]} <= set(syms)


def test_regime_presence_prefers_live_segments():
    from services.asset_suggest import regime_presence
    ps = {"BTC": {"segments": [{"regime": 0, "bars": 10}], "live_segments": [{"regime": 3, "bars": 5}]},
          "ETH": {"segments": [{"regime": 2, "bars": 4}]}}
    out = regime_presence(ps)
    assert set(out["BTC"]) == {3} and set(out["ETH"]) == {2}


def test_weak_detection_never_recommended():
    doc = {"id": "w", "timeframe": "1h", "settings": {"train_pct": 70},
           "combined": {"model": {"regimes": [{"id": 0}, {"id": 1}]}}}
    r = adv.analysis_suitability(doc, {"overall": {"grade": "schwach", "avg_segment_days": 7}}, True)
    assert r["verdict"] == "bedingt" and not adv.rank_analyses([r])[0]["recommended"]


def test_rank_overfit_does_not_favor_losers():
    loser = {"validation_passed": False, "score": -56.7, "metrics": {"pnl": -70.9, "trades": 10},
             "validation": {"pnl": -48.3, "trades": 6}}
    trained = {"validation_passed": False, "score": 48.3, "overfit": True, "metrics": {"pnl": 43.35, "trades": 12},
               "validation": {"pnl": -21.17, "trades": 6}}
    assert wb.candidate_rank(trained) > wb.candidate_rank(loser)


def test_int_ids_rejects_garbage():
    import pytest
    from fastapi import HTTPException
    from routers import dynamic
    assert dynamic._int_ids(["1", 2]) == [1, 2]
    with pytest.raises(HTTPException) as e:
        dynamic._int_ids(["x"])
    assert e.value.status_code == 400


def test_match_all_losers_gives_no_improvement(monkeypatch):
    async def fake_run_lab(job, kind_fn, body, analysis_doc, deps):
        sid = body.get("strategy_id")
        return {"mode": "params", "strategy_id": sid, "strategy_name": sid, "top5": [
            {"score": -5.0, "validation_passed": False, "metrics": {"pnl": -5, "trades": 20},
             "validation": {"pnl": -3, "trades": 8}}]}

    built = []

    async def fake_build(aid, body):
        built.append(body)
        return {"id": "dyn_l"}

    monkeypatch.setattr(wb, "_run_lab", fake_run_lab)
    p = {"analysis_id": "ra1", "scope": "combined", "rounds": 1, "iterations": 20, "walkforward": False,
         "result_backtest": False, "targets": {"0": {"mode": "params", "candidates": ["s1", "s2"]}}}
    jid = wb.create_job("match", p)
    asyncio.run(wb.run(jid, p, {"db": _DB(_analysis()), "registry": _Reg(), "build": fake_build}))
    job = wb.JOBS[jid]
    assert job["status"] == "done" and job["result"]["no_improvement"] and not built
    assert "kein Edge" in job["regimes"]["0"]["edge_note"]
