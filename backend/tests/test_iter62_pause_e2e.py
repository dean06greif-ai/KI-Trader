"""
E2E: pause an optimizer run on local worker, verify /api/jobs/paused lists it,
run a local backtest concurrently (must succeed while optimizer paused),
then resume optimizer and finally cancel.
"""
import os, time, pytest, requests

BASE = os.environ.get("REACT_APP_BACKEND_URL", "https://daytrade-lab.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def H():
    t = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"},
                      timeout=20).json()["token"]
    return {"Authorization": f"Bearer {t}", "Content-Type": "application/json"}


def _get(url, H, **kw):
    return requests.get(url, headers=H, timeout=kw.get("timeout", 20))


def test_pause_resume_with_concurrent_backtest(H):
    # 1) start optimizer on local worker
    payload = {"mode": "params", "strategy_id": "rsi_only",
               "symbols": ["BTCUSDT"], "days": 90, "timeframe": "5m",
               "iterations": 5000, "execution": "local"}
    r = requests.post(f"{BASE}/api/optimizer/run", headers=H, json=payload, timeout=30)
    assert r.status_code in (200, 202), r.text
    oid = r.json().get("id") or r.json().get("job_id") or r.json().get("optimizer_id")
    assert oid, f"no id in response: {r.json()}"

    # 2) wait ~20s then pause
    time.sleep(20)
    rp = requests.post(f"{BASE}/api/optimizer/pause/{oid}", headers=H, timeout=20)
    assert rp.status_code in (200, 202), rp.text

    # 3) wait until status.paused == True
    paused = False
    for _ in range(40):
        s = _get(f"{BASE}/api/optimizer/status/{oid}", H).json()
        if s.get("paused") or s.get("status") == "paused":
            paused = True
            break
        time.sleep(1)
    assert paused, f"optimizer never reached paused: last={s}"
    pre_progress = s.get("progress") or s.get("iteration") or 0

    # 4) /api/jobs/paused shows it with resume/cancel paths
    pj = _get(f"{BASE}/api/jobs/paused", H).json()
    assert pj["count"] >= 1
    match = [j for j in pj["jobs"] if (str(j.get("id")) == str(oid)) or (str(oid) in str(j))]
    assert match, f"optimizer {oid} missing from paused list: {pj}"
    j = match[0]
    has_resume = any("resume" in str(v).lower() for v in j.values())
    has_cancel = any("cancel" in str(v).lower() for v in j.values())
    assert has_resume and has_cancel, f"missing resume/cancel paths: {j}"

    # 5) start concurrent local backtest, verify completes while optimizer paused
    bt = requests.post(f"{BASE}/api/backtest/run", headers=H,
                       json={"strategy_ids": ["rsi_only"], "symbols": ["BTCUSDT"],
                             "days": 10, "timeframe": "15m", "execution": "local"},
                       timeout=30)
    assert bt.status_code in (200, 202), bt.text
    bid = bt.json().get("id") or bt.json().get("backtest_id") or bt.json().get("job_id")
    assert bid, f"no backtest id: {bt.json()}"
    bt_done = False
    for _ in range(120):
        bs = _get(f"{BASE}/api/backtest/status/{bid}", H)
        if bs.status_code != 200:
            # some backends use /api/backtest/{id}
            bs = _get(f"{BASE}/api/backtest/{bid}", H)
        bd = bs.json() if bs.status_code == 200 else {}
        if bd.get("status") == "done" or bd.get("done") or bd.get("finished"):
            bt_done = True
            break
        time.sleep(2)
    assert bt_done, f"backtest did not complete while optimizer paused: last={bd}"

    # 6) optimizer still paused
    s2 = _get(f"{BASE}/api/optimizer/status/{oid}", H).json()
    assert s2.get("paused") or s2.get("status") == "paused", f"optimizer no longer paused: {s2}"

    # 7) resume
    rr = requests.post(f"{BASE}/api/optimizer/resume/{oid}", headers=H, timeout=20)
    assert rr.status_code in (200, 202), rr.text
    resumed_prog = pre_progress
    for _ in range(45):
        s3 = _get(f"{BASE}/api/optimizer/status/{oid}", H).json()
        if not (s3.get("paused") or s3.get("status") == "paused"):
            p = s3.get("progress") or s3.get("iteration") or 0
            if p > pre_progress:
                resumed_prog = p
                break
        time.sleep(1)
    assert resumed_prog > pre_progress, f"progress did not increase after resume: pre={pre_progress} got={resumed_prog}"

    # 8) cancel to free resources
    rc = requests.post(f"{BASE}/api/optimizer/cancel/{oid}", headers=H, timeout=20)
    assert rc.status_code in (200, 202, 204), rc.text
