"""Regressionstests Setup-Backtest: Tiefen-Diagnose, Lernschleife und erweiterte
Stellschrauben der KI-Revision (analysis.py, detectors.optional_params/apply_filters,
revise.sanitize/build_prompt/_best_base, runner._hist/run_variant).

Kernforderung: Basis-Varianten verhalten sich mit Defaults EXAKT wie vorher
(Rückwärtskompatibilität) – die neuen Schlüssel greifen nur, wenn gesetzt."""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.candles import CandleArray  # noqa: E402
from services.setup_backtest import analysis, detectors, revise, runner, simulator  # noqa: E402
from services.setup_backtest.detectors import Features, Signal  # noqa: E402

M1 = 60_000
T0 = 1_735_689_600_000  # 2025-01-01 00:00 UTC


def _ca(closes, ts0=T0, spread=0.001):
    closes = np.asarray(closes, float)
    n = len(closes)
    op = np.concatenate([[closes[0]], closes[:-1]])
    hi, lo = np.maximum(op, closes) * (1 + spread), np.minimum(op, closes) * (1 - spread)
    return CandleArray(ts0 + np.arange(n) * M1, op, hi, lo, closes, np.full(n, 100.0))


def _features(n_min=12_000, seed=7) -> Features:
    rng = np.random.default_rng(seed)
    closes = 100 + np.cumsum(rng.normal(0, 0.05, n_min)) + 2 * np.sin(np.arange(n_min) / 400)
    return Features(_ca(closes))


def _trade(pnl, side="LONG", reason="sl", sym="BTCUSDT", hour=10, entry=100.0, sl=99.0,
           peak=None, trough=None, bars=12):
    from datetime import datetime, timedelta, timezone
    opened = datetime(2025, 1, 6, hour, 0, tzinfo=timezone.utc)
    return {"realized_pnl": pnl, "side": side, "exit_reason": reason, "symbol": sym,
            "opened_at": opened.isoformat(), "closed_at": (opened + timedelta(minutes=5 * bars)).isoformat(),
            "entry": entry, "initial_sl": sl, "margin_used": 100.0,
            "peak_price": peak if peak is not None else entry, "trough_price": trough if trough is not None else entry}


# ---------------------------------------------------------------- analysis
def test_diagnose_key_figures_and_exits():
    trades = [_trade(2.0, reason="tp", peak=102.5), _trade(-1.0, reason="sl", peak=100.6, trough=99.0),
              _trade(-1.0, reason="sl", side="SHORT", sl=101.0, trough=99.8, peak=101.0),
              _trade(0.5, reason="be", hour=18), _trade(-0.5, reason="time", hour=3)]
    d = analysis.diagnose(trades, fee_rt_pct=0.06)
    assert d["n"] == 5 and d["wr"] == 40 and d["pnl"] == 0.0
    assert d["exits"]["sl"]["n"] == 2 and d["exits"]["tp"]["n"] == 1
    assert d["sides"]["SHORT"]["n"] == 1 and d["sides"]["LONG"]["n"] == 4
    assert d["pf"] == 1.0 and d["fees"] == 0.3
    assert d["max_dd"] == 2.0 and d["loss_streak"] == 2
    assert d["hold_bars"] == 12
    # MFE der Verlierer in R: Long-SL lief 0.6R ins Plus, Short-SL 0.2R, Zeit-Exit 0
    assert d["mfe_losers_r"] == 0.2
    assert d["sl_reached_half_r"] == 50
    assert set(d["hours"]) >= {"08-13", "17-24"}
    assert analysis.diagnose([]) == {"n": 0}


def test_compact_and_describe_roundtrip():
    trades = [_trade(2.0, reason="tp"), _trade(-1.0)]
    full = analysis.diagnose(trades, 0.06)
    small = analysis.compact(full)
    assert small["exits"] == {"tp": 1, "sl": 1} and small["n"] == 2
    lines_full, lines_small = analysis.describe(full, "IS"), analysis.describe(small, "IS")
    assert lines_full[0].startswith("IS: 2 Trades") and lines_small[0].startswith("IS: 2 Trades")
    assert any("Exits: tp=1, sl=1" in x for x in lines_small)
    assert analysis.describe(None, "OOS") == ["OOS: keine Trades"]
    assert analysis.compact(None) == {"n": 0}


def test_score_prefers_oos_and_penalizes_few_trades():
    good = analysis.score({"trades": 20, "pnl": 10}, {"trades": 12, "pnl": 6})
    few = analysis.score({"trades": 20, "pnl": 10}, {"trades": 2, "pnl": 1})
    empty = analysis.score({"trades": 20, "pnl": 10}, {"trades": 0, "pnl": 0})
    assert good > few > empty
    assert analysis.score(None, None) == -1.0


def test_best_entry_and_lessons():
    hist = [
        {"name": "standard", "variant": 0, "params": {"sl_atr": 1.2, "tp_r": 2.0},
         "is": {"trades": 30, "pnl": -3}, "oos": {"trades": 12, "pnl": -2, "winrate": 45}},
        {"name": "KI-Rev.1", "ai": True, "params": {"sl_atr": 1.8, "tp_r": 2.0}, "reason": "SL zu eng",
         "expect": "weniger SL", "is": {"trades": 25, "pnl": 2}, "oos": {"trades": 11, "pnl": 1, "winrate": 52}},
        {"name": "KI-Rev.2", "ai": True, "params": {"sl_atr": 1.8, "tp_r": 1.0},
         "is": {"trades": 25, "pnl": -6}, "oos": {"trades": 11, "pnl": -4, "winrate": 40}},
    ]
    assert analysis.best_entry(hist)["name"] == "KI-Rev.1"
    assert analysis.best_entry([{"name": "alt", "is": {}, "oos": {}}]) is None
    les = analysis.lessons(hist)
    assert len(les) == 2
    assert "sl_atr: 1.2→1.8" in les[0] and "BESSER" in les[0] and "Hypothese: SL zu eng" in les[0]
    assert "tp_r: 2.0→1.0" in les[1] and "SCHLECHTER" in les[1]
    assert analysis.param_diff({"a": 1, "name": "x"}, {"a": 2, "b": 3}) == ["a: 1→2", "b: –→3"]


# ---------------------------------------------------------------- detectors
def test_optional_params_registry():
    for sid in detectors.VARIANTS:
        opt = detectors.optional_params(sid)
        assert {"max_bars", "sides", "vol_min", "vol_max", "hour_from", "hour_to", "htf_trend"} <= set(opt)
        assert ("tp1_r" in opt) == (sid not in detectors.NO_TP1R)
        for k, (d, lo, hi, txt) in opt.items():
            assert lo <= d <= hi and txt
        # kein optionaler Schlüssel kollidiert mit einem Varianten-Schlüssel (Defaults = Altverhalten)
        for v in detectors.VARIANTS[sid]:
            assert not (set(v) & set(opt)), (sid, set(v) & set(opt))
        help_txt = detectors.param_help(sid)
        assert set(help_txt) >= set(opt) | (set(detectors.VARIANTS[sid][0]) - {"name"})
    assert detectors.param_defaults("divergence")["rsi_lo"] == 40
    assert detectors.param_defaults("trend_follow")["sl_atr"] == 0.3
    assert detectors.param_defaults("breakout")["body_atr"] == 0.5


def test_variants_unchanged_with_defaults():
    """Rückwärtskompatibilität: Variante == Variante + explizite Defaults (alle Setups)."""
    f = _features()
    for sid, variants in detectors.VARIANTS.items():
        for idx, v in enumerate(variants):
            a = detectors.run_detector(sid, f, idx)
            b = detectors.run_detector_params(sid, f, {**v, **detectors.param_defaults(sid)})
            assert [(s.idx, s.side, s.sl, s.tp1, s.tpf) for s in a] == \
                   [(s.idx, s.side, s.sl, s.tp1, s.tpf) for s in b], sid


def test_tp1_r_moves_partial_target_only():
    f = _features()
    found = False
    for sid in ("trend_follow", "divergence", "pullback", "squeeze_breakout"):
        base = dict(detectors.VARIANTS[sid][2])
        a = detectors.run_detector_params(sid, f, base)
        if not a:
            continue
        found = True
        b = detectors.run_detector_params(sid, f, {**base, "tp1_r": 0.5})
        assert len(a) == len(b)
        for s1, s2 in zip(a, b):
            assert s1.idx == s2.idx and s1.sl == s2.sl and s1.tpf == s2.tpf
            assert abs(s2.tp1 - s2.entry) < abs(s1.tp1 - s2.entry)
    assert found


def test_apply_filters_sides_hours_vol_trend():
    f = _features()
    sigs = [Signal(i, "LONG" if i % 2 else "SHORT", 100.0, 99.0, 101.0, 102.0) for i in range(300, 1500, 7)]
    assert detectors.apply_filters(f, sigs, {}) is sigs
    longs = detectors.apply_filters(f, sigs, {"sides": 1})
    assert longs and all(s.side == "LONG" for s in longs)
    shorts = detectors.apply_filters(f, sigs, {"sides": -1})
    assert len(longs) + len(shorts) == len(sigs)
    hours = detectors.apply_filters(f, sigs, {"hour_from": 9, "hour_to": 12})
    bl = f.berlin()
    assert hours and all(9 <= bl[s.idx].hour < 12 for s in hours)
    strict = detectors.apply_filters(f, sigs, {"vol_min": 1.0})
    calm = detectors.apply_filters(f, sigs, {"vol_max": 1.0})
    assert len(strict) + len(calm) >= len(sigs) - 5 and len(strict) < len(sigs)
    with_trend = detectors.apply_filters(f, sigs, {"htf_trend": 1})
    against = detectors.apply_filters(f, sigs, {"htf_trend": -1})
    assert len(with_trend) + len(against) <= len(sigs)
    for s in with_trend:
        j = int(f.i60[s.idx])
        assert f.trend60[j] == (1 if s.side == "LONG" else -1)


# ---------------------------------------------------------------- revise
def test_param_ranges_include_optional_keys():
    r = revise.param_ranges("breakout")
    assert r["lookback"] == (24 * revise.RANGE_LO, 48 * revise.RANGE_HI)
    assert r["tp1_r"] == (0.5, 2.5) and r["sides"] == (-1, 1) and r["body_atr"] == (0.2, 1.5)
    assert "tp1_r" not in revise.param_ranges("mean_reversion")
    assert revise.param_ranges("divergence")["rsi_lo"] == (25, 50)


def test_sanitize_optional_keys_and_default_compaction():
    base = dict(detectors.VARIANTS["breakout"][0])
    # Default-Wert für optionalen Schlüssel = keine Änderung
    assert revise.sanitize("breakout", {"tp1_r": 1.0, "sides": 0}, base, 1) is None
    out = revise.sanitize("breakout", {"tp1_r": 0.6, "sides": 1, "max_bars": 5000, "hour_from": 8.4,
                                       "vol_min": 0}, base, 4)
    assert out["tp1_r"] == 0.6 and out["sides"] == 1 and out["max_bars"] == 576
    assert out["hour_from"] == 8 and isinstance(out["sides"], int)
    assert revise.sanitize("breakout", {"htf_trend": -3}, base, 4)["htf_trend"] == -1
    assert "vol_min" not in out            # Default -> nicht aufgenommen
    assert out["name"] == "KI-Rev.4" and out["lookback"] == base["lookback"]
    # Einmal gesetzter optionaler Schlüssel bleibt in der Basis, auch wenn zurück auf Default
    back = revise.sanitize("breakout", {"sides": 0, "sl_atr": 1.5}, out, 5)
    assert back["sides"] == 0 and back["sl_atr"] == 1.5


def test_sanitize_caps_number_of_changes():
    base = dict(detectors.VARIANTS["breakout"][0])
    many = {"sl_atr": 2.0, "tp_r": 3.0, "lookback": 30, "vol_mult": 1.0, "sides": 1, "hour_to": 8, "htf_trend": 1}
    out = revise.sanitize("breakout", many, base, 1)
    eff = {**detectors.param_defaults("breakout"), **{k: v for k, v in base.items() if k != "name"}}
    changed = [k for k, v in out.items() if k != "name" and eff.get(k) != v]
    assert len(changed) == revise.MAX_CHANGES == 4
    assert set(changed) == {"sl_atr", "tp_r", "lookback", "vol_mult"}     # erste 4 in Vorschlags-Reihenfolge
    assert "hour_to" not in out and "htf_trend" not in out and "sides" not in out


def test_lessons_show_defaults_for_new_optional_keys():
    v0 = {"sl_atr": 1.2}
    hist = [{"name": "standard", "params": v0, "is": {"trades": 20, "pnl": -2}, "oos": {"trades": 10, "pnl": -1}},
            {"name": "KI-Rev.1", "ai": True, "params": {**v0, "tp1_r": 0.5},
             "is": {"trades": 20, "pnl": 1}, "oos": {"trades": 10, "pnl": 1}}]
    assert "tp1_r: 1.0→0.5" in analysis.lessons(hist, {"tp1_r": 1.0})[0]
    assert "tp1_r: –→0.5" in analysis.lessons(hist)[0]


def test_lessons_diff_against_named_base_not_previous_entry():
    v0 = {"sl_atr": 1.2, "tp_r": 2.0}
    hist = [{"name": "KI-Rev.1", "ai": True, "params": {**v0, "sl_atr": 1.8},
             "is": {"trades": 20, "pnl": 1}, "oos": {"trades": 10, "pnl": 1}},
            {"name": "aggressiv", "params": {"sl_atr": 1.0, "tp_r": 2.5},
             "is": {"trades": 40, "pnl": -4}, "oos": {"trades": 20, "pnl": -3}},
            {"name": "KI-Rev.2", "ai": True, "base": "KI-Rev.1", "params": {**v0, "sl_atr": 1.8, "tp_r": 1.5},
             "is": {"trades": 20, "pnl": 2}, "oos": {"trades": 10, "pnl": 2}},
            {"name": "KI-Rev.2", "ai": True, "params": {**v0, "sl_atr": 1.8, "tp_r": 1.5},
             "is": {"trades": 20, "pnl": 2}, "oos": {"trades": 10, "pnl": 2}}]
    les = analysis.lessons(hist)
    assert les[1].startswith("- KI-Rev.2 (tp_r: 2.0→1.5 von KI-Rev.1)")
    assert "Bestätigungslauf" in les[2]


def test_build_prompt_contains_diagnosis_lessons_and_help():
    base = {**detectors.VARIANTS["breakout"][0]}
    d = analysis.compact(analysis.diagnose([_trade(-1.0, peak=100.7), _trade(1.5, reason="tp")], 0.06))
    hist = [{"name": "standard", "variant": 0, "params": {k: v for k, v in base.items() if k != "name"},
             "is": {"trades": 12, "winrate": 40, "pnl": -3.2}, "oos": {"trades": 5, "winrate": 60, "pnl": 1.1},
             "diag": {"is": d, "oos": d}},
            {"name": "KI-Rev.1", "ai": True, "params": {**{k: v for k, v in base.items() if k != "name"}, "sl_atr": 2.0},
             "reason": "SL zu eng", "is": {"trades": 10, "pnl": -5}, "oos": {"trades": 5, "pnl": -2}}]
    p = revise.build_prompt("crypto", "breakout", "Breakout-Regel", base, hist, runner.rules_for_prompt())
    assert "DIAGNOSE der Basis" in p and "In-Sample: 2 Trades" in p and "Exits:" in p
    assert "LERNSCHLEIFE" in p and "sl_atr: 1.2→2.0" in p and "SCHLECHTER" in p
    assert "tp1_r: 0.5–2.5 (Default 1.0)" in p and "Teilgewinn" in p
    assert "Score" in p and "Krypto" in p


def test_best_base_prefers_best_scored_history_entry():
    v0 = {k: v for k, v in detectors.VARIANTS["breakout"][0].items() if k != "name"}
    hist = [{"name": "standard", "params": v0, "is": {"trades": 30, "pnl": -3}, "oos": {"trades": 12, "pnl": -2}},
            {"name": "KI-Rev.1", "ai": True, "params": {**v0, "sl_atr": 1.8},
             "is": {"trades": 25, "pnl": 2}, "oos": {"trades": 11, "pnl": 1}},
            {"name": "KI-Rev.2", "ai": True, "params": {**v0, "sl_atr": 2.4},
             "is": {"trades": 25, "pnl": -6}, "oos": {"trades": 11, "pnl": -4}}]
    entry = {"ai_proposal": {**v0, "sl_atr": 2.4, "name": "KI-Rev.2"}}
    best = revise._best_base("breakout", entry, hist)
    assert best["sl_atr"] == 1.8 and best["name"] == "KI-Rev.1"
    # Alt-Historie ohne Parameter: Fallback auf vorgemerkten Vorschlag
    legacy = [{"name": "standard", "variant": 0, "is": {"trades": 30, "pnl": -3}, "oos": {"trades": 12, "pnl": -2}}]
    assert revise._best_base("breakout", entry, legacy)["sl_atr"] == 2.4
    assert revise._best_base("breakout", {}, legacy)["name"] == "standard"


# ---------------------------------------------------------------- runner
def test_run_variant_reports_diag_effective_params_and_max_bars():
    f = _features()
    feats = {"BTCUSDT": f}
    split = int(f.c5.ts[int(len(f.c5) * 0.7)])
    base = detectors.run_variant if False else None  # noqa: F841 (Lesbarkeit)
    res = runner.run_variant("mean_reversion", feats, 2, "crypto", split, "job")
    assert set(res["diag"]) == {"is", "oos"} and res["effective_params"] == \
        {k: v for k, v in detectors.VARIANTS["mean_reversion"][2].items() if k != "name"}
    assert res["params"] is None
    h = runner._hist(res, 2, ai=True, reason="r", expect="e")
    assert h["params"] == res["effective_params"] and h["ai"] and h["reason"] == "r"
    assert set(h["diag"]) == {"is", "oos"}
    # Zeit-Exit: kürzeres max_bars darf keine längeren Trades erzeugen
    p = {**detectors.VARIANTS["mean_reversion"][2], "max_bars": 24, "name": "KI-Rev.1"}
    short = runner.run_variant("mean_reversion", feats, 2, "crypto", split, "job", params=p)
    assert short["effective_params"]["max_bars"] == 24
    for t in short["oos_trades"]:
        assert analysis._bars(t) <= 24 + 1


def test_simulate_max_bars_override():
    closes = np.full(400, 100.0)
    c = CandleArray(T0 + np.arange(400) * 5 * M1, closes, closes + 0.01, closes - 0.01, closes, np.ones(400))
    sig = Signal(10, "LONG", 100.0, 98.0, 103.0, 105.0)
    assert simulator.simulate(c, sig, 0.0)["exit_idx"] == 10 + simulator.MAX_BARS
    assert simulator.simulate(c, sig, 0.0, max_bars=24)["exit_idx"] == 34
