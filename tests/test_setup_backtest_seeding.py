"""Backtest-Seeding (services/setup_backtest) – reine Regressionstests.

Detektoren auf synthetischen Kerzen, Simulator (SL vor TP, Gebühren, Klassen-
Grenzen, MFE/MAE), Gewichtung (Deckelung, echte Paper-Trades Pflicht),
Runner-Regeln (passed/next_variant) und die Playbook-Einbindung (Default ohne
Backtest-Daten unverändert).
"""
import asyncio
import math
import sys
import os

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from services import ai_playbook, setup_capital  # noqa: E402
from services import setup_asset_class as ac  # noqa: E402
from services import setup_lifecycle as lifecycle  # noqa: E402
from services.candles import CandleArray  # noqa: E402
from services.setup_backtest import detectors, simulator, weights, runner  # noqa: E402
from services.setup_backtest.detectors import Features, Signal  # noqa: E402

M1 = 60_000
T0 = 1_700_000_000_000 - (1_700_000_000_000 % (5 * M1))


def _ca(closes, ts0=T0, spread=0.001, vol=None):
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    op = np.concatenate([[closes[0]], closes[:-1]])
    hi = np.maximum(op, closes) * (1 + spread)
    lo = np.minimum(op, closes) * (1 - spread)
    v = np.full(n, 100.0) if vol is None else np.asarray(vol, dtype=float)
    return CandleArray(ts0 + np.arange(n) * M1, op, hi, lo, closes, v)


def _flat_then_breakout(n_flat=1500, level=100.0):
    rng = np.random.default_rng(1)
    flat = level + rng.normal(0, 0.05, n_flat)            # enge Range
    spike = np.linspace(level, level * 1.03, 40)          # kräftiger Ausbruch
    after = level * 1.03 + rng.normal(0, 0.05, 300)
    vol = np.concatenate([np.full(n_flat, 100.0), np.full(40, 500.0), np.full(300, 100.0)])
    return _ca(np.concatenate([flat, spike, after]), vol=vol)


# ---------------------------------------------------------------- Features
def test_features_no_lookahead_15m_index():
    f = Features(_flat_then_breakout())
    for i in (200, 300, len(f.c5) - 1):
        j = int(f.i15[i])
        assert j >= 0
        # 15m-Kerze muss zum Schluss der 5m-Kerze bereits geschlossen sein
        assert int(f.c15.ts[j]) + detectors.M15 <= int(f.c5.ts[i]) + detectors.M5


def test_features_session_filter_and_atr_floor():
    ca = _flat_then_breakout()
    f_all = Features(ca)                      # Krypto: kein Zeitfilter
    f_idx = Features(ca, ac.INDICES)          # nur US-Session (Berlin 15:30-22:00)
    assert f_all.active.sum() >= f_idx.active.sum()
    for k, d in enumerate(f_idx.berlin()):
        hm = d.hour * 60 + d.minute
        inside = d.weekday() < 5 and 15 * 60 + 30 <= hm < 22 * 60
        if not inside:
            assert not f_idx.active[k]
    # ATR-Boden: komplett flache Phase nach einem volatilen Abschnitt = inaktiv
    rng = np.random.default_rng(5)
    closes = np.concatenate([100 + rng.normal(0, 0.5, 2000), np.full(1500, 100.0)])
    f = Features(_ca(closes, spread=0.0))
    assert not f.active[-10:].any()


# ---------------------------------------------------------------- Detektoren
def test_breakout_detects_range_break_long():
    f = Features(_flat_then_breakout())
    sigs = detectors.run_detector("breakout", f, 0)
    assert sigs and all(s.side == "LONG" for s in sigs)
    s = sigs[0]
    assert s.sl < s.entry < s.tp1 <= s.tpf
    assert f.c5.ts[s.idx] >= T0 + 1500 * M1 - 5 * M1   # erst im Ausbruch, nicht in der Range


def test_breakout_no_signal_in_pure_noise():
    rng = np.random.default_rng(3)
    f = Features(_ca(100 + rng.normal(0, 0.02, 2500)))
    # Rauschen ohne Volumen-/Body-Impuls: (fast) keine Signale
    assert len(detectors.run_detector("breakout", f, 1)) <= 3


def test_mean_reversion_after_crash():
    rng = np.random.default_rng(2)
    base = 100 + rng.normal(0, 0.03, 1500)
    crash = np.linspace(100, 96, 30)
    bounce = np.linspace(96, 96.4, 20)
    f = Features(_ca(np.concatenate([base, crash, bounce, 96.4 + rng.normal(0, 0.03, 200)])))
    sigs = detectors.run_detector("mean_reversion", f, 0)
    assert sigs and sigs[0].side == "LONG"
    assert sigs[0].tpf > sigs[0].entry > sigs[0].sl


def test_all_detectors_run_on_synthetic_data():
    rng = np.random.default_rng(7)
    steps = rng.normal(0, 0.0008, 6000)
    closes = 100 * np.cumprod(1 + steps)
    f = Features(_ca(closes, vol=rng.uniform(50, 300, 6000)))
    for sid, variants in detectors.VARIANTS.items():
        for vi in range(len(variants)):
            sigs = detectors.run_detector(sid, f, vi)
            for s in sigs:
                assert s.side in ("LONG", "SHORT") and s.entry > 0
                d = 1 if s.side == "LONG" else -1
                assert d * (s.entry - s.sl) > 0, f"{sid}: SL auf falscher Seite"
                assert d * (s.tpf - s.entry) > 0, f"{sid}: TP auf falscher Seite"
                assert 0 <= s.idx < f.n


# ---------------------------------------------------------------- Simulator
def _five(closes):
    return CandleArray(T0 + np.arange(len(closes)) * 5 * M1, np.asarray(closes, float),
                       np.asarray(closes, float), np.asarray(closes, float),
                       np.asarray(closes, float), np.ones(len(closes)))


def test_simulator_sl_before_tp_same_candle():
    c = _five([100, 100, 100])
    c.hi[1], c.lo[1] = 103.0, 98.0                    # SL und TP in derselben Kerze
    sig = Signal(0, "LONG", 100.0, 99.0, 101.0, 102.0)
    r = simulator.simulate(c, sig, 0.0)
    assert r["reason"] == "sl" and r["exit_price"] == 99.0 and r["pnl"] < 0


def test_simulator_tp1_then_be_and_tpf():
    c = _five([100, 100, 100, 100])
    c.hi[1] = 101.2                                   # TP1
    c.hi[2], c.lo[2] = 102.5, 100.5                   # TPf in Folgekerze (BE nicht berührt)
    sig = Signal(0, "LONG", 100.0, 99.0, 101.0, 102.0)
    r = simulator.simulate(c, sig, 0.0)
    assert r["tp1_done"] and r["reason"] == "tp"
    assert math.isclose(r["pnl"], (0.5 * 0.01 + 0.5 * 0.02) * simulator.MARGIN, rel_tol=1e-6)
    assert r["peak_price"] == 102.5 and r["trough_price"] <= 100.0


def test_simulator_fees_and_time_exit():
    c = _five([100, 100.1, 100.1, 100.1])
    sig = Signal(0, "SHORT", 100.0, 101.0, 99.5, 99.0)
    r = simulator.simulate(c, sig, 0.06, max_bars=2)
    assert r["reason"] == "time"
    assert math.isclose(r["pnl"], -0.001 * simulator.MARGIN - 0.0006 * simulator.MARGIN, rel_tol=1e-6)


def test_clamp_signal_uses_class_limits():
    sig = Signal(0, "LONG", 100.0, 95.0, 101.0, 110.0)     # SL 5 % – für Forex viel zu weit
    fx = simulator.clamp_signal(ac.FOREX, sig)
    lim = ac.LIMITS[ac.FOREX]
    assert math.isclose((fx.entry - fx.sl) / fx.entry * 100, lim["sl_max"], rel_tol=1e-6)
    assert (fx.tpf - fx.entry) / fx.entry * 100 <= lim["tpf_max"] + 1e-9
    assert simulator.clamp_signal(ac.CRYPTO, Signal(0, "LONG", 100, 99, 100, 100)) is None


# ---------------------------------------------------------------- Gewichtung
def test_merge_requires_real_profitable_paper_trades():
    bt = {"trades": 20, "wins": 14, "pnl": 30.0, "margin": 2000.0}
    assert weights.merge_stats(None, bt) is None
    assert weights.merge_stats({"trades": 1, "wins": 1, "pnl": 2.0}, bt) is None
    assert weights.merge_stats({"trades": 3, "wins": 1, "pnl": -1.0}, bt) is None
    assert weights.merge_stats({"trades": 3, "wins": 2, "pnl": 1.0}, None) is None


def test_merge_weight_and_cap():
    real = {"trades": 2, "wins": 2, "pnl": 3.0, "margin": 200.0}
    m = weights.merge_stats(real, {"trades": 4, "wins": 3, "pnl": 8.0, "margin": 400.0})
    assert m["trades"] == 4 and m["backtest_weighted"] == 2.0        # 4 x 0.5
    assert lifecycle.promotion_ok(m)[0] is False                      # 4 < 5
    m = weights.merge_stats(real, {"trades": 20, "wins": 14, "pnl": 40.0, "margin": 2000.0})
    assert m["trades"] == 5 and m["backtest_weighted"] == 3.0        # gedeckelt auf 3
    assert lifecycle.promotion_ok(m)[0] is True
    assert ai_playbook.live_ready(m)[0] is True


def test_setup_factor_backtest_promoted():
    f, note = setup_capital.setup_factor({"trades": 2, "verdict": "test", "backtest_promoted": True})
    assert f == 0.4 and "Backtest" in note
    assert setup_capital.setup_factor({"trades": 2, "verdict": "test"})[0] == 0.6


# ---------------------------------------------------------------- Runner-Regeln
def test_passed_requires_edge_in_both_windows():
    good = {"trades": 12, "wins": 8, "pnl": 5.0}
    assert runner.passed(good, good)
    assert not runner.passed(good, {"trades": 12, "wins": 8, "pnl": -1.0})
    assert not runner.passed({"trades": 5, "wins": 5, "pnl": 5.0}, good)
    assert not runner.passed(good, {"trades": 9, "wins": 9, "pnl": 5.0})


def test_next_variant_cycles_and_exhausts():
    assert runner.next_variant(0, 3) == {"variant": 1, "status": "failed"}
    assert runner.next_variant(2, 3) == {"variant": 0, "status": "exhausted"}


def test_no_edge_line():
    st = {"breakout": {"status": "exhausted"}, "pullback": {"status": "failed"}, "x": {"status": "exhausted"}}
    line = runner.no_edge_line("Indizes", st)
    assert line.startswith("BACKTEST OHNE EDGE Indizes") and "breakout, x" in line and "pullback" not in line
    assert runner.no_edge_line("Indizes", {}) is None
    assert runner.no_edge_line("Indizes", {"live_blocked": {"a": 1}}) is None


def test_eligible_setups_respect_class_exclusions():
    lib = ai_playbook.SETUPS
    assert "funding_fade" not in runner.eligible_setups(ac.INDICES, lib)
    assert set(runner.eligible_setups(ac.CRYPTO, lib)) == set(detectors.VARIANTS)
    assert runner.eligible_setups(ac.FOREX, lib, ["breakout", "hedge"]) == ["breakout"]


def test_run_variant_splits_is_oos():
    f = Features(_flat_then_breakout())
    split = int(f.c5.ts[0] + (f.c5.ts[-1] - f.c5.ts[0]) * 0.5)
    res = runner.run_variant("breakout", {"QQQUSDT": f}, 0, ac.INDICES, split, "job")
    assert res["setup"] == "breakout" and res["is"]["trades"] + res["oos"]["trades"] >= 1
    for t in res["oos_trades"]:
        assert t["oos"] and t["backtest"] and t["mode"] == "backtest" and t["strategy_id"] == "ai_trader"
        assert t["asset_class"] == ac.INDICES and t["margin_used"] == simulator.MARGIN


# ---------------------------------------------------------------- Playbook-UI
def test_maturity_overview_backtest_field_default_none():
    rows = ai_playbook.maturity_overview({"breakout": {"trades": 2, "wins": 1, "pnl": 0.5, "verdict": "test"}},
                                         {}, asset_class=ac.INDICES)
    row = next(r for r in rows if r["setup"] == "breakout")
    assert row["backtest"] is None and row["live_ready"] is False


def test_maturity_overview_backtest_field_present():
    rows = ai_playbook.maturity_overview({}, {}, asset_class=ac.INDICES,
                                         backtest={"breakout": {"trades": 12, "wins": 7, "pnl": 4.0}},
                                         bt_promoted={})
    row = next(r for r in rows if r["setup"] == "breakout")
    assert row["backtest"] == {"trades": 12, "winrate": 58, "pnl": 4.0, "weighted": 3.0, "promoted": False}


def test_backtest_context_lines():
    lines = ai_playbook.backtest_context_lines(
        "Indizes", {"breakout": {"trades": 12, "wins": 7, "pnl": 4.0}}, {}, {}, {})
    assert lines[0].startswith("BACKTEST-EDGE Indizes")
    assert "noch 2 echte Paper-Trades" in lines[1]
    assert ai_playbook.backtest_context_lines("X", {}, {}, {}, {}) == []
    # rückgestufte Setups bekommen keinen Backtest-Hinweis
    assert ai_playbook.backtest_context_lines(
        "X", {"breakout": {"trades": 12, "wins": 7, "pnl": 4.0}}, {}, {}, {"breakout": {}}) == []


# ---------------------------------------------------------------- refresh() mit Backtest-Daten
class _Agg:
    def __init__(self, rows):
        self.rows = rows

    def aggregate(self, pipeline, *a, **kw):
        match = pipeline[0]["$match"]
        rows = self.rows
        if match.get("mode") == "live":
            rows = []
        if isinstance(match.get("symbol"), dict):
            rows = [r for r in rows if r.get("_symbol") in match["symbol"]["$in"]] if rows and "_symbol" in rows[0] else rows
        if match.get("setup") and not isinstance(match["setup"], dict):
            rows = [r for r in rows if r["_id"] == match["setup"]]
        if isinstance(match.get("asset_class"), str):
            rows = [r for r in rows if r.get("_cls") == match["asset_class"]]

        class _C:
            async def to_list(self_, n=None):
                return [dict(r) for r in rows]
        return _C()

    def find(self, *a, **kw):
        class _C:
            async def to_list(self_, n=None):
                return []
        return _C()


class _Settings:
    def __init__(self, doc):
        self.doc = doc

    async def find_one(self, *a, **kw):
        return dict(self.doc)

    async def update_one(self, q, upd, **kw):
        self.doc.update(upd.get("$set", {}))


class _Chat:
    def __init__(self):
        self.inserted = []

    async def insert_one(self, doc):
        self.inserted.append(doc)


class _FakeDB:
    def __init__(self, real_rows, bt_rows):
        self.auto_trades = _Agg(real_rows)
        self.settings = _Settings({"_id": "ai_playbook_state", "disabled": {}, "live_ready": {}})
        self.ai_chat = _Chat()
        self._bt = _Agg(bt_rows)

    def __getitem__(self, name):
        assert name == weights.COLLECTION
        return self._bt


def _refresh(real_rows, bt_rows):
    ai_playbook._ready_cache.clear()
    ai_playbook._class_cache.clear()
    ai_playbook.invalidate_cache()
    db = _FakeDB(real_rows, bt_rows)
    return db, asyncio.run(ai_playbook.refresh(db))


BT_GOOD = [{"_id": "breakout", "_cls": ac.INDICES, "trades": 12, "wins": 8, "pnl": 6.0, "margin": 1200.0}]


def test_refresh_backtest_boost_requires_real_paper_trades():
    db, data = _refresh([], BT_GOOD)
    cls = data["classes"][ac.INDICES]
    assert cls["live_ready"]["breakout"] is False and cls["bt_promoted"] == {}
    assert cls["backtest"]["breakout"]["trades"] == 12          # sichtbar (Spalte BT) …
    # … aber ohne echte Trades keine Freischaltung, Kapital-Faktor unverändert
    assert ai_playbook.live_ready_for("breakout", None, asset_class=ac.INDICES)[0] is False
    assert setup_capital.setup_factor(ai_playbook.class_stats(ac.INDICES, "breakout"))[0] == 0.6


def test_refresh_backtest_boost_with_two_good_paper_trades():
    real = [{"_id": "breakout", "_symbol": "QQQUSDT", "trades": 2, "wins": 2, "pnl": 1.5, "margin": 200.0}]
    db, data = _refresh(real, BT_GOOD)
    cls = data["classes"][ac.INDICES]
    assert cls["live_ready"]["breakout"] is True
    assert "breakout" in cls["bt_promoted"] and cls["bt_promoted"]["breakout"]["backtest_weighted"] == 3.0
    ok, why = ai_playbook.live_ready_for("breakout", None, asset_class=ac.INDICES)
    assert ok and why.startswith("backtest-seeded")
    # klein dimensionierter Live-Antest
    assert setup_capital.setup_factor(ai_playbook.class_stats(ac.INDICES, "breakout"))[0] == 0.4
    # UI-Zeile trägt das BT-Feld inkl. promoted
    row = next(r for r in ai_playbook.maturity_overview(
        cls["stats"], {}, cls["live_blocked"], cls["live_stats"], None, cls["judge_stats"],
        asset_class=ac.INDICES, ready=cls["live_ready"], ready_why=cls["ready_why"],
        backtest=cls["backtest"], bt_promoted=cls["bt_promoted"]) if r["setup"] == "breakout")
    assert row["live_ready"] and row["backtest"]["promoted"] is True
    # Prompt-Block nennt den Backtest-Edge
    lines = ai_playbook.backtest_context_lines("Indizes", cls["backtest"], cls["bt_promoted"],
                                               cls["stats"], cls["live_blocked"])
    assert any("LIVE-Antest klein" in ln for ln in lines)


def test_refresh_without_backtest_collection_is_unchanged():
    """Default/Rückwärtskompatibilität: ohne Backtest-Daten identisches Verhalten."""
    real = [{"_id": "breakout", "trades": 2, "wins": 2, "pnl": 1.5, "margin": 200.0}]
    db, data = _refresh(real, [])
    cls = data["classes"][ac.INDICES]
    assert cls["live_ready"]["breakout"] is False and cls["backtest"] == {} and cls["bt_promoted"] == {}
    assert setup_capital.setup_factor(ai_playbook.class_stats(ac.INDICES, "breakout"))[0] == 0.6


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
