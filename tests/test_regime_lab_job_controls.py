"""Regime-Lab Job-Steuerung (Pausieren / Fortsetzen / Suche beenden / Reset)
– gleiche Semantik wie im Strategie-Optimizer.

Unit-Teil: services.job_control + sanfter Stop der Regime-Discovery.
Live-Teil (REACT_APP_BACKEND_URL gesetzt): neue Endpoints antworten korrekt.
"""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))

from services import job_control  # noqa: E402


def _job(status="running"):
    return {"id": "j1", "status": status, "phase": "Parameter 3/40", "cancel": False}


# ---------------------------------------------------------------- unit
def test_request_stop_only_for_running_jobs():
    j = _job()
    j["pause"] = True
    assert job_control.request_stop(j) is True
    assert j["stop_explore"] is True
    assert j["pause"] is False  # Pause aufgehoben, sonst greift der Stop nie
    assert job_control.stop_requested(j)
    assert job_control.request_stop(_job("done")) is False


def test_reset_running_clears_only_running_jobs():
    store = {"a": _job(), "b": _job("done"), "c": _job()}
    store["c"]["pause"] = True
    assert job_control.reset_running(store) == 2
    assert store["a"]["status"] == "cancelled" and store["a"]["cancel"] is True
    assert store["c"]["status"] == "cancelled" and store["c"]["pause"] is False
    assert store["b"]["status"] == "done"
    assert "Notfall-Reset" in store["a"]["phase"]


def test_pause_resume_flags_and_public_state():
    j = _job()
    assert job_control.request_pause(j)
    assert job_control.pause_requested(j)
    st = job_control.public_state(j)
    assert st["pause"] is True and st["paused"] is False
    assert job_control.request_resume(j)
    assert not job_control.pause_requested(j)
    assert job_control.request_pause(_job("cancelled")) is False


def test_wait_if_paused_blocks_until_resume():
    j = _job()
    job_control.request_pause(j)

    async def run():
        async def release():
            await asyncio.sleep(0.6)
            job_control.request_resume(j)
        t = asyncio.ensure_future(release())
        await job_control.wait_if_paused(j)
        await t
    asyncio.run(run())
    assert j["paused"] is False
    assert j["phase"] == "Parameter 3/40"  # Phase nach der Pause wiederhergestellt
    assert j["paused_total_s"] >= 0.5


def test_discovery_soft_stop_keeps_best_so_far(monkeypatch):
    """Sanfter Stop mitten in der Greedy-Suche: kein JobCancelled, sondern die
    bis dahin beste Regel-Kombination als reguläres Ergebnis."""
    from services import dynamic_strategy as dyn

    calls = {"n": 0}

    async def fake_eval(strategy, segments, rid, settings, cfg, should_stop=None):
        calls["n"] += 1
        n_rules = len(strategy.definition.get("long_rules") or [])
        return {"pnl": 10.0 * n_rules + calls["n"] * 0.01, "win_rate": 60.0,
                "trades": 30, "max_drawdown": 1.0}

    monkeypatch.setattr(dyn, "eval_regime_config", fake_eval)
    stop_after = 3

    def soft_stop():
        return calls["n"] >= stop_after

    res = asyncio.run(dyn.discover_regime_strategy(
        {"BTCUSDT": []}, 0, {}, {}, ["rsi", "ema", "macd"], None,
        "pnl", 5, max_rules=3, soft_stop=soft_stop))
    assert res["definition"] is not None
    assert len(res["rules"]) >= 1
    assert calls["n"] <= stop_after + 1
    assert any("Suche vom Nutzer beendet" in (s.get("info") or "") for s in res["steps"])


# ---------------------------------------------------------------- live
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or "").rstrip("/")
live = pytest.mark.skipif(not BASE_URL, reason="REACT_APP_BACKEND_URL nicht gesetzt")


@pytest.fixture(scope="module")
def auth_headers():
    import requests
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": os.environ.get("ADMIN_USER", "Admin"),
                            "password": os.environ.get("ADMIN_PASSWORD", "Dean06Greif!/Admin")},
                      timeout=20)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return {"Authorization": f"Bearer {r.json()['token']}"}


@live
@pytest.mark.parametrize("action", ["pause", "resume", "stop", "cancel"])
def test_live_controls_unknown_job_404(auth_headers, action):
    import requests
    r = requests.post(f"{BASE_URL}/api/regime-lab/{action}/does-not-exist",
                      headers=auth_headers, timeout=20)
    assert r.status_code == 404


@live
@pytest.mark.parametrize("action", ["pause", "resume", "stop", "reset"])
def test_live_controls_require_admin(action):
    import requests
    path = "/api/regime-lab/reset" if action == "reset" else f"/api/regime-lab/{action}/x"
    r = requests.post(f"{BASE_URL}{path}", timeout=20)
    assert r.status_code in (401, 403)


@live
def test_live_reset_is_idempotent(auth_headers):
    import requests
    r = requests.post(f"{BASE_URL}/api/regime-lab/reset", headers=auth_headers, timeout=20)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "reset" and isinstance(body["cleared"], int)
    assert requests.get(f"{BASE_URL}/api/regime-lab/active", timeout=20).json()["active"] is None
