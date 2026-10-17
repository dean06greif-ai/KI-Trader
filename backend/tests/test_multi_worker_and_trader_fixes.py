"""Regression 10/2026: Multi-Worker (max. 2, Job-Zuweisung), KI-Trader-Blocker
(Funding-Einheit, Mindest-SL), Setup-Rücksprung per Chat, Asset-Vorschlag."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from services import local_exec as lx  # noqa: E402
from services import optimizer as opt  # noqa: E402
from services import funding_fees, min_sl_rule, ai_playbook, asset_suggest  # noqa: E402
from services import ai_chat_commands as cc  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_state():
    for d in (lx.WORKERS, lx.WAITING_WORKERS, lx.LOCAL_JOBS):
        d.clear()
    lx.COMPUTE_QUEUE.clear()
    yield
    for d in (lx.WORKERS, lx.WAITING_WORKERS, lx.LOCAL_JOBS):
        d.clear()
    lx.COMPUTE_QUEUE.clear()


def _online(wid, name=None, version="1.18.0"):
    lx.heartbeat(wid, {"name": name or wid, "version": version, "running_jobs": []})


def _opt_job(body=None):
    jid = opt.create_job({"mode": "explore"})
    lx.enqueue_compute("optimizer", jid, {"kind": "optimizer", "args": {"body": body or {}}})
    return jid


# ---------------- Multi-Worker ----------------
def test_max_two_workers_admitted():
    _online("pc_a")
    _online("pc_b")
    assert lx.worker_admitted("pc_a") and lx.worker_admitted("pc_b")
    assert not lx.worker_admitted("pc_c")
    lx.note_waiting("pc_c", {"name": "Dritter"})
    assert [w["worker_id"] for w in lx.waiting_public()] == ["pc_c"]


def test_untargeted_job_any_worker_backward_compatible():
    _online("pc_a")
    jid = _opt_job()
    item = lx.claim("pc_a")
    assert item and item["job_id"] == jid


def test_targeted_job_only_claimed_by_target():
    _online("pc_a")
    _online("pc_b")
    jid = _opt_job({"worker_id": "pc_b", "execution": "local"})
    assert opt.JOBS[jid]["worker_target"] == "pc_b"
    assert lx.claim("pc_a") is None
    assert len(lx.COMPUTE_QUEUE) == 1
    item = lx.claim("pc_b")
    assert item and item["job_id"] == jid


def test_check_target_offline():
    _online("pc_a")
    assert lx.check_target({"worker_id": "pc_a"}) is None
    assert "nicht verbunden" in lx.check_target({"worker_id": "ghost"})
    assert lx.check_target({}) is None


def test_parallel_allowed_only_on_other_local_worker():
    _online("pc_a")
    _online("pc_b")
    jid = _opt_job({"worker_id": "pc_a", "execution": "local"})
    running = [opt.JOBS[jid]]
    assert lx.parallel_allowed(running, {"execution": "local", "worker_id": "pc_b"})
    assert not lx.parallel_allowed(running, {"execution": "local", "worker_id": "pc_a"})
    assert not lx.parallel_allowed(running, {"execution": "cloud"})
    assert not lx.parallel_allowed(running, {"execution": "local"})
    assert not lx.parallel_allowed([{"id": "cloudjob", "params": {}}],
                                   {"execution": "local", "worker_id": "pc_b"})


def test_queued_target_offline_times_out():
    _online("pc_a")
    jid = _opt_job({"worker_id": "pc_b", "execution": "local"})
    lx.LOCAL_JOBS[jid]["enqueued_at"] = time.time() - lx.QUEUED_TIMEOUT - 5
    lx.check_stale()
    assert opt.JOBS[jid]["status"] == "error"
    assert "gewählte" in opt.JOBS[jid]["error"]


# ---------------- KI-Trader-Blocker ----------------
def test_bitunix_funding_rate_is_percent():
    info = funding_fees.parse_funding_payload(
        {"code": 0, "data": {"fundingRate": "0.015", "fundingInterval": 8, "maxFundingRate": "0.375"}})
    assert info["rate"] == pytest.approx(0.00015)
    # Scalp (2 h) auf ADA: 0,00375 % statt fälschlich 0,375 %
    assert funding_fees.adverse_funding_pct(info["rate"], "LONG", 2, 8) == pytest.approx(0.00375)
    # Payload ohne Kappe bleibt unverändert (Alt-Verhalten)
    assert funding_fees.parse_funding_payload(
        {"code": 0, "data": {"fundingRate": "0.0001"}})["rate"] == 0.0001


def test_min_sl_widen_instead_of_block():
    sl, tp1, tpf, note = min_sl_rule.widen({}, "EURUSD", 0.0015, 0.003, 0.006)
    assert sl == pytest.approx(0.25 * min_sl_rule.WIDEN_BUFFER / 100)
    assert tp1 / sl == pytest.approx(2.0) and tpf / sl == pytest.approx(4.0)
    assert note and "Mindest-SL" in note
    entry = 1.1
    assert min_sl_rule.check({}, "EURUSD", entry, entry * (1 - sl))[0]
    # bereits weit genug -> unverändert
    assert min_sl_rule.widen({}, "BTCUSDT", 0.006, 0.01, 0.02)[3] is None
    # abschaltbar -> altes Verhalten (Block)
    assert min_sl_rule.widen({"min_sl_auto_widen": False}, "EURUSD", 0.0015, 0.003, 0.006)[3] is None


def test_min_sl_execution_drift_tolerance():
    entry = 150.0
    sl = entry * (1 - 0.002497)  # „0.25% < 0.25%“ durch Tick-Rundung
    assert not min_sl_rule.check({}, "USDJPY", entry, sl)[0]
    assert min_sl_rule.check({}, "USDJPY", entry, sl, tolerance=min_sl_rule.EXEC_DRIFT_TOLERANCE)[0]
    assert not min_sl_rule.check({}, "USDJPY", entry, entry * 0.9985,
                                 tolerance=min_sl_rule.EXEC_DRIFT_TOLERANCE)[0]


# ---------------- Setup-Rücksprung ----------------
def _scope():
    return {"revisions": {"order_block": {"desc": "neue strengere Regeln mit CVD", "version": 1}},
            "live_blocked": {"order_block": {"kind": "revision", "reason": "Revision v1"}},
            "lifecycle": {"order_block": {"versions": [
                {"v": 1, "params": {"sl_pct": 0.56}, "stats": {"trades": 0, "pnl_per_trade": 0}},
                {"v": 2, "params": {"sl_pct": 0.62}, "stats": {"trades": 10, "pnl": 110.7, "pnl_per_trade": 11.07}},
                {"v": 3, "params": {"sl_pct": 0.61, "max_leverage": 61.7}, "stats": {"trades": 0}}]}}}


def test_revert_plan_original_picks_best_profile_and_unblocks():
    plan = ai_playbook.revert_plan(_scope(), "order_block", "original")
    assert plan["had_revision"] and plan["unblock"] and not plan["noop"]
    assert plan["profile"]["v"] == 2


def test_revert_plan_noop_when_untouched():
    plan = ai_playbook.revert_plan({}, "order_block", "original")
    assert plan["noop"]


def test_chat_detects_setup_revert_and_classes():
    assert cc.looks_like_command("setzte das orderblock setup auf ihren anfangs variante zurück")
    assert ai_playbook.normalize_setup("orderblock") == "order_block"
    assert cc.setup_classes("ALL", ["crypto", "forex"]) == ["crypto", "forex"]
    assert cc.setup_classes("Krypto", ["crypto", "forex"]) == ["crypto"]


# ---------------- Asset-Vorschlag ----------------
def test_asset_suggest_prefers_present_and_profitable_assets():
    per_symbol = {
        "BTCUSDT": {"segments": [{"regime": 0, "bars": 100}, {"regime": 1, "bars": 50},
                                 {"regime": 0, "bars": 80}]},
        "ETHUSDT": {"segments": [{"regime": 0, "bars": 90}, {"regime": 0, "bars": 60},
                                 {"regime": 1, "bars": 70}]},
        "DOGEUSDT": {"segments": [{"regime": 1, "bars": 300}]},
    }
    syms = list(per_symbol)
    sim = [{"a": "BTCUSDT", "b": "ETHUSDT", "agreement_pct": 80},
           {"a": "BTCUSDT", "b": "DOGEUSDT", "agreement_pct": 30}]
    perf = asset_suggest.perf_from_trades(
        [{"symbol": "ETHUSDT", "dynamic": {"regime": 0}, "realized_pnl": 12}] * 6)
    res = asset_suggest.score_assets(syms, [{"id": 0, "label": "Trend"}],
                                     asset_suggest.regime_presence(per_symbol),
                                     asset_suggest.consistency(sim, syms), perf)
    r0 = res["per_regime"][0]
    assert r0["assets"][0]["symbol"] == "ETHUSDT"
    assert "DOGEUSDT" not in r0["picks"]
    assert set(res["recommended"]) == {"BTCUSDT", "ETHUSDT"}


def test_auto_revert_due_after_dead_revision():
    from datetime import datetime, timezone, timedelta
    sc = _scope()
    sc["revisions"]["order_block"]["source"] = "ki"
    sc["live_blocked"]["order_block"].update(
        {"retest_at": (datetime.now(timezone.utc) - timedelta(days=1)).isoformat(),
         "paper_since": {"trades": 0}})
    assert "Auto-Rücksprung" in ai_playbook.auto_revert_due(sc, "order_block")
    # Re-Test noch nicht erreicht -> warten
    sc["live_blocked"]["order_block"]["retest_at"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    assert ai_playbook.auto_revert_due(sc, "order_block") is None
    # Trader-Revision wird nie automatisch zurückgedreht
    sc["live_blocked"]["order_block"]["retest_at"] = "2026-01-01T00:00:00+00:00"
    sc["revisions"]["order_block"]["source"] = "user"
    assert ai_playbook.auto_revert_due(sc, "order_block") is None
