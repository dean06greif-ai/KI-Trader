"""Regression: Walk-Forward bricht nicht mehr ab, wenn EIN Symbol nicht exakt zum
Manifest passt (z.B. HYPEUSDT 8659 ≠ 8759 wegen Lücke im Worker-Cache).
1) Lücken im 1m-Cache werden gezielt nachgeladen (candle_cache.repair_gaps)
2) bleibt die Abweichung, wird das Symbol erklärt ausgeschlossen
3) sind zu viele Symbole betroffen, bleibt der bisherige Abbruch."""
import asyncio

import numpy as np
import pytest

from services import candle_cache, regime_lab, research_dataset as rd
from services.candles import CandleArray

M = 60000


def _ca(ts):
    ts = np.asarray(ts, dtype=np.int64)
    x = 100 + (ts // M % 97).astype(float)
    return CandleArray(ts, x, x + 1, x - 1, x, np.ones(len(ts)))


def _hist(n, start=0):
    return [{"timestamp": start + i * 3600000, "open": 1.0, "high": 1.0, "low": 1.0,
             "close": 1.0 + i, "volume": 1.0} for i in range(n)]


def test_repair_gaps_fills_internal_hole(monkeypatch):
    full = np.arange(0, 600) * M
    holey = np.concatenate([full[:200], full[300:]])
    candle_cache._MEM["TESTGAP"] = {"candles": _ca(holey), "last_refresh": 0, "used_at": 0}

    async def fake_fetch(session, symbol, a, b, job=None, pace=None):
        return _ca(full[(full >= a) & (full <= b)])
    monkeypatch.setattr(candle_cache, "_fetch_range", fake_fetch)
    monkeypatch.setattr(candle_cache, "_save_disk", lambda s, c: None)
    added = asyncio.run(candle_cache.repair_gaps(None, "TESTGAP", 0, int(full[-1])))
    assert added == 100
    assert np.array_equal(candle_cache._MEM["TESTGAP"]["candles"].ts, full)
    candle_cache._MEM.pop("TESTGAP", None)


def _manifest(hists):
    return rd.dataset_manifest(hists, "1h")


def test_one_mismatching_symbol_is_excluded_not_fatal(monkeypatch):
    good = {f"C{i}USDT": _hist(300, i) for i in range(12)}
    good["HYPEUSDT"] = _hist(300, 99)
    manifest = _manifest(good)
    loaded = dict(good)
    loaded["HYPEUSDT"] = _hist(290, 99)                  # 10 Kerzen fehlen dauerhaft

    async def no_repair(*a, **k):
        return 0
    monkeypatch.setattr(candle_cache, "repair_gaps", no_repair)
    job = {}
    asyncio.run(regime_lab._verify_or_repair(None, loaded, manifest, job, None, 3600000))
    assert "HYPEUSDT" not in loaded and len(loaded) == 12
    assert job["dataset_excluded"][0]["symbol"] == "HYPEUSDT"
    assert "Kerzenanzahl 290 ≠ Manifest 300" in job["dataset_excluded"][0]["reason"]


def test_repair_then_verified_keeps_symbol(monkeypatch):
    good = {f"C{i}USDT": _hist(300, i) for i in range(3)}
    manifest = _manifest(good)
    loaded = dict(good)
    loaded["C0USDT"] = _hist(290, 0)

    async def repaired(*a, **k):
        return 600

    async def reload(session, sym):
        return _hist(300, 0)
    monkeypatch.setattr(candle_cache, "repair_gaps", repaired)
    job = {}
    asyncio.run(regime_lab._verify_or_repair(None, loaded, manifest, job, reload, 3600000))
    assert len(loaded["C0USDT"]) == 300 and "dataset_excluded" not in job


def test_too_many_mismatches_still_abort(monkeypatch):
    good = {f"C{i}USDT": _hist(300, i) for i in range(4)}
    manifest = _manifest(good)
    loaded = {s: _hist(290, i) for i, s in enumerate(good)}

    async def no_repair(*a, **k):
        return 0
    monkeypatch.setattr(candle_cache, "repair_gaps", no_repair)
    with pytest.raises(RuntimeError, match="Datensatz nicht reproduzierbar"):
        asyncio.run(regime_lab._verify_or_repair(None, loaded, manifest, {}, None, 3600000))


def test_verify_histories_unchanged_semantics():
    good = {"AUSDT": _hist(300)}
    m = _manifest(good)
    assert rd.verify_histories(good, m) == []
    assert "Kerzenanzahl" in rd.verify_histories({"AUSDT": _hist(299)}, m)[0]
    assert rd.min_verified(13) == 10 and rd.min_verified(2) == 2
