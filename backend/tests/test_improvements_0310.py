"""Regressionstests 03.10.2026: Iterations-Grenze, kein Cloud-Fallback beim lokalen
Ergebnis-Backtest, Zusatz-Tests (Robustheit) für dynamische Strategien,
Drawdown-Filter je Regime, KI Closed-Loop bevorzugt freien lokalen Worker."""
import asyncio
import re
from pathlib import Path

import pytest

from core import search_limits as sl
from services import dynamic_robustness as drb
from services import dynamic_workbench as wb

ROOT = Path(__file__).resolve().parents[2]


# ---------------- Iterations-Grenze ----------------
def test_clamp_iterations_cloud_and_local(monkeypatch):
    monkeypatch.delenv("KI_LOCAL_WORKER", raising=False)
    monkeypatch.delenv("SEARCH_MAX_ITERATIONS_CLOUD", raising=False)
    monkeypatch.delenv("SEARCH_MAX_ITERATIONS_LOCAL", raising=False)
    assert sl.clamp_iterations(1000, "cloud") == 1000          # vorher still auf 500 gekappt
    assert sl.clamp_iterations(99999, "cloud") == sl.DEFAULT_MAX_CLOUD
    assert sl.clamp_iterations(99999, "local") == sl.DEFAULT_MAX_LOCAL
    assert sl.clamp_iterations(None) == sl.DEFAULT_ITERATIONS
    assert sl.clamp_iterations(1, "cloud") == 5
    assert sl.clamp_iterations(0, "cloud", minimum=0) == 0


def test_clamp_iterations_env_override_and_worker(monkeypatch):
    monkeypatch.setenv("SEARCH_MAX_ITERATIONS_CLOUD", "300")
    monkeypatch.delenv("KI_LOCAL_WORKER", raising=False)
    assert sl.clamp_iterations(1000, "cloud") == 300
    monkeypatch.setenv("KI_LOCAL_WORKER", "1")               # Worker ohne execution im Body
    assert sl.clamp_iterations(5000, None) == 5000


def test_frontend_limits_mirror_backend():
    js = (ROOT / "frontend/src/constants/searchLimits.js").read_text()
    m = re.search(r"cloud:\s*(\d+),\s*local:\s*(\d+)", js)
    assert m and int(m.group(1)) == sl.DEFAULT_MAX_CLOUD and int(m.group(2)) == sl.DEFAULT_MAX_LOCAL


def test_no_hardcoded_500_iteration_caps_left():
    for rel in ("backend/services/optimizer.py", "backend/services/regime_opt.py",
                "backend/services/dynamic_workbench.py"):
        src = (ROOT / rel).read_text()
        assert not re.search(r"iterations.*,\s*500\)", src), rel


def test_search_body_uses_central_limit(monkeypatch):
    monkeypatch.delenv("KI_LOCAL_WORKER", raising=False)
    b = wb._search_body({"iterations": 1500, "execution": "cloud"}, "a", 0, "params", "s", 1)
    assert b["iterations"] == 1500
    b = wb._search_body({"iterations": 1990, "execution": "cloud"}, "a", 0, "params", "s", 3)
    assert b["iterations"] == sl.DEFAULT_MAX_CLOUD             # Runden-Aufschlag gedeckelt
    b = wb._search_body({"iterations": 5000, "execution": "local"}, "a", 0, "params", "s", 1)
    assert b["iterations"] == 5000


# ---------------- Lokaler Ergebnis-Backtest ohne Cloud-Fallback ----------------
def test_local_result_backtest_outdated_worker_raises(monkeypatch):
    from services import local_exec
    monkeypatch.setattr(local_exec, "worker_online", lambda: True)
    monkeypatch.setattr(local_exec, "worker_supports", lambda v, w=None: False)
    with pytest.raises(wb.LocalBacktestUnavailable) as e:
        asyncio.run(wb._local_result_backtest({"id": "j", "params": {}}, {"id": "d"}, 30, {},
                                              {"execution": "local"}, {}))
    assert "zu alt" in str(e.value)


def test_result_backtest_local_never_loads_in_cloud(monkeypatch):
    from services import dynamic_backtest, local_exec
    monkeypatch.setattr(local_exec, "worker_online", lambda: False)
    monkeypatch.setattr(local_exec, "worker_supports", lambda v, w=None: False)

    async def boom(*a, **k):
        raise AssertionError("Cloud-Simulation darf bei lokaler Ausführung nicht laufen")
    monkeypatch.setattr(dynamic_backtest, "simulate_dynamic", boom)

    class _Coll:
        async def find_one(self, *a, **k):
            return {"id": "d", "symbols": ["BTCUSDT"], "days": 30}

    class _Db:
        dynamic_strategies = _Coll()
        regime_analyses = _Coll()

    job = {"id": "j", "params": {}, "log": []}
    res = asyncio.run(wb._result_backtest(job, "d", {"execution": "local", "analysis_id": "a"},
                                          {"db": _Db(), "default_cfg": {}}))
    assert res["skipped"] == "local_unavailable" and "kein lokaler Worker" in res["error"]


# ---------------- Zusatz-Tests (Robustheit) ----------------
def _breakdown():
    pts = [{"t": f"2026-0{1 + i // 10}-{1 + i % 10:02d}T10:00:00+00:00", "pnl": p, "regime": i % 2}
           for i, p in enumerate([5, -2, 4, 3, -1, 6, 2, -3, 4, 5, 3, -2, 4, 2, 1, -1, 3, 2, 4, -2])]
    pnl = sum(p["pnl"] for p in pts)
    return {"total": {"pnl": pnl, "fees": 6.0, "max_drawdown": 3.0, "trades": len(pts)},
            "points": pts,
            "regimes": [{"regime": 0, "label": "Up", "traded": True,
                         "metrics": {"pnl": 40, "fees": 3, "max_drawdown": 2, "trades": 10}},
                        {"regime": 1, "label": "Down", "traded": True,
                         "metrics": {"pnl": 1, "fees": 3, "max_drawdown": 5, "trades": 10}},
                        {"regime": 2, "label": "Flat", "traded": False, "metrics": None}]}


def test_robustness_parse_off_by_default():
    assert drb.parse(None) is None
    assert drb.parse({"dd_filter": {"enabled": False}}) is None


def test_robustness_evaluate_all_checks():
    cfg = drb.parse({"dd_filter": {"enabled": True, "max_dd_pct": 40},
                     "monte_carlo": {"enabled": True, "runs": 100},
                     "stress_test": {"enabled": True, "cost_multiplier": 2},
                     "constancy": {"enabled": True, "chunk_days": 10, "max_deviation_pct": 1000}})
    res = drb.evaluate(_breakdown(), cfg)
    keys = [c["key"] for c in res["checks"]]
    assert keys == ["dd", "mc", "stress", "ct"]
    assert res["stress"]["pnl"] == round(_breakdown()["total"]["pnl"] - 6.0, 2)
    assert res["monte_carlo"]["runs"] == 100
    assert res["trades"] == 20
    per = {r["label"]: r for r in res["per_regime"]}
    assert set(per) == {"Up", "Down"}                          # ungehandelte Regime fehlen
    assert per["Up"]["dd_pass"] is True and per["Down"]["dd_pass"] is False
    assert per["Down"]["stress_pass"] is False                 # 1 - 3*(2-1) < 0
    assert res["passed"] == all(c["passed"] for c in res["checks"])


def test_robustness_skips_errors():
    cfg = drb.parse({"monte_carlo": {"enabled": True}})
    assert drb.evaluate({"error": "x"}, cfg) is None
    assert drb.evaluate(None, cfg) is None


def test_result_backtest_attaches_robustness(monkeypatch):
    async def raw(job, did, p, deps):
        return _breakdown()
    monkeypatch.setattr(wb, "_result_backtest_raw", raw)
    job = {"id": "j", "log": []}
    res = asyncio.run(wb._result_backtest(job, "d", {"robustness": {"stress_test": {"enabled": True}}}, {}))
    assert res["robustness"]["checks"][0]["key"] == "stress"
    res = asyncio.run(wb._result_backtest(job, "d", {}, {}))
    assert "robustness" not in res                             # aus = unverändert


# ---------------- Drawdown-Filter je Regime ----------------
def test_candidate_rank_dd_filter_backward_compatible():
    assert wb.candidate_rank({"validation_passed": True, "score": 1}) > \
        wb.candidate_rank({"validation_passed": False, "score": 99})
    assert wb.candidate_rank({"score": 5}) > wb.candidate_rank({"score": 50, "dd_pass": False})
    assert wb.candidate_rank({"score": 5, "dd_pass": True}) == wb.candidate_rank({"score": 5})
    assert wb.candidate_rank(None) < wb.candidate_rank({"score": -5, "dd_pass": False})


def test_candidate_from_result_carries_dd():
    c = wb.candidate_from_result({"mode": "params"}, {"score": 1, "dd_pass": False, "dd_ratio_pct": 80}, "j")
    assert c["dd_pass"] is False and c["dd_ratio_pct"] == 80
    assert "dd_pass" not in wb.candidate_from_result({"mode": "params"}, {"score": 1}, "j")


def test_dd_filter_passthrough_and_router_keys():
    assert "dd_filter" in wb.PASSTHROUGH_KEYS
    src = (ROOT / "backend/routers/dynamic.py").read_text()
    assert '"dd_filter", "robustness")' in src
    ro = (ROOT / "backend/services/regime_opt.py").read_text()
    assert 'entry["dd_pass"], entry["dd_ratio_pct"] = robustness.dd_check(' in ro


# ---------------- KI Closed-Loop: freier lokaler Worker ----------------
def test_idle_worker_online(monkeypatch):
    from services import local_exec
    monkeypatch.setattr(local_exec, "WORKERS", {"a": {"last_seen": local_exec._now(), "running_jobs": ["x"]}})
    assert local_exec.idle_worker_online() is False
    monkeypatch.setattr(local_exec, "WORKERS", {"a": {"last_seen": local_exec._now(), "running_jobs": []}})
    assert local_exec.idle_worker_online() is True
    monkeypatch.setattr(local_exec, "WORKERS", {"a": {"last_seen": 0, "running_jobs": []}})
    assert local_exec.idle_worker_online() is False


def test_closed_loop_prefers_local_source():
    src = (ROOT / "backend/services/ai_closed_loop.py").read_text()
    assert "local_exec.idle_worker_online()" in src and 'enqueue_compute("optimizer"' in src


# ---------------- Frontend-Struktur: KI-Trader-Lab ----------------
def test_tools_menu_order_and_backtester_tab_removed():
    hdr = (ROOT / "frontend/src/components/Header.js").read_text()
    order = [hdr.index(t) for t in ("tools-menu-backtester", "tools-menu-optimizer",
                                    "tools-menu-regime-lab", "tools-menu-ai-trader-lab",
                                    "tools-menu-series")]
    assert order == sorted(order)
    bt = (ROOT / "frontend/src/components/Backtester.js").read_text()
    assert "bt-tab-ai" not in bt and "AITraderSeeding" not in bt
    lab = (ROOT / "frontend/src/components/AITraderLab.js").read_text()
    assert "<AITraderSeeding />" in lab


def test_worker_sets_local_marker():
    src = (ROOT / "local_worker/worker.py").read_text()
    assert 'os.environ.setdefault("KI_LOCAL_WORKER", "1")' in src


# ---------------- Copilot: Kontext dynamischer Strategien ----------------
def test_copilot_dynamic_strategy_text_full_mapping():
    from services import copilot_dynamic_context as cdc
    doc = {"id": "dyn_x", "name": "Dyn X", "timeframe": "1h", "symbols": ["BTCUSDT", "ETHUSDT"],
           "settings": {"analysis_id": "a1", "skipped_regimes": [2], "confidence_min": 70},
           "verdict": {"dynamic_better": True}}
    regimes = [{"id": 0, "label": "Aufwärtstrend", "traded": True, "strategy_id": "ema",
                "strategy_name": "EMA Cross", "strategy_params": {"fast": 9}, "trade_params": {"sl": 1.5}},
               {"id": 2, "label": "Seitwärts", "traded": False}]
    perf = {"regimes": [{"regime": 0, "live": {"trades": 4, "pnl": 12.5}, "verdict": {"text": "ok"}}],
            "total": {"trades": 4, "pnl": 12.5}, "unknown_regime_trades": 0}
    txt = cdc.strategy_text(doc, regimes, {"BTCUSDT": {"label": "Aufwärtstrend", "confidence": 81}},
                            "validated", perf)
    for frag in ("Dyn X [dyn_x]", "Status validated", "EMA Cross [ema]", '"fast": 9', '"sl": 1.5',
                 "R2 Seitwärts: NICHT HANDELN (Verlust im Walk-Forward)", "BTCUSDT: Aufwärtstrend (81 %)",
                 "Live/Paper: trades=4, pnl=12.5", "dynamic_better"):
        assert frag in txt, frag


def test_copilot_workbench_text_includes_result_and_checks():
    from services import copilot_dynamic_context as cdc
    job = {"id": "wb_1", "kind": "refine", "status": "done", "round": 3, "progress": 100,
           "params": {"iterations": 800, "execution": "local", "robustness": {"monte_carlo": {"enabled": True}}},
           "regimes": {"0": {"label": "Up", "score": 1.2, "pnl": 30}},
           "result": {"dynamic_id": "dyn_new", "walkforward": {"verdict": {"dynamic_better": False}},
                      "backtest": {"total": {"trades": 10, "pnl": 5},
                                   "regimes": [{"label": "Up", "traded": True, "metrics": {"pnl": 5}}],
                                   "robustness": {"passed": False, "checks": [
                                       {"label": "Monte-Carlo", "passed": False, "detail": "p95 hoch"}]}}}}
    txt = cdc.workbench_text(job)
    for frag in ("Status done", '"iterations": 800', "Up", "dynamic_better", "trades=10, pnl=5",
                 "Zusatz-Tests: NICHT bestanden", "Monte-Carlo: ✗ p95 hoch", "dyn_new"):
        assert frag in txt, frag
    assert cdc.workbench_text(None) == ""


def test_copilot_context_block_wires_dynamic_context():
    src = (ROOT / "backend/services/strategy_copilot.py").read_text()
    assert "copilot_dynamic_context.build_block" in src and "DYNAMISCHE\n  STRATEGIEN" in src
    opt = (ROOT / "frontend/src/components/Optimizer.js").read_text()
    assert "dynamic_job_id: wbCtxRef.current.job_id" in opt
    bt = (ROOT / "frontend/src/components/Backtester.js").read_text()
    assert "dynamic_ids: selStrats.filter" in bt


# ---------------- 03.10 (3): Min-Trades-Tooltip, Branding, Deploy ----------------
def test_min_trades_tooltip_mirrors_backend_rules():
    from services import dynamic_workbench as wbm
    js = (ROOT / "frontend/src/components/DynamicMinTrades.js").read_text()
    assert f"MIN_TRADES_SKIP = {wbm.MIN_TRADES_SKIP}" in js
    assert "Math.max(Math.floor((minTrades || 0) * 0.4), 3)" in js       # == dynamic_strategy min_val
    src = (ROOT / "backend/services/regime_opt.py").read_text()
    assert "min_val = max(int(min_trades * 0.4), 3)" in src


def test_branding_without_mm_suffix_and_icons_present():
    pub = ROOT / "frontend/public"
    assert "(MM)" not in (pub / "index.html").read_text()
    assert "(MM)" not in (pub / "manifest.json").read_text()
    assert "(MM)" not in (ROOT / "frontend/src/components/Header.js").read_text()
    for f in ("favicon.svg", "favicon.ico", "apple-touch-icon.png", "icon-192.png", "icon-512.png",
              "icon-maskable-512.png"):
        assert (pub / f).exists(), f


def test_frontend_yarn_lock_committed():
    gi = (ROOT / ".gitignore").read_text().splitlines()
    assert "frontend/yarn.lock" not in gi and (ROOT / "frontend/yarn.lock").exists()


def test_big_panels_lazy_loaded_with_styles_eager():
    app = (ROOT / "frontend/src/App.js").read_text()
    for c in ("Backtester", "Optimizer", "RegimeLab", "AITraderLab", "JobSeriesPanel", "SettingsPanel"):
        assert f"const {c} = lazyWithReload(() => import('./components/{c}'))" in app, c
        assert f"import {c} from" not in app, c
    assert "<Suspense" in app and "import './lazyPanelStyles';" in app
    styles = (ROOT / "frontend/src/lazyPanelStyles.js").read_text()
    assert "Backtester.css" in styles and "Optimizer.css" in styles
