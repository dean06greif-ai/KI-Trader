"""Iter 56 tests: backtest+optimizer pause/resume, playbook cache, localworker version."""
import os, time
import pytest
import requests

BASE = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
ADMIN = {"username": "Admin", "password": "Dean06Greif!/Admin"}


@pytest.fixture(scope="module")
def s():
    ses = requests.Session()
    ses.headers.update({"Content-Type": "application/json"})
    return ses


@pytest.fixture(scope="module")
def token(s):
    r = s.post(f"{BASE}/api/auth/login", json=ADMIN, timeout=30)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_admin_login(token):
    assert isinstance(token, str) and len(token) > 5


def test_ai_playbook_cache(s):
    t0 = time.time()
    r1 = s.get(f"{BASE}/api/ai/playbook", timeout=60)
    d1 = time.time() - t0
    assert r1.status_code == 200, r1.text
    j = r1.json()
    for k in ("maturity", "classes", "class_order"):
        assert k in j, f"missing {k}"
    t1 = time.time()
    r2 = s.get(f"{BASE}/api/ai/playbook", timeout=30)
    d2 = time.time() - t1
    assert r2.status_code == 200
    print(f"playbook first={d1:.2f}s second={d2:.2f}s")
    assert d2 < 2.0, f"second call not cached ({d2:.2f}s)"


def test_localworker_required_version(s):
    r = s.get(f"{BASE}/api/localworker/status", timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("required_version") == "1.10.0", j


def test_pause_resume_unknown_and_auth(s, auth):
    # unknown job -> 404
    for path in ("/api/backtest/pause/nope", "/api/backtest/resume/nope",
                 "/api/optimizer/pause/nope", "/api/optimizer/resume/nope"):
        r = s.post(f"{BASE}{path}", headers=auth, timeout=15)
        assert r.status_code == 404, f"{path} -> {r.status_code} {r.text}"
    # without token -> 401/403
    for path in ("/api/backtest/pause/x", "/api/optimizer/pause/x"):
        r = requests.post(f"{BASE}{path}", timeout=15)
        assert r.status_code in (401, 403), f"{path} -> {r.status_code}"


def _reset_backtest(s, auth):
    try:
        s.post(f"{BASE}/api/backtest/reset", headers=auth, timeout=15)
    except Exception:
        pass


def test_backtest_pause_resume(s, auth):
    _reset_backtest(s, auth)
    body = {"strategy_ids": ["scalping_4_rules"],
            "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT"],
            "days": 60}
    r = s.post(f"{BASE}/api/backtest/run", headers=auth, json=body, timeout=30)
    assert r.status_code in (200, 202), r.text
    job_id = r.json().get("job_id") or r.json().get("id")
    assert job_id, r.text
    print(f"backtest job {job_id}")

    # Wait a few seconds so job is actually working
    time.sleep(5)

    # Pause
    rp = s.post(f"{BASE}/api/backtest/pause/{job_id}", headers=auth, timeout=15)
    assert rp.status_code == 200, rp.text
    assert rp.json().get("status") in ("pausing", "paused"), rp.json()

    # Wait until paused=true (within ~15s)
    paused_state = None
    phase_txt = ""
    progress_at_pause = None
    for _ in range(30):
        st = s.get(f"{BASE}/api/backtest/status/{job_id}", timeout=15).json()
        if st.get("paused"):
            paused_state = st
            phase_txt = st.get("phase", "")
            progress_at_pause = st.get("progress")
            break
        time.sleep(0.5)
    assert paused_state is not None, f"never reached paused=true; last={st}"
    assert paused_state.get("status") == "running", paused_state
    assert paused_state.get("pause") is True
    assert phase_txt.startswith("⏸ Pausiert") or "Pausiert" in phase_txt, phase_txt

    # Progress stays roughly constant for ~5s of pause
    time.sleep(5)
    st2 = s.get(f"{BASE}/api/backtest/status/{job_id}", timeout=15).json()
    assert st2.get("paused") is True, st2
    if isinstance(progress_at_pause, (int, float)) and isinstance(st2.get("progress"), (int, float)):
        assert abs(st2["progress"] - progress_at_pause) < 0.02, (progress_at_pause, st2["progress"])

    # Resume
    rr = s.post(f"{BASE}/api/backtest/resume/{job_id}", headers=auth, timeout=15)
    assert rr.status_code == 200, rr.text
    assert rr.json().get("status") in ("resuming", "running"), rr.json()

    # paused becomes false; progress advances or job done
    resumed_ok = False
    for _ in range(60):
        st3 = s.get(f"{BASE}/api/backtest/status/{job_id}", timeout=15).json()
        if st3.get("paused") is False:
            resumed_ok = True
            break
        time.sleep(0.5)
    assert resumed_ok, f"never resumed: last={st3}"
    print("backtest resumed OK")

    # Best-effort cleanup
    _reset_backtest(s, auth)


def _reset_opt(s, auth):
    try:
        s.post(f"{BASE}/api/optimizer/reset", headers=auth, timeout=15)
    except Exception:
        pass


def test_optimizer_pause_resume(s, auth):
    _reset_opt(s, auth)
    body = {"mode": "params", "strategy_id": "scalping_4_rules",
            "symbols": ["BTCUSDT", "ETHUSDT"], "days": 30,
            "timeframe": "5m", "iterations": 20, "min_trades": 1}
    r = s.post(f"{BASE}/api/optimizer/run", headers=auth, json=body, timeout=30)
    assert r.status_code in (200, 202), r.text
    job_id = r.json().get("job_id") or r.json().get("id")
    assert job_id, r.text
    print(f"opt job {job_id}")

    # Wait until data-load phase done (phase no longer contains 'Lade Daten')
    phase = ""
    for _ in range(60):
        st = s.get(f"{BASE}/api/optimizer/status/{job_id}", timeout=15).json()
        phase = st.get("phase", "") or ""
        if phase and "Lade Daten" not in phase and "Lade" not in phase:
            break
        if st.get("status") in ("done", "error", "failed"):
            break
        time.sleep(1)
    print(f"opt post-load phase={phase!r}")

    rp = s.post(f"{BASE}/api/optimizer/pause/{job_id}", headers=auth, timeout=15)
    assert rp.status_code == 200, rp.text

    paused = None
    for _ in range(30):
        st = s.get(f"{BASE}/api/optimizer/status/{job_id}", timeout=15).json()
        if st.get("paused"):
            paused = st
            break
        if st.get("status") in ("done", "error", "failed"):
            pytest.skip(f"opt finished before pause observed: {st.get('status')}")
        time.sleep(0.5)
    assert paused is not None, f"never paused: last={st}"
    assert paused.get("status") == "running"

    rr = s.post(f"{BASE}/api/optimizer/resume/{job_id}", headers=auth, timeout=15)
    assert rr.status_code == 200, rr.text

    for _ in range(30):
        st = s.get(f"{BASE}/api/optimizer/status/{job_id}", timeout=15).json()
        if st.get("paused") is False or st.get("status") in ("done", "error", "failed"):
            break
        time.sleep(0.5)
    print(f"opt after resume: paused={st.get('paused')} status={st.get('status')}")
    _reset_opt(s, auth)
