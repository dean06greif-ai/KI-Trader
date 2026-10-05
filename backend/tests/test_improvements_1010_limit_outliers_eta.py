"""Regressionstests 10/2026: realistische Limit-Fills, Ausreißer-Filter für
dynamische Strategien/Regime-Lab, genauere Restzeit, Asset-Filter im Optimizer,
Worker-Fix "No module named 'telegram'" und Kerzen-Cache-Budget."""
import asyncio
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


# ---------------- Limit-Order-Simulation ----------------
def _candles(n=200, signal_at=100, after=None):
    rows = []
    for i in range(n):
        rows.append({"timestamp": 1_700_000_000_000 + i * 60000, "open": 100.0, "high": 100.05,
                     "low": 99.95, "close": 100.0, "volume": 1.0})
    for j, c in enumerate(after or []):
        rows[signal_at + 1 + j].update(c)
    return rows


def _sim(candles, cfg_extra, signal_at=100):
    from services.backtester import simulate_pair
    from services.bitunix_trade import DEFAULT_COIN_CFG
    cfg = {**DEFAULT_COIN_CFG, "entry_order_type": "limit", "limit_expiry_bars": 3,
           "sl_mode": "fixed", "sl_fixed_percent": 1.0, **cfg_extra}
    sig = {"type": "LONG", "entry_price": 100.0}
    return simulate_pair(None, candles, "BTCUSDT", {}, cfg, collect_trades=True,
                         signal_provider=lambda i: sig if i == signal_at else None)


def test_limit_fill_pure_functions():
    from services import limit_fill as lf
    assert lf.limit_price("LONG", 100, 0.1) == pytest.approx(99.9)
    assert lf.limit_price("SHORT", 100, 0.1) == pytest.approx(100.1)
    # reine Berührung reicht realistisch nicht, Durchbruch schon, Lücke -> Open
    assert lf.fill_price("LONG", 100, 100.2, 100.5, 100.0, 0.0001) is None
    assert lf.fill_price("LONG", 100, 100.2, 100.5, 99.98, 0.0001) == 100
    assert lf.fill_price("LONG", 100, 99.5, 100.5, 99.4, 0.0001) == 99.5
    assert lf.fill_price("SHORT", 100, 99.8, 100.0, 99.5, 0.0001) is None
    assert lf.fill_price("LONG", 100, 100.2, 100.5, 100.0, 0.0) == 100   # touch-Modus
    assert lf.params({})["mode"] == "realistic" and lf.params({"limit_fill_mode": "touch"})["penetration"] == 0


def test_limit_touch_only_is_not_filled_realistically():
    rising = [{"open": 100.2, "high": 101.0, "low": 100.0, "close": 100.6}] * 5
    touch = _sim(_candles(after=rising), {"limit_fill_mode": "touch"})
    real = _sim(_candles(after=rising), {})
    assert touch["limit_filled"] == 1 and touch["trades"] == 1
    assert real["limit_filled"] == 0 and real["limit_expired"] == 1 and real["trades"] == 0


def test_limit_stop_in_fill_bar_counts_as_loss():
    crash = [{"open": 100.0, "high": 100.1, "low": 98.0, "close": 98.5}]
    real = _sim(_candles(after=crash), {})
    assert real["limit_filled"] == 1 and real["trades"] == 1 and real["losses"] == 1
    touch = _sim(_candles(after=crash), {"limit_fill_mode": "touch"})
    assert touch["limit_filled"] == 1   # altes Verhalten: Fill-Kerze ohne Stop-Prüfung


def test_market_entry_unchanged():
    from services.backtester import simulate_pair
    from services.bitunix_trade import DEFAULT_COIN_CFG
    c = _candles(after=[{"open": 100.0, "high": 104.0, "low": 99.9, "close": 103.0}] * 5)
    sig = {"type": "LONG", "entry_price": 100.0}
    r = simulate_pair(None, c, "BTCUSDT", {}, {**DEFAULT_COIN_CFG, "entry_order_type": "market"},
                      signal_provider=lambda i: sig if i == 100 else None)
    assert r["trades"] == 1 and r["limit_filled"] == 0


def test_new_limit_keys_are_passed_through():
    from services.backtester import _pair_trade_cfg
    out = _pair_trade_cfg({}, {"limit_offset_pct": 0.1, "limit_fill_mode": "touch"})
    assert out.get("limit_offset_pct") == 0.1 and out.get("limit_fill_mode") == "touch"
    from services.optimizer import TRADE_SPACES
    assert "limit_offset_pct" in TRADE_SPACES["entry_order"]


# ---------------- Ausreißer-Filter Regime ----------------
SCREENSHOT_CAND = {"score": -5549.7, "validation_passed": True,
                   "metrics": {"pnl": -6139.1, "trades": 2765},
                   "validation": {"pnl": 1112.0, "trades": 1348}}


def test_train_negative_candidate_is_not_robust():
    from services import regime_outliers as ro
    assert "train_negative" in ro.candidate_flags(SCREENSHOT_CAND)
    assert not ro.robust_validated(SCREENSHOT_CAND)
    ok = {"score": 50, "validation_passed": True, "metrics": {"pnl": 60, "trades": 40},
          "validation": {"pnl": 20, "trades": 15}}
    assert ro.robust_validated(ok)


def test_workbench_rank_rejects_screenshot_candidate():
    from services import dynamic_workbench as wb
    prev = {"score": 120.0, "validation_passed": False, "metrics": {"pnl": 130, "trades": 50}}
    assert wb.candidate_rank(SCREENSHOT_CAND) < wb.candidate_rank(prev)
    entry = wb.rejected_entry("Seitwärts", SCREENSHOT_CAND)
    assert entry["rejected"] and entry["validation_passed"] is False and entry["flags"]


def test_outlier_dominance_flags():
    from services import regime_outliers as ro
    pnls = [300, 250] + [-15] * 30
    d = ro.dominance(pnls)
    assert d["dominated"] and d["pnl_ex_top"] < 0
    assert not ro.dominance([10] * 20)["dominated"]
    from services.dynamic_strategy import metrics_from_rows
    rows = [{"closed": f"2026-01-01T00:{i:02d}:00", "pnl": p} for i, p in enumerate(pnls)]
    m = metrics_from_rows(rows, 100)
    assert m["pnl_ex_top2"] == d["pnl_ex_top"] and m["pnl"] > 0
    cand = {"validation_passed": True, "metrics": {"pnl": 10, "trades": 30},
            "validation": {**m}}
    assert "test_outlier_dominated" in ro.candidate_flags(cand) and not ro.robust_validated(cand)


def test_regime_report_and_recommendation():
    from services import regime_outliers as ro
    rows = []
    for s in ("AUSDT", "BUSDT", "CUSDT", "DUSDT"):
        rows += [{"symbol": s, "pnl": 5.0}] * 10
    rows += [{"symbol": "XUSDT", "pnl": -15.0}] * 10
    rep = ro.regime_report(rows)
    assert rep["outlier_assets"] == ["XUSDT"] and rep["pnl_without_outliers"] == 200.0
    rec = ro.adjust_recommendation({"action": "trade", "text": "handeln"}, rep)
    assert "XUSDT".replace("USDT", "") in rec["text"]
    dom_rows = [{"symbol": "A", "pnl": 400}, {"symbol": "A", "pnl": 300}] + [{"symbol": "A", "pnl": -10}] * 20
    rec2 = ro.adjust_recommendation({"action": "trade", "text": "x"}, ro.regime_report(dom_rows))
    assert rec2["action"] == "caution"


def test_detection_outliers():
    from services import regime_outliers as ro
    rows = [{"symbol": s, "holdout_direction_pct": v}
            for s, v in (("A", 80), ("B", 82), ("C", 79), ("D", 81), ("E", 45))]
    out = ro.detection_outliers(rows)
    assert out and out["symbols"] == ["E"]
    assert ro.detection_outliers(rows[:3]) is None
    assert ro.detection_outliers([{**r, "holdout_direction_pct": 80} for r in rows]) is None


# ---------------- Restzeit ----------------
def test_eta_grows_when_stalled_instead_of_sticking_at_seconds():
    from services import job_eta
    job = {}
    assert job_eta.estimate(job, 100, 50, 1000.0) is not None
    for t, p in ((1100.0, 70), (1200.0, 90), (1300.0, 99)):
        job_eta.estimate(job, t - 900, p, t)
    stalled = job_eta.estimate(job, 1300 - 900 + 600, 99, 1900.0)
    assert stalled >= 250       # 10 min ohne Fortschritt -> nicht "~5 s"
    assert job_eta.estimate({}, 10, 1, 0.0) is None


def test_job_public_hides_eta_state():
    from core.utils import _job_public
    from datetime import datetime, timezone
    j = {"status": "running", "progress": 40, "created_at": datetime.now(timezone.utc).isoformat()}
    out = _job_public(j)
    assert "_eta" not in out and "_eta" in j


# ---------------- Asset-Filter Optimizer ----------------
def test_regime_breakdown_per_symbol_consistent():
    from services import robustness
    from services.candles import CandleArray  # noqa: F401
    hist = {s: [{"timestamp": i * 60000, "open": 100 + i * 0.01, "high": 100 + i * 0.01 + 0.05,
                 "low": 100 + i * 0.01 - 0.05, "close": 100 + i * 0.01, "volume": 1}
                for i in range(600)] for s in ("AUSDT", "BUSDT")}
    trades = [("AUSDT", 300 * 60000, 2.0), ("BUSDT", 400 * 60000, -1.0), ("AUSDT", 500 * 60000, 1.0)]
    total, per = robustness.regime_breakdown_with_symbols(trades, hist)
    assert total == robustness.regime_breakdown(trades, hist)
    assert sum(v["trades"] for v in total.values()) == 3
    assert sum(v["trades"] for v in per["AUSDT"].values()) == 2


def test_asset_insight_keeps_outlier_fields():
    from services.optimizer import asset_insight
    a = asset_insight({"pnl": 1, "trades": 2, "win_rate": 50, "max_drawdown": 3})
    assert {"pnl", "trades", "win_rate", "max_drawdown"} <= set(a)


# ---------------- Worker ohne python-telegram-bot ----------------
def test_core_state_imports_without_telegram_lib():
    code = ("import sys\nsys.modules['telegram']=None\nsys.modules['telegram.constants']=None\n"
            "from core import state\nfrom services import backtester, dynamic_backtest\nprint('ok')")
    r = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0 and "ok" in r.stdout, r.stderr[-500:]


# ---------------- Kerzen-Cache ----------------
def test_candle_cache_evicts_after_disk_hydration(monkeypatch):
    from services import candle_cache as cc
    from services.candles import CandleArray
    big = CandleArray.from_dicts([{"timestamp": 1_700_000_000_000 + i * 60000, "open": 1, "high": 1,
                                  "low": 1, "close": 1, "volume": 1} for i in range(300)])
    monkeypatch.setattr(cc, "MAX_CANDLES_IN_MEMORY", 500)
    monkeypatch.setattr(cc, "DISK_ENABLED", False)
    saved = dict(cc._MEM)
    cc._MEM.clear()
    try:
        async def go():
            for sym in ("AUSDT", "BUSDT", "CUSDT"):
                cc._MEM.pop(sym, None)
                async with cc._LOCK:
                    entry = {"candles": big, "last_refresh": 0, "used_at": len(cc._MEM)}
                    cc._MEM[sym] = entry
                    await cc._evict_if_needed_async(keep=sym)
        asyncio.run(go())
        assert cc._total_candles() <= 500 and "CUSDT" in cc._MEM
    finally:
        cc._MEM.clear()
        cc._MEM.update(saved)


# ---------------- Backtester: Robustheits-Tests aus den Trades ----------------
def _bt_trades():
    rows = []
    base = 1_700_000_000_000
    for i in range(60):
        sym = ("AUSDT", "BUSDT", "CUSDT", "XUSDT")[i % 4]
        pnl = -6.0 if sym == "XUSDT" else (3.0 if i % 3 else -1.0)
        from datetime import datetime, timezone
        closed = datetime.fromtimestamp((base + i * 3600_000 * 4) / 1000, timezone.utc).isoformat()
        rows.append({"strategy_id": "s1", "strategy_name": "S1", "symbol": sym, "closed": closed,
                     "pnl": pnl, "fees": 0.5, "market_phase": ("bull", "bear", "sideways")[i % 3]})
    return rows, base, base + 60 * 3600_000 * 4


def test_backtest_robustness_from_trades():
    from services import backtest_robustness as br
    rows, a, b = _bt_trades()
    body = {"walk_forward": {"enabled": True, "train_pct": 70}, "dd_filter": {"enabled": True},
            "constancy": {"enabled": True, "chunk_days": 2}, "stress_test": {"enabled": True, "cost_multiplier": 2},
            "monte_carlo": {"enabled": True, "runs": 50}, "regime_analysis": {"enabled": True}}
    out = br.evaluate(rows, body, 100, a, b)
    e = out["per_strategy"]["s1"]
    total = round(sum(r["pnl"] for r in rows), 2)
    assert e["metrics"]["pnl"] == total
    assert e["train_metrics"]["trades"] + e["test_metrics"]["trades"] == 60
    assert e["stress"]["pnl"] == round(total - 0.5 * 60, 2)
    assert sum(v["trades"] for v in e["regimes"].values()) == 60
    assert e["outlier_variant"]["excluded"] == ["XUSDT"]
    assert e["per_symbol"]["AUSDT"]["regimes"] and "test" in e["per_symbol"]["AUSDT"]
    assert {c["id"] for c in e["checks"]} >= {"walk_forward", "dd_filter", "constancy", "stress", "monte_carlo"}
    assert e["recommendation"]["level"]
    rolling = br.evaluate(rows, {"walk_forward": {"enabled": True, "mode": "rolling", "windows": 3}}, 100, a, b)
    assert len(rolling["per_strategy"]["s1"]["wf_windows"]) == 3


def test_simulate_pair_tags_market_phase():
    from services.backtester import simulate_pair
    from services.bitunix_trade import DEFAULT_COIN_CFG
    c = _candles(after=[{"open": 100.0, "high": 104.0, "low": 99.9, "close": 103.0}] * 5)
    sig = {"type": "LONG", "entry_price": 100.0}
    r = simulate_pair(None, c, "BTCUSDT", {}, {**DEFAULT_COIN_CFG}, collect_trades=True,
                      signal_provider=lambda i: sig if i == 100 else None)
    assert r["all_trades"] and r["all_trades"][0]["market_phase"] in ("bull", "bear", "sideways")


# ---------------- Dynamisch: Such-Modus je Phase ----------------
def test_targets_for_refine_per_phase_modes():
    from services import dynamic_workbench as wb
    doc = {"model": {"regimes": [{"id": 0}, {"id": 1}, {"id": 2}]},
           "regime_strategies": {"0": "rsi_only", "1": "rsi_only", "2": "rsi_only"},
           "sub_strategies": {"2": {"long_rules": [1]}}}
    t = wb.targets_for_refine(doc, [0, 1, 2], "params", {"1": "discovery"})
    assert t[0]["mode"] == "params" and t[1]["mode"] == "discovery" and t[2]["mode"] == "combo"
    assert wb.targets_for_refine(doc, [0], "params")[0]["mode"] == "params"   # rückwärtskompatibel


def test_optimizer_best_snapshot_is_independent():
    from services.optimizer import _snap
    d = {"long_rules": [{"a": 1}]}
    s = _snap(d)
    d["long_rules"].append({"b": 2})
    assert len(s["long_rules"]) == 1
