"""Partial Pooling der Regime-Erkennung (10/2026): reine Regeln, Champion-Abschlag,
per_symbol_config in run_analysis und der Pooling-Job End-to-End (synthetische
Kerzen, Stub-DB – keine echte Datenbank)."""
import asyncio
import math

import numpy as np
import pytest

from services import regime_lab as lab
from services import regime_pooling as rp
from services import regime_selection as sel


def _doc(**kw):
    d = {"id": "ra_grp", "name": "Gruppe 1h", "timeframe": "1h", "days": 720,
         "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
         "settings": {"engine": "v2", "train_pct": 75, "engine_config": {"detector": "reactive"}},
         "combined": {"model": {"config": {"detector": "reactive", "rev_atr_mult": 3.0,
                                           "side_leg_atr_mult": 1.5, "adapt_applied": "standard"}}}}
    d.update(kw)
    return d


def test_source_check_accepts_group_and_lists_reasons():
    assert rp.source_check(_doc())["ok"]
    bad = rp.source_check(_doc(symbols=["BTCUSDT"], settings={"engine": "v2", "train_pct": 100}, combined={}))
    assert not bad["ok"] and len(bad["reasons"]) == 3
    reg = _doc()
    reg["combined"]["model"]["config"]["detector"] = "regression"
    assert any("Skalen-Schwelle" in r for r in rp.source_check(reg)["reasons"])
    pooled = _doc(settings={"engine": "v2", "train_pct": 75, "pooling": {"coins": {}}})
    assert any("Pooling-Ergebnis" in r for r in rp.source_check(pooled)["reasons"])
    w = rp.source_check(_doc(days=300, timeframe="15m"))["warnings"]
    assert len(w) == 2


def test_shrinkage_math():
    assert rp.shrink_weight(0) == 0.0
    assert rp.shrink_weight(40) == pytest.approx(0.5)
    assert rp.shrink_weight(120, prior=40) == pytest.approx(0.75)
    assert rp.pooled_factor(0.7, 0.0) == 1.0
    assert rp.pooled_factor(0.7, 1.0) == 0.7
    assert rp.pooled_factor(1.4, 0.5) == pytest.approx(round(math.sqrt(1.4), 3))


def test_choose_factor_requires_clear_gain():
    assert rp.choose_factor({0.7: 60.0, 1.0: 55.0, 1.4: 50.0}) == (0.7, 5.0)
    assert rp.choose_factor({0.7: 55.5, 1.0: 55.0}) == (1.0, 0.5)     # < 1 Pkt. = kein Beleg
    assert rp.choose_factor({0.7: 60.0, 1.0: None}) == (1.0, 0.0)     # Gruppe nicht messbar
    assert rp.choose_factor({0.85: 58.0, 1.2: 58.0, 1.0: 50.0})[0] == 0.85   # Gleichstand: näher an 1


def test_plan_coin_and_base_config_pin_structure():
    group = rp.group_scales(_doc())
    assert group == {"rev_atr_mult": 3.0, "side_leg_atr_mult": 1.5}
    row = rp.plan_coin("BTCUSDT", {0.7: 62.0, 0.85: 60.0, 1.0: 55.0, 1.2: 50.0, 1.4: 45.0}, 40, group, 40.0)
    assert row["k_best"] == 0.7 and row["weight"] == 0.5
    assert row["overrides"]["rev_atr_mult"] == pytest.approx(3.0 * row["k_pooled"], abs=1e-3)
    cfg = rp.base_config(_doc())
    assert cfg["adapt_profile"] == "standard" and cfg["detector"] == "reactive"


def test_analysis_body_is_per_coin_with_overrides():
    group = rp.group_scales(_doc())
    coins = {s: rp.plan_coin(s, {1.0: 50.0}, 10, group, 40.0) for s in ("BTCUSDT", "ETHUSDT")}
    body = rp.analysis_body(_doc(), rp.report(_doc(), coins, 40.0))
    assert body["scope"] == "per_coin" and body["symbols"] == ["BTCUSDT", "ETHUSDT"]
    assert body["per_symbol_config"]["BTCUSDT"] == {"rev_atr_mult": 3.0, "side_leg_atr_mult": 1.5}
    assert body["pooling"]["source_id"] == "ra_grp" and body["name"].endswith("· Pooling")


def test_selection_penalty_scales_with_pool_weight():
    base = {"holdout_f1": 60.0, "inner_f1": 60.0, "train_f1": 60.0, "holdout_bars": 5000,
            "bars_per_day": 24.0, "scope": "per_coin"}
    coin = sel.evaluate(dict(base))
    pooled = sel.evaluate({**base, "pooled": True, "pool_weight": 0.2})
    comb = sel.evaluate({**base, "scope": "combined"})
    assert comb["score"] - pooled["score"] == pytest.approx(0.3, abs=0.01)
    assert comb["score"] - coin["score"] == pytest.approx(sel.PER_COIN_PENALTY, abs=0.01)
    assert any("Pooling-Modell" in w for w in pooled["why"])


def test_candidate_rows_mark_pooled_per_coin():
    doc = {"id": "ra_p", "name": "P", "timeframe": "1h", "symbols": ["BTCUSDT"],
           "settings": {"pooling": {"coins": {"BTCUSDT": {"weight": 0.4}}}},
           "per_coin": {"BTCUSDT": {"reference": {"holdout_f1_pct": 55, "holdout_bars": 900}}}}
    rows = sel.candidate_rows(doc, "BTCUSDT", 24.0)
    assert rows[0]["scope"] == "per_coin" and rows[0]["pooled"] and rows[0]["pool_weight"] == 0.4
    plain = sel.candidate_rows({**doc, "settings": {}}, "BTCUSDT", 24.0)
    assert "pooled" not in plain[0]


def _candles(seed: int, n: int = 24 * 260):
    rng = np.random.default_rng(seed)
    drift = np.repeat(rng.choice([-1, 0, 1], size=n // 240 + 1), 240)[:n] * 0.0015
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, n)))
    t0 = 1_700_000_000_000
    return [{"timestamp": t0 + i * 3_600_000, "open": float(c), "high": float(c) * 1.003,
             "low": float(c) * 0.997, "close": float(c), "volume": 1000.0} for i, c in enumerate(close)]


class _Coll:
    def __init__(self, doc):
        self.doc, self.saved = doc, []

    async def find_one(self, q, proj=None):
        return self.doc if q.get("id") == self.doc["id"] else None


class _DB:
    def __init__(self, doc):
        self.regime_analyses = _Coll(doc)


def test_run_pooling_end_to_end(monkeypatch):
    hist = {s: _candles(i) for i, s in enumerate(("BTCUSDT", "ETHUSDT", "SOLUSDT"))}
    src = _doc(days=260)
    src["combined"]["model"]["config"].pop("adapt_applied")
    saved = {}

    async def fetch(symbols, days, tf, job, skipped=None):
        return {s: hist[s] for s in symbols}

    async def persist(db, doc):
        saved["doc"] = doc
    monkeypatch.setattr(lab, "fetch_histories", fetch)
    monkeypatch.setattr(lab, "persist_analysis", persist)
    monkeypatch.setattr(rp, "FACTORS", (0.7, 1.0, 1.4))
    jid = lab.create_job("pooling", {})
    asyncio.run(rp.run_pooling(jid, {"analysis_id": "ra_grp", "prior_phases": 40}, _DB(src)))
    job = lab.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    doc = saved["doc"]
    assert doc["scope"] == "per_coin" and doc["pooled_from"] == "ra_grp"
    rep = doc["settings"]["pooling"]
    assert set(rep["coins"]) == set(hist) and job["result"]["pooling"] == rep
    for sym, c in rep["coins"].items():
        assert 0.0 <= c["weight"] < 1.0 and 0.7 <= c["k_pooled"] <= 1.4
        used = doc["per_coin"][sym]["model"]["config"]["rev_atr_mult"]
        assert used == pytest.approx(3.0 * c["k_pooled"], abs=1e-3)   # Override wirkt im Coin-Modell


def test_run_pooling_rejects_unsuitable_source(monkeypatch):
    jid = lab.create_job("pooling", {})
    asyncio.run(rp.run_pooling(jid, {"analysis_id": "ra_grp"}, _DB(_doc(symbols=["BTCUSDT"]))))
    assert lab.JOBS[jid]["status"] == "error" and "mindestens 3" in lab.JOBS[jid]["error"]
