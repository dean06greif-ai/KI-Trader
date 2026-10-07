"""Asset-Korrelation (Regime-Lab): reine Rechnung, Job-Lauf mit synthetischen
Kerzen (ohne Netzwerk/DB), Copilot-Kontext und Router-Schutz."""
import asyncio
import math

import pytest

from services import asset_correlation as ac
from services import regime_lab as lab


def _rets_dirs(n=400):
    base = [((i * 7919) % 13 - 6) / 100 for i in range(n)]
    noise = [((i * 104729) % 11 - 5) / 100 for i in range(n)]
    rets = {"A": {i: base[i] for i in range(n)}, "B": {i: base[i] * 1.1 for i in range(n)},
            "C": {i: noise[i] for i in range(n)}}
    dirs = {"A": {i: (i // 50) % 3 for i in range(n)}, "B": {i: (i // 50) % 3 for i in range(n)},
            "C": {i: (i // 37) % 3 for i in range(n)}}
    return rets, dirs


def test_pairs_and_groups():
    rets, dirs = _rets_dirs()
    pairs = ac.pair_stats(rets, dirs)
    ab = next(p for p in pairs if {p["a"], p["b"]} == {"A", "B"})
    assert ab["ret_corr"] > 0.99 and ab["agree_pct"] == 100.0 and pairs[0] is ab
    groups = ac.group_assets(["A", "B", "C"], pairs)
    assert groups[0]["symbols"] == ["A", "B"] and ["C"] in [g["symbols"] for g in groups]
    assert "A/B" in ac.copilot_block({"pairs": pairs, "groups": groups, "timeframe": "1h", "days": 720})


def test_holdout_values_only_with_enough_candles():
    rets, dirs = _rets_dirs()
    pairs = ac.pair_stats(rets, dirs, {"A": 250, "B": 260, "C": 390})
    ab = next(p for p in pairs if {p["a"], p["b"]} == {"A", "B"})
    assert ab["n_holdout"] == 140 and ab["ret_corr_holdout"] > 0.99 and ab["agree_pct_holdout"] == 100.0
    ac_pair = next(p for p in pairs if {p["a"], p["b"]} == {"A", "C"})
    assert ac_pair["n_holdout"] == 10 and ac_pair["ret_corr_holdout"] is None
    assert all(p["ret_corr_holdout"] is None for p in ac.pair_stats(rets, dirs))


def test_too_little_overlap_is_skipped():
    rets = {"A": {i: 0.01 * (i % 3) for i in range(50)}, "B": {i: 0.02 * (i % 3) for i in range(50)}}
    assert ac.pair_stats(rets, {}) == []


def test_detector_config_forces_three_regimes():
    assert ac.detector_config("kmeans", {"detector": "ema"}) == {"detector": "reactive", "regime_mode": 3}
    cfg = ac.detector_config("v2", {"detector": "ema", "regime_mode": 9, "ema_regime_days": 5})
    assert cfg["detector"] == "ema" and cfg["regime_mode"] == 3 and cfg["ema_regime_days"] == 5
    assert ac.detector_config(None, {"detector": "quatsch"})["detector"] == "reactive"


def test_copilot_block_hint_and_stale_warning():
    assert "Korrelation berechnen" in ac.copilot_block(None)
    doc = {"pairs": [], "groups": [], "timeframe": "1h", "days": 720, "symbols": ["A", "B"]}
    assert "ACHTUNG" in ac.copilot_block(doc, {"timeframe": "4h", "days": 720})
    assert "ACHTUNG" not in ac.copilot_block(doc, {"timeframe": "1h", "days": 720})


def _candles(n, seed, drift_fn):
    out, p = [], 100.0
    for i in range(n):
        r = drift_fn(i) + 0.004 * math.sin(i * 0.7 + seed) + 0.002 * math.sin(i * 1.3 * seed)
        p *= math.exp(r)
        out.append({"timestamp": 1_700_000_000_000 + i * 3_600_000, "open": p, "high": p * 1.003,
                    "low": p * 0.997, "close": p, "volume": 1000 + (i % 7)})
    return out


def _wave(i):
    return 0.002 * (1 if (i // 300) % 2 == 0 else -1)


class _FakeColl:
    def __init__(self):
        self.doc = None

    async def replace_one(self, flt, doc, upsert=False):
        self.doc = doc

    async def find_one(self, flt):
        return dict(self.doc) if self.doc else None


class _FakeDb:
    def __init__(self):
        self.regime_correlation = _FakeColl()


def test_series_with_real_engine():
    rets, dirs = ac._series(_candles(1500, 1, _wave), "1h")
    assert len(rets) == 1499 and dirs and set(dirs.values()) <= {0, 1, 2}


def test_run_job_end_to_end(monkeypatch):
    data = {"AUSDT": _candles(1500, 1, _wave), "BUSDT": _candles(1500, 2, _wave),
            "CUSDT": _candles(1500, 3, lambda i: -_wave(i))}

    async def fake_fetch(symbols, days, tf, skipped=None, **kw):
        sym = symbols[0]
        if sym == "XUSDT":
            skipped[sym] = {"bars": 10, "reason": "zu wenig Daten"}
            return {}
        return {sym: data[sym]}

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    db = _FakeDb()
    jid = lab.create_job(ac.JOB_KIND, {})
    body = {"symbols": ["AUSDT", "BUSDT", "CUSDT", "XUSDT"], "timeframe": "1h", "days": 60, "train_pct": 70}
    asyncio.run(ac.run(jid, body, db))
    job = lab.JOBS[jid]
    assert job["status"] == "done", job.get("error")
    doc = db.regime_correlation.doc
    assert doc["symbols"] == ["AUSDT", "BUSDT", "CUSDT"] and doc["missing"] == ["XUSDT"]
    ab = next(p for p in doc["pairs"] if {p["a"], p["b"]} == {"AUSDT", "BUSDT"})
    acp = next(p for p in doc["pairs"] if {p["a"], p["b"]} == {"AUSDT", "CUSDT"})
    assert ab["ret_corr"] > acp["ret_corr"] and ab["n_holdout"] >= ac.MIN_HOLDOUT
    st = ac.job_state(lab.JOBS)
    assert st["id"] == jid and st["running"] is False
    assert asyncio.run(ac.latest(db))["timeframe"] == "1h"


def test_run_job_cancel_and_error(monkeypatch):
    async def fake_fetch(symbols, days, tf, skipped=None, **kw):
        return {}

    monkeypatch.setattr(lab, "fetch_histories", fake_fetch)
    jid = lab.create_job(ac.JOB_KIND, {})
    asyncio.run(ac.run(jid, {"symbols": ["AUSDT", "BUSDT"]}, _FakeDb()))
    assert lab.JOBS[jid]["status"] == "error" and "mind. 2" in lab.JOBS[jid]["error"]
    jid2 = lab.create_job(ac.JOB_KIND, {})
    lab.JOBS[jid2]["cancel"] = True
    asyncio.run(ac.run(jid2, {"symbols": ["AUSDT", "BUSDT"]}, _FakeDb()))
    assert lab.JOBS[jid2]["status"] == "cancelled"


def test_router_guards(monkeypatch):
    from fastapi import HTTPException
    from routers import regime_lab as rl
    with pytest.raises(HTTPException) as e:
        asyncio.run(rl.start_correlation({"symbols": ["AUSDT"]}, True))
    assert e.value.status_code == 400
    jid = lab.create_job("analysis", {})
    try:
        with pytest.raises(HTTPException) as e:
            asyncio.run(rl.start_correlation({"symbols": ["AUSDT", "BUSDT"]}, True))
        assert e.value.status_code == 409
    finally:
        lab.JOBS[jid]["status"] = "done"
