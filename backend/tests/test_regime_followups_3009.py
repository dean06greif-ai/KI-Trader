"""Regressionstests 30.09.2026 (Teil 2): Lücken-Reparatur aus zweiter Quelle,
Ergebnis je Regime (gehandelt vs. Walk-Forward), Verlust-Regime abschalten.
Rein (ohne DB/Netz)."""
import asyncio
import time

import numpy as np

from services import candle_cache as cc
from services import dynamic_performance as dp
from services import dynamic_workbench as wb
from services import history_sources as hs
from services import local_exec
from services.candles import CandleArray


# ---------------- Lücken-Reparatur ----------------
def test_secondary_source_mapping(monkeypatch):
    monkeypatch.setattr(hs, "source_of", lambda s: {"HYPEUSDT": "binance", "XAUTUSDT": "bitunix",
                                                    "EURUSD": "yahoo", "FOO": "yahoo"}[s])
    assert hs.secondary_source("HYPEUSDT") == "bitunix"
    assert hs.secondary_source("XAUTUSDT") == "binance"
    assert hs.secondary_source("EURUSD") == "dukascopy"
    assert hs.secondary_source("FOO") is None


def _arr(ts):
    ts = np.array(ts, dtype=np.int64)
    one = np.ones(len(ts))
    return CandleArray(ts, one * 10, one * 11, one * 9, one * 10, one)


def test_repair_gaps_secondary_fills_hole_and_stats(monkeypatch):
    base = 1_700_000_000_000
    have = [base + i * 60000 for i in range(100) if not 40 <= i < 60]   # 20 min Lücke
    monkeypatch.setitem(cc._MEM, "HYPEUSDT", {"candles": _arr(have), "used_at": time.time()})
    monkeypatch.setattr(cc, "_save_disk", lambda *a, **k: None)
    assert cc.gap_stats("HYPEUSDT") == {"gaps": 1, "missing_minutes": 20}
    asked = {}

    async def fake_secondary(session, sym, a, b, job=None):
        asked["range"] = (a, b)
        rows = [[t, 10, 11, 9, 10, 1] for t in range(a - 120000, b + 120001, 60000)]  # etwas zu breit
        return [np.array(rows, dtype=float)]
    monkeypatch.setattr(hs, "fetch_secondary", fake_secondary)
    monkeypatch.setattr(hs, "secondary_source", lambda s: "bitunix")
    added = asyncio.run(cc.repair_gaps(None, "HYPEUSDT", have[0], have[-1], source="secondary"))
    assert added == 20, "nur die Lücke selbst wird eingefügt"
    assert asked["range"] == (base + 40 * 60000, base + 59 * 60000)
    assert cc.gap_stats("HYPEUSDT") == {"gaps": 0, "missing_minutes": 0}


def test_worker_min_version(monkeypatch):
    monkeypatch.setattr(local_exec, "WORKERS", {"w1": {"version": "1.16.2", "last_seen": local_exec._now()}})
    assert not local_exec.worker_min_version(local_exec.DATA_REPAIR_MIN_VERSION)
    monkeypatch.setattr(local_exec, "WORKERS", {"w1": {"version": "1.17.0", "last_seen": local_exec._now()}})
    assert local_exec.worker_min_version(local_exec.DATA_REPAIR_MIN_VERSION)


# ---------------- Verlust-Regime abschalten ----------------
def test_ui_data_info_normalizes_worker_payload():
    d = local_exec.ui_data_info({"symbols": ["HYPEUSDT", "BTCUSDT"], "data_dir": "/w",
                                 "detail": [{"symbol": "HYPEUSDT", "bytes": 100, "mtime": 1_700_000_000,
                                             "candles": 4298, "first_ts": 1, "last_ts": 2}]})
    assert d["dir"] == "/w" and d["total_bytes"] == 100
    assert d["symbols"][0]["symbol"] == "HYPEUSDT" and d["symbols"][0]["candles"] == 4298
    assert d["symbols"][0]["updated"].startswith("2023-11-14")
    assert d["symbols"][1] == {"symbol": "BTCUSDT"}
    # altes Objekt-Format bleibt unverändert nutzbar
    assert local_exec.ui_data_info({"symbols": [{"symbol": "X", "bytes": 1}]})["symbols"][0]["symbol"] == "X"
    assert local_exec.ui_data_info(None)["symbols"] == []


WF = {"per_regime": [
    {"regime": 0, "label": "Ab", "metrics": {"trades": 56, "pnl": -100.09}},
    {"regime": 1, "label": "Seit", "metrics": {"trades": 3, "pnl": -50.0}},      # zu wenig Trades
    {"regime": 2, "label": "Auf", "metrics": {"trades": 63, "pnl": 442.48}},
]}


def test_losing_regimes_needs_evidence():
    assert wb.losing_regimes(WF) == [{"regime": 0, "label": "Ab", "pnl": -100.09, "trades": 56}]
    assert wb.losing_regimes(None) == []


def test_kept_after_skip_is_marked_not_independent():
    k = wb.kept_after_skip(WF, wb.losing_regimes(WF))
    assert k == {"pnl": 392.48, "trades": 66, "regimes": 2, "independent": False}
    assert wb.kept_after_skip(WF, []) is None


# ---------------- Ergebnis je Regime ----------------
def _t(rid, pnl):
    return {"dynamic": {"regime": rid}, "realized_pnl": pnl,
            "result": "win" if pnl > 0 else "loss", "fees_paid": 0.1}


def test_aggregate_per_regime_with_walkforward():
    trades = [_t(0, -5)] * 6 + [_t(2, 3)] * 4 + [_t(2, -1)] * 2 + [{"realized_pnl": 1, "result": "win"}]
    regimes = [{"id": 0, "label": "Ab", "traded": True}, {"id": 1, "label": "Seit", "traded": False},
               {"id": 2, "label": "Auf", "traded": True}]
    res = dp.aggregate(trades, regimes, WF["per_regime"])
    r0, r1, r2 = res["regimes"]
    assert r0["live"]["trades"] == 6 and r0["live"]["pnl"] == -30 and r0["verdict"]["status"] == "negative"
    assert r1["live"]["trades"] == 0 and r1["verdict"]["status"] == "few" and r1["traded"] is False
    assert r2["live"]["win_rate"] == 66.7 and r2["live"]["pnl"] == 10
    assert r2["walkforward"] == {"trades": 63, "win_rate": None, "pnl": 442.48}
    assert r2["verdict"]["status"] in ("ok", "weaker")
    assert res["unknown_regime_trades"] == 1 and res["total"]["trades"] == 13


def test_verdict_live_negative_but_walkforward_positive():
    v = dp._verdict({"trades": 10, "pnl": -4.0, "avg_pnl": -0.4}, {"trades": 20, "pnl": 30})
    assert v["status"] == "worse"
