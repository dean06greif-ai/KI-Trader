"""Endlos-Suche (deep_explore): Rettungs-Verfeinerung, TP/SL-Suche,
Kombi-Größen nach Trade-Anteil, Wiederholung statt Abbruch bei Ausschöpfung."""
import asyncio
import types

from services import deep_explore as de


def _fake_opt(trade_fix=True, n_cands=8):
    o = types.SimpleNamespace()
    cands = [{"ind": f"i{i}", "label": f"L{i}", "long": {"indicator": f"i{i}", "op": ">", "value": 1.0},
              "short": {"indicator": f"i{i}", "op": "<", "value": 1.0}} for i in range(n_cands)]
    cands.append({"ind": "i0", "label": "L0b", "long": {"indicator": "i0", "op": ">", "value": 2.0},
                  "short": {"indicator": "i0", "op": "<", "value": 2.0}})
    o.build_candidates = lambda allowed, tf: cands
    o._mk_strategy = lambda d: d
    o._labels = lambda d: [r["indicator"] for r in d["long_rules"]]
    o._score = lambda m, obj, mt, dd: (-1e9 + m["trades"]) if m["trades"] < mt else m["pnl"]

    async def ev_batch(job, pool, items, hists, fsm, stop):
        out = []
        for d, settings, cfg in items:
            n = len(d["long_rules"])
            pnl = 5.0 if cfg.get("tp") == "good" else -2.0      # Standard-TP/SL verliert knapp
            out.append({"trades": 40 if n <= 2 else 2, "pnl": pnl, "win_rate": 50,
                        "max_drawdown": 1.0})
        return out
    o._evaluate_batch = ev_batch

    async def refine(job, d, sc, m, *a, **k):
        return d, m, []
    o._refine = refine

    async def tune(job, d, sc, m, hists, settings, cfg, obj, mt, it, space, prog, *a, **k):
        if not trade_fix:
            return {}, m, sc
        return {"tp": "good"}, {**m, "pnl": 5.0}, 5.0
    o._optimize_trade_settings = tune
    return o


def _run(o, cfg=None, trade_space=None):
    job = {}
    return asyncio.run(de.run(o, job, {"A": []}, {"A": []}, {}, {}, "pnl", 10, 3, None,
                              lambda p, t: job.update(phase=t), 5, None, None, None, None, 1, None,
                              60.0, 20.0, cfg or {"target_champions": 2}, trade_space=trade_space)), job


def test_rescue_and_trade_tuning_find_champions():
    (top, rep), job = _run(_fake_opt(), trade_space={"tp": ["bad", "good"]})
    assert rep["champions_found"] >= 2 and rep["stop_reason"] == "target_reached"
    assert rep["rescue_refined"] > 0 and rep["trade_tuned"] > 0
    assert top[0]["trade_params"] == {"tp": "good"}
    assert "mit genug Trades" in job["phase"] or "Trades" in job["phase"]


def test_without_trade_tuning_no_champion_but_terminates():
    (top, rep), _ = _run(_fake_opt(trade_fix=False), trade_space=None)
    assert rep["champions_found"] == 0 and rep["stop_reason"] == "space_exhausted"
    assert rep["revisits"] > 0          # Wiederholung vor dem Abbruch


def test_size_weights_prefer_sizes_with_trades():
    w = de.size_weights([2, 4], {2: [100, 60], 4: [100, 1]})
    assert w[0] > w[1] * 3
    assert de.size_weights([2, 3], {}) == [0.65, 0.65]


def test_one_per_indicator():
    ind = {"a": "rsi", "b": "rsi", "c": "ema"}
    assert de.one_per_indicator(["a", "c"], ind) and not de.one_per_indicator(["a", "b"], ind)


def test_consistency_does_not_punish_better_test():
    assert de.consistent_wf({"train_quality": 0.06, "test_quality": 0.19, "consistency_pct": 33}, 40)
    assert not de.consistent_wf({"train_quality": 0.19, "test_quality": 0.06, "consistency_pct": 33}, 40)
    assert de.consistent_wf({"train_quality": 0.1, "test_quality": 0.08, "consistency_pct": 80}, 40)
