"""Regression 10/2026: Werkbank-Zeitraum über die Analyse hinaus (nur Live-Sicht)
und Ergebnis-Backtest auf dem lokalen Worker."""
import asyncio

from services import dynamic_workbench as wb
from services import regime_opt as ro


def test_extra_days_for():
    assert ro.extra_days_for({"days": 500}, {"days": 360}) == 140
    assert ro.extra_days_for({"days": 90}, {"days": 360}) == 0
    assert ro.extra_days_for({}, {"days": 360}) == 0


def test_extended_live_segments_before_analysis(monkeypatch):
    day = 86_400_000
    start = 1000 * day
    candles = [{"timestamp": start - (200 - i) * day // 4} for i in range(200)]
    calls = {}

    async def fake_fetch(syms, days, tf, job, end_ts=None, **k):
        calls["end_ts"], calls["days"] = end_ts, days
        return {"BTCUSDT": candles}

    monkeypatch.setattr(ro.lab, "fetch_histories", fake_fetch)
    monkeypatch.setattr(ro.lab, "model_for", lambda d, s, y: {"regimes": [{"id": 1}]})
    from services import dynamic_backtest as dbt
    monkeypatch.setattr(dbt, "_regime_warmup_days", lambda m: 5)
    monkeypatch.setattr(dbt, "phase_labels", lambda *a, **k: [1] * 100 + [0] * 100)
    doc = {"id": "a", "days": 360, "bounds": {"BTCUSDT": {"start_ts": start}}, "settings": {}}
    out = asyncio.run(ro._extended_live_segments(doc, "combined", None, 1, "6h", {}, ["BTCUSDT"], 40))
    assert calls["end_ts"] == {"BTCUSDT": start - 1} and calls["days"] == 45
    segs = out["BTCUSDT"]
    assert len(segs) == 1 and segs[0]["regime"] == 1 and segs[0]["extended"] is True
    assert segs[0]["start_ts"] >= start - 40 * day     # nur der gewünschte Zusatz-Zeitraum
    assert all(c["timestamp"] < start for c in segs[0]["candles"])   # Holdout unberührt


def test_retro_beyond_analysis_is_rejected():
    import inspect
    src = inspect.getsource(ro.run_regime_optimizer)
    assert "extra_days_for(body, doc)" in src and "Rückblick (ideale Phasen) gibt es nur im Zeitraum" in src


def test_local_result_backtest_skipped_for_cloud():
    res = asyncio.run(wb._local_result_backtest({"id": "j"}, {"id": "d"}, 30, {}, {"execution": "cloud"}, {}))
    assert res is None


def test_local_result_backtest_uses_worker(monkeypatch):
    from services import backtester as bt
    from services import local_exec
    sent = {}
    monkeypatch.setattr(local_exec, "worker_online", lambda: True)
    monkeypatch.setattr(local_exec, "worker_supports", lambda v, w=None: True)

    def fake_enqueue(kind, jid, payload, worker_id=None):
        sent.update(kind=kind, payload=payload)
        bt.JOBS[jid].update(status="done", result={"dynamic_breakdown": {"d": {"dynamic_id": "d", "total": {}}}})

    monkeypatch.setattr(local_exec, "enqueue_compute", fake_enqueue)

    class _Reg:
        def list_custom_definitions(self):
            return []

    deps = {"settings": {}, "registry": _Reg()}
    doc = {"id": "d", "symbols": ["BTCUSDT"], "last_state": {"x": 1}}
    res = asyncio.run(wb._local_result_backtest({"id": "j", "params": {}, "log": []}, doc, 30, {}, {"execution": "local"}, deps))
    assert res["dynamic_id"] == "d" and sent["kind"] == "backtest"
    assert "last_state" not in sent["payload"]["args"]["dynamic_docs"]["d"]
