"""Regression: gespeichertes Analyse-Fenster darf nicht vom Tages-Deckel
(days_cap, ab JETZT) abgeschnitten werden ("Kerzenanzahl ≠ Manifest")."""
import asyncio
import time

import numpy as np

DAY = 86400000


def _daily(start_ms, n):
    return [{"timestamp": start_ms + i * DAY, "open": 1.0 + i, "high": 2.0 + i, "low": 0.5 + i,
             "close": 1.5 + i, "volume": 10.0} for i in range(n)]


def test_get_candles_honours_anchor_beyond_cap(monkeypatch):
    from services import candle_cache as cc, history_sources as hs
    from services.candles import CandleArray
    now = int(time.time() * 1000)
    t0 = (now - 500 * DAY) // DAY * DAY
    arr = CandleArray.from_dicts(_daily(t0, 495))
    monkeypatch.setitem(cc._MEM, "TESTANCHOR", {"candles": arr, "last_refresh": time.time(),
                                                "used_at": time.time()})
    monkeypatch.setattr(hs, "days_cap", lambda sym, d: min(int(d), 365))
    capped = asyncio.run(cc.get_candles(None, "TESTANCHOR", 450))
    anchored = asyncio.run(cc.get_candles(None, "TESTANCHOR", 450, start_ms=t0 + 30 * DAY))
    assert int(capped.ts[0]) >= now - 365 * DAY
    assert int(anchored.ts[0]) == t0 + 30 * DAY


def test_fetch_histories_pinned_window_not_excluded(monkeypatch):
    from services import regime_lab as lab, research_dataset as rd
    import services.backtester as bt
    now = int(time.time() * 1000)
    t0 = (now - 500 * DAY) // DAY * DAY
    base = _daily(t0, 495)

    async def fake_fetch(session, sym, days, job=None, start_ms=None):
        start = now - min(int(days), 365) * DAY
        if start_ms:
            start = min(start, int(start_ms))
        return [c for c in base if c["timestamp"] >= start]

    monkeypatch.setattr(bt, "fetch_history", fake_fetch)
    window = [c for c in base if t0 + 50 * DAY <= c["timestamp"] <= t0 + 480 * DAY]
    man = rd.dataset_manifest({"HYPEUSDT": window, "BTCUSDT": window, "ETHUSDT": window}, "1d")
    anchors = {s: window[0]["timestamp"] for s in man["per_symbol"]}
    ends = {s: window[-1]["timestamp"] for s in man["per_symbol"]}
    job = {}
    hist = asyncio.run(lab.fetch_histories(list(anchors), 365, "1d", job=job, start_ts=anchors,
                                           end_ts=ends, dataset=man))
    assert set(hist) == set(anchors)
    assert not job.get("dataset_excluded")
    assert rd.verify_histories(hist, man) == []
    assert np.isclose(hist["HYPEUSDT"][0]["timestamp"], window[0]["timestamp"])
