"""Regressionstests 08.10: Trail-Start unabhängig von TP1 (Backtester + Live-Regeln),
BE-Ratsche, Korrelations-Verlauf, pausierte Jobs (Worker-Slot, Pool-Pause, Übersicht)."""
import asyncio
import importlib.util
import os
import sys

from services import asset_correlation as ac
from services import job_control, job_overview, parallel_sim, trail_policy
from services.backtester import TRADE_CFG_KEYS
from services.optimizer import TRADE_SPACES
from core.defaults import OPT_TRADE_KEYS

NEW_KEYS = ("trail_mode", "trail_trigger_pct", "trail_trigger_crv", "trail_start_be")


# ---------------- trail_policy (rein) ----------------
class TestTrailPolicy:
    def test_defaults_are_legacy_tp1(self):
        p = trail_policy.params({})
        assert p["mode"] == "tp1" and p["enabled"] and p["mult"] == 1.5
        assert trail_policy.params({"trail_mode": "quatsch"})["mode"] == "tp1"

    def test_tp1_mode_only_after_tp1(self):
        p = trail_policy.params({})
        assert not trail_policy.should_activate(p, "LONG", 100, 1, 105, False, 50, 100)
        assert trail_policy.should_activate(p, "LONG", 100, 1, 101, True, 1, 100)

    def test_profit_pct_mode_before_tp1(self):
        p = trail_policy.params({"trail_mode": "profit_pct", "trail_trigger_pct": 10})
        assert not trail_policy.should_activate(p, "LONG", 100, 1, 100.5, False, 9.9, 100)
        assert trail_policy.should_activate(p, "LONG", 100, 1, 101, False, 10, 100)

    def test_crv_mode_short(self):
        p = trail_policy.params({"trail_mode": "crv", "trail_trigger_crv": 1.5})
        assert not trail_policy.should_activate(p, "SHORT", 100, 2, 98, False, 0, 100)
        assert trail_policy.should_activate(p, "SHORT", 100, 2, 97, False, 0, 100)

    def test_disabled(self):
        p = trail_policy.params({"trail_after_tp1": False, "trail_mode": "profit_pct"})
        assert not trail_policy.should_activate(p, "LONG", 100, 1, 200, True, 999, 100)

    def test_ratchet_never_worse(self):
        assert trail_policy.improves("LONG", 100.2, 100.5)
        assert not trail_policy.improves("LONG", 100.2, 99.9)
        assert trail_policy.improves("SHORT", 99.8, 99.5)

    def test_start_be_only_on_protective_side_and_not_tp1_mode(self):
        p = trail_policy.params({"trail_mode": "profit_pct"})
        assert trail_policy.start_be_level(p, "LONG", 101, 100.12, False) == 100.12
        assert trail_policy.start_be_level(p, "LONG", 100.05, 100.12, False) is None
        assert trail_policy.start_be_level(trail_policy.params({}), "LONG", 101, 100.12, False) is None
        assert trail_policy.start_be_level(p, "LONG", 101, 100.12, True) is None


# ---------------- Konfig-/Optimizer-Durchreichung ----------------
def test_new_trail_keys_flow_through_pipeline():
    for k in NEW_KEYS:
        assert k in TRADE_CFG_KEYS and k in OPT_TRADE_KEYS and k in TRADE_SPACES["trail"]
    assert set(TRADE_SPACES["trail"]["trail_mode"]) == {"tp1", "profit_pct", "crv"}


def test_live_trail_reads_trade_snapshot_and_be_ratchet():
    import inspect
    from services import bitunix_trade as bt
    for k in NEW_KEYS:
        assert k in bt.DEFAULT_COIN_CFG and k in bt.TRAIL_KEYS
    src = inspect.getsource(bt.AutoTradeManager._manage_trade) if hasattr(bt, "AutoTradeManager") else \
        inspect.getsource(bt)
    assert "trail_policy.should_activate" in src and "trail_policy.improves" in src


# ---------------- Backtester: Trail vor TP1 ----------------
def _sim(cfg_extra):
    from services.backtester import simulate_pair

    class _S:
        STRATEGY_ID = "t"
        IS_CUSTOM = False
        n = 0

        def check_signal(self, window, symbol, settings):
            self.n += 1
            if self.n == 5:
                return {"type": "LONG", "entry_price": float(window[-1]["close"])}
            return None

    candles, t0, px = [], 1_700_000_000_000, 100.0
    for i in range(400):
        if 60 <= i < 160:
            px += 0.05            # Anstieg (unter TP1 bei hohem CRV)
        elif i >= 160:
            px -= 0.08            # Rückfall
        candles.append({"timestamp": t0 + i * 60000, "open": px, "high": px + 0.02,
                        "low": px - 0.02, "close": px, "volume": 1000})
    cfg = {"max_capital": 100, "leverage": 10, "fee_percent": 0.0, "tp1_crv": 50, "tp_full_crv": 80,
           "sl_mode": "fixed", "sl_fixed_percent": 2.0, "be_mode": "off", "trail_atr_mult": 1.0,
           "min_risk_percent": 0.0, **cfg_extra}
    return simulate_pair(_S(), candles, "TESTUSDT", {}, cfg, collect_trades=True)


def test_backtester_trail_profit_pct_locks_gain_before_tp1():
    legacy = _sim({})
    early = _sim({"trail_mode": "profit_pct", "trail_trigger_pct": 10})
    tl, te = legacy.get("all_trades") or [], early.get("all_trades") or []
    assert tl and te
    # ohne TP1 trailt der Legacy-Modus nicht -> Verlust am fixen SL; früher Start sichert Gewinn
    assert te[0]["pnl"] > tl[0]["pnl"]
    assert te[0]["pnl"] > 0


# ---------------- Korrelation ----------------
def test_perfect_pairs_plus_and_minus_one():
    pairs = [{"a": "A", "b": "B", "ret_corr": 0.97, "score": 0.9},
             {"a": "A", "b": "C", "ret_corr": -0.99, "score": 0.2},
             {"a": "B", "b": "C", "ret_corr": 0.5, "score": 0.5}]
    pp = ac.perfect_pairs(pairs)
    assert [(p["a"], p["b"], p["sign"]) for p in pp] == [("A", "B", 1), ("A", "C", -1)]


def test_fetch_one_stall_timeout(monkeypatch):
    from services import regime_lab as lab
    monkeypatch.setattr(ac, "STALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ac, "STALL_POLL_S", 0.05)

    async def hang(*a, **kw):
        await asyncio.sleep(10)

    monkeypatch.setattr(lab, "fetch_histories", hang)
    job = {"status": "running", "phase": "x", "progress": 1}
    try:
        asyncio.run(ac._fetch_one(lab, "GOLD", 540, "30m", job, {}))
        assert False, "TimeoutError erwartet"
    except asyncio.TimeoutError:
        pass


def test_fetch_one_long_download_with_progress_survives(monkeypatch):
    from services import regime_lab as lab
    monkeypatch.setattr(ac, "STALL_TIMEOUT_S", 0.2)
    monkeypatch.setattr(ac, "STALL_POLL_S", 0.05)

    async def slow(symbols, days, tf, job=None, **kw):
        for i in range(12):          # 0,6 s > Stall-Limit, aber mit Fortschritt
            job["phase"] = f"Lade Daten: {symbols[0]} ({i}%)"
            await asyncio.sleep(0.05)
        return {symbols[0]: [1]}

    monkeypatch.setattr(lab, "fetch_histories", slow)
    job = {"status": "running", "phase": "x", "progress": 1}
    assert asyncio.run(ac._fetch_one(lab, "GOLD", 540, "30m", job, {})) == {"GOLD": [1]}


# ---------------- Pause: Pool-Event je Job ----------------
class _Evt:
    def __init__(self):
        self.flag = False

    def set(self):
        self.flag = True

    def clear(self):
        self.flag = False


def test_resume_releases_own_pool_even_after_other_pool(monkeypatch):
    ea, eb = _Evt(), _Evt()
    monkeypatch.setattr(parallel_sim, "_PAUSE_EVT", ea)
    a = {"status": "running"}
    job_control._enter(a)                       # A pausiert -> Pool A angehalten
    assert ea.flag
    monkeypatch.setattr(parallel_sim, "_PAUSE_EVT", eb)   # B startet eigenen Pool
    job_control._leave(a)                       # A fortsetzen -> NUR Pool A frei
    assert not ea.flag and not eb.flag


def test_parked_locally():
    assert job_control.parked_locally({"status": "running", "paused": True, "execution": "local"})
    assert job_control.parked_locally({"status": "running", "pause": True, "params": {"execution": "local"}})
    assert not job_control.parked_locally({"status": "running", "paused": True})          # Cloud
    assert not job_control.parked_locally({"status": "running", "execution": "local"})    # rechnet


def test_job_overview_lists_paused_across_areas():
    from services import optimizer, regime_lab
    optimizer.JOBS["ovw1"] = {"status": "running", "paused": True, "phase": "⏸", "progress": 40.0,
                              "execution": "local"}
    regime_lab.JOBS["ovw2"] = {"status": "running", "pause": True, "params": {"workbench_job": True}}
    try:
        rows = {r["id"]: r for r in job_overview.paused_jobs()}
        assert rows["ovw1"]["resume"] == "/api/optimizer/resume/ovw1" and rows["ovw1"]["local"]
        assert "ovw2" not in rows
    finally:
        optimizer.JOBS.pop("ovw1", None)
        regime_lab.JOBS.pop("ovw2", None)


def test_worker_paused_job_frees_slot():
    path = os.path.join(os.path.dirname(__file__), "..", "..", "local_worker", "worker.py")
    src = open(path, encoding="utf-8").read()
    assert "def active_compute_jobs" in src and "compute_running = active_compute_jobs()" in src
    assert 'jd.get("paused")' in src
