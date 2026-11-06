"""Regressionstests 07.10.2026: „Ein Regime für alle“ (Auto-Trade-Gate nutzt die
wirksame Lab-/Champion-Erkennung, Rückfall eigene) und „Fairer Zeitraum-Vergleich“
(alle Erkennungen auf exakt demselben Testzeitraum). Reine Funktionen, kein Netz."""
import asyncio
from datetime import datetime, timedelta, timezone

import numpy as np

from services import regime_fair_compare as fair
from services import regime_gate
from services import regime_selection as sel
from services import structural_regime as sr

DAY = fair.DAY_MS
NOW = 1_800_000_000_000


def _doc(aid, train_end_days_ago, tf="1h", coins=("BTCUSDT",)):
    te = NOW - int(train_end_days_ago * DAY) if train_end_days_ago is not None else None
    return {"id": aid, "timeframe": tf, "bounds": {c: {"train_end_ts": te} for c in coins}}


# ---------------- Fairer Zeitraum: gemeinsames Fenster ----------------
def test_window_starts_after_latest_train_end_over_all_coins():
    d = {"id": "a", "bounds": {"BTCUSDT": {"train_end_ts": NOW - 100 * DAY},
                               "ETHUSDT": {"train_end_ts": NOW - 60 * DAY}}}
    assert fair.unseen_start_ts(d) == NOW - 60 * DAY       # Kombi-Modell: kein Leck über andere Coins
    start, end, note, excl = fair.common_window([d, _doc("b", 90)], NOW, 7.0)
    assert start == NOW - 60 * DAY and end == NOW - int(3.5 * DAY) and not excl
    assert "Tage gemeinsames Out-of-Sample" in note


def test_too_fresh_and_boundless_analyses_excluded_not_blocking():
    start, _, _, excl = fair.common_window([_doc("old", 120), _doc("fresh", 10), _doc("legacy", None)], NOW, 7.0)
    assert start == NOW - 120 * DAY
    assert set(excl) == {"fresh", "legacy"} and "zu frisch" in excl["fresh"]
    start, _, note, excl = fair.common_window([_doc("fresh", 5)], NOW, 7.0)
    assert start is None and "kein Kandidat" in note and "fresh" in excl


def test_window_capped_at_max_days():
    start, end, _, _ = fair.common_window([_doc("a", 500)], NOW, 7.0)
    assert (end - start) / DAY == fair.MAX_WINDOW_DAYS


# ---------------- Kausale Projektion & Metrik ----------------
def test_coarse_labels_only_after_candle_close():
    h = 3_600_000
    base_ts = np.arange(0, 12) * h                          # 12 × 1h
    src_ts = np.array([0, 4 * h, 8 * h])                    # 3 × 4h
    out = fair.project_causal(src_ts, 4 * h, np.array([0, 1, 2]), base_ts, h)
    # 4h-Kerze 0 schließt bei 4h -> gilt erst ab 1h-Kerze 3 (Schluss 4h)
    assert out.tolist() == [-1, -1, -1, 0, 0, 0, 0, 1, 1, 1, 1, 2]


def test_macro_f1_and_windows():
    t = np.array([0] * 20 + [2] * 20 + [1] * 20)
    assert fair.macro_f1(t, t) == 100.0
    assert fair.macro_f1(np.array([0, 1]), np.array([0, 1])) is None   # < 10 gültige
    ws = fair.window_scores(t, t, np.ones(60, dtype=bool))
    assert ws["f1"] == 100.0 and len(ws["windows"]) == 3 and ws["bars"] == 60
    assert fair.window_scores(t, t, np.zeros(60, dtype=bool))["f1"] is None


def _candles(n, tf_ms):
    rng = np.random.default_rng(1)
    drift = np.r_[np.full(n // 3, 0.004), np.full(n // 3, -0.004), np.zeros(n - 2 * (n // 3))]
    close = 100 * np.exp(np.cumsum(drift + rng.normal(0, 0.002, n)))
    return [{"timestamp": i * tf_ms, "open": c, "high": c * 1.001, "low": c * 0.999, "close": c, "volume": 1}
            for i, c in enumerate(close)]


def test_evaluate_symbol_same_axis_and_window(monkeypatch):
    from services import regime as rg
    from services import regime_reference as rref
    from services import regime_truth as rt
    h = 3_600_000
    c1h = _candles(24 * 120, h)
    c4h = c1h[::4]
    truth = fair._directions(rt.centered_labels(c1h, rref.reference_cfg({"bars_per_day": 24.0}), 3), 3)

    def fake(model, candles, tf):
        if model["kind"] == "perfect":
            return [None if x < 0 else int(x) for x in truth]
        return [1] * len(candles)                           # immer „seitwärts“
    monkeypatch.setattr(rg, "classify_series", fake)
    cands = [{"aid": "p", "scope": "combined", "timeframe": "1h", "model": {"kind": "perfect", "config": {"regime_mode": 3}}},
             {"aid": "s", "scope": "combined", "timeframe": "4h", "model": {"kind": "side", "config": {"regime_mode": 3}}}]
    rows = fair.evaluate_symbol("BTCUSDT", cands, {"1h": c1h, "4h": c4h}, 20 * 24 * h, 110 * 24 * h)
    by = {r["aid"]: r for r in rows}
    assert {r["base_timeframe"] for r in rows} == {"1h"}    # eine Zeitachse für alle
    assert by["p"]["bars"] == by["s"]["bars"]               # exakt derselbe Zeitraum
    assert by["p"]["f1"] > by["s"]["f1"]
    assert len(by["p"]["windows"]) == fair.N_WINDOWS


# ---------------- Champion-Wahl mit fairem Vergleich ----------------
def _row(aid, h=60.0, i=58.0, t=62.0, scope="combined"):
    return {"aid": aid, "name": aid, "scope": scope, "timeframe": "1h", "holdout_f1": h, "inner_f1": i,
            "train_f1": t, "holdout_bars": 2000, "bars_per_day": 24.0, "walkforward_passed": None}


def _fair_res(rows, computed=None, seen=None, excluded=None):
    return {"rows": rows, "seen": seen if seen is not None else [fair.cand_key(r["aid"], r["scope"]) for r in rows],
            "excluded": excluded or {}, "window": {"note": "90 Tage gemeinsames Out-of-Sample"},
            "computed_at": (computed or datetime.now(timezone.utc)).isoformat()}


def test_apply_fair_replaces_own_period_values():
    rows = [_row("a", h=70), _row("b", h=50)]
    fr = _fair_res([{"aid": "a", "scope": "combined", "f1": 52.0, "windows": [50.0, 52.0, 54.0], "bars": 2000,
                     "base_timeframe": "1h"},
                    {"aid": "b", "scope": "combined", "f1": 61.0, "windows": [60.0, 61.0, 62.0], "bars": 2000,
                     "base_timeframe": "1h"}])
    out = {r["aid"]: r for r in sel.apply_fair(rows, fr)}
    assert out["a"]["holdout_f1"] == 52.0 and out["a"]["own_holdout_f1"] == 70
    res = sel.choose(list(out.values()))
    assert res["champion"]["aid"] == "b"                    # fair besser, nicht Glücks-Zeitraum
    assert any("gleicher Zeitraum" in w for w in res["champion"]["why"])


def test_beats_every_window_pairs_same_time_slices():
    ch = {"fair": True, "windows": [60, 61, 62], "holdout_f1": 61}
    assert sel.beats_every_window(ch, {"fair": True, "windows": [55, 56, 57], "holdout_f1": 56})
    assert not sel.beats_every_window(ch, {"fair": True, "windows": [55, 62, 57], "holdout_f1": 56})


def test_fair_rows_with_unmeasured_window_not_eligible():
    r = sel.evaluate({**_row("a"), "fair": True, "windows": [60.0, None, 61.0], "inner_f1": None, "min_bars": 504})
    assert not r["eligible"] and "Teilfenstern" in r["why"][0]


def test_status_for_conditions():
    rows_fair = [{"aid": "a", "scope": "combined", "f1": 55.0}, {"aid": "b", "scope": "combined", "f1": None}]
    keys = ["a|combined", "b|combined"]
    assert fair.status_for(None, keys, None)["reason"] == "missing"
    assert fair.status_for(_fair_res(rows_fair), keys, {"aid": "a"})["used"]
    old = datetime.now(timezone.utc) - timedelta(days=fair.FRESH_DAYS + 1)
    assert fair.status_for(_fair_res(rows_fair, computed=old), keys, None)["reason"] == "old"
    assert fair.status_for(_fair_res(rows_fair), keys + ["new|combined"], None)["reason"] == "uncovered"
    st = fair.status_for(_fair_res(rows_fair), keys, {"aid": "b", "scope": "combined"})
    assert not st["used"] and st["reason"] == "blocked"     # Amtsinhaber nicht messbar -> alte Werte
    assert fair.status_for({"error": "x", "computed_at": datetime.now(timezone.utc).isoformat()}, keys, None)["reason"] == "blocked"


def test_needs_refresh():
    now = datetime.now(timezone.utc)
    assert fair.needs_refresh(None) and fair.needs_refresh({"reason": "old"})
    assert fair.needs_refresh({"reason": "uncovered"}) and not fair.needs_refresh({"reason": "ok"})
    assert not fair.needs_refresh({"reason": "blocked", "computed_at": now.isoformat()})
    assert fair.needs_refresh({"reason": "blocked", "computed_at": (now - timedelta(hours=25)).isoformat()})


def test_candidates_of_matches_candidate_rows_keys():
    doc = {"id": "x", "timeframe": "1h", "settings": {"regime_mode": 5},
           "combined": {"model": {"config": {}}, "per_symbol": {"BTCUSDT": {"reference": {}}}},
           "per_coin": {"BTCUSDT": {"model": {"config": {}}}, "ETHUSDT": {"error": "x"}}}
    cands, seen = fair.candidates_of([doc], "BTCUSDT", "intraday")
    rows = sel.candidate_rows(doc, "BTCUSDT", 24.0)
    assert sorted(seen) == sorted(fair.cand_key(r["aid"], r["scope"]) for r in rows)
    assert len(cands) == 2
    _, seen_eth = fair.candidates_of([doc], "ETHUSDT", "intraday")
    assert seen_eth == []                                   # Coin-Fehler + nicht im Kombi-Pool


# ---------------- Ein Regime für alle (Gate) ----------------
def test_gate_auto_uses_lab_truth_and_falls_back(monkeypatch):
    cfg = {"regime_filter_enabled": True, "regime_block_phases": ["seitwärts"], "regime_gate_source": "own"}

    async def own(symbol):
        return {"phase": "seitwärts", "label": "Seitwärts", "confidence": 70}
    monkeypatch.setattr(regime_gate, "current_phase", own)

    async def lab_bull(symbol, band=None):
        return "bulle"
    monkeypatch.setattr(sr, "phase", lab_bull)
    ok, _ = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert ok                                               # Alt-Wert own -> auto: Lab-Wahrheit gilt

    async def lab_none(symbol, band=None):
        return None
    monkeypatch.setattr(sr, "phase", lab_none)
    ok, msg = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert not ok and "Regime-Filter" in msg                # kein wirksames Lab -> bisheriges Verhalten

    async def lab_err(symbol, band=None):
        raise RuntimeError("boom")
    monkeypatch.setattr(sr, "phase", lab_err)
    ok, _ = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT"))
    assert not ok                                           # Lab-Fehler -> Rückfall eigene (nicht fail-open)
    ok, _ = asyncio.run(regime_gate.check_signal_allowed({**cfg, "regime_gate_source": "lab"}, "BTCUSDT"))
    assert ok                                               # nur Lab: fail-open wie bisher


def test_gate_horizon_band_passed_to_lab(monkeypatch):
    seen = {}

    async def lab(symbol, band=None):
        seen["band"] = band
        return "bär"
    monkeypatch.setattr(sr, "phase", lab)
    cfg = {"regime_filter_enabled": True, "regime_block_phases": ["bär"]}
    ok, msg = asyncio.run(regime_gate.check_signal_allowed(cfg, "BTCUSDT", "swing"))
    assert not ok and "Lab-Analyse" in msg and seen["band"] is not None


def test_default_coin_cfg_uses_one_truth():
    from services import bitunix_trade
    assert bitunix_trade.DEFAULT_COIN_CFG["regime_gate_source"] == "auto"
    assert bitunix_trade.DEFAULT_COIN_CFG["regime_filter_enabled"] is False   # Filter bleibt opt-in


# ---------------- Hintergrund-Refresh ----------------
def test_auto_fair_skips_when_champion_mode_off(monkeypatch):
    calls = []

    async def state():
        return {"mode": "off", "assign": {}}
    monkeypatch.setattr(sr, "_champion_state", state)

    async def many(db, keys):
        calls.append(keys)
    monkeypatch.setattr(fair, "run_many", many)
    sr._champ["fair_at"] = 0.0
    asyncio.run(sr._auto_fair(["BTCUSDT"]))
    assert calls == []


def test_auto_fair_runs_due_pairs_limited(monkeypatch):
    calls = []

    async def state():
        return {"mode": "suggest", "assign": {}}
    monkeypatch.setattr(sr, "_champion_state", state)

    async def compute(db, symbols):
        return {"results": {f"{s}|intraday": {"fair": {"reason": "missing"}} for s in ("A", "B", "C")}
                | {"D|swing": {"fair": {"reason": "ok", "used": True}}}}
    from services import regime_selection
    monkeypatch.setattr(regime_selection, "compute", compute)

    async def many(db, keys):
        calls.append(keys)
    monkeypatch.setattr(fair, "run_many", many)
    sr._champ["fair_at"] = 0.0
    asyncio.run(sr._auto_fair(["A", "B", "C", "D"]))
    assert calls == [["A|intraday", "B|intraday"]]
    asyncio.run(sr._auto_fair(["A"]))                       # Stunden-Takt: kein zweiter Lauf
    assert len(calls) == 1
