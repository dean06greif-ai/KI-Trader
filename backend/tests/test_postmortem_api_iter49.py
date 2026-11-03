"""Postmortem API end-to-end tests (Iteration 49 review request)."""
import os
import requests

BASE = (os.environ.get("REACT_APP_BACKEND_URL") or "http://localhost:8001").rstrip("/")
API = f"{BASE}/api"
ADMIN = {"username": "Admin", "password": "Dean06Greif!/Admin"}


def _token():
    r = requests.post(f"{API}/auth/login", json=ADMIN, timeout=15)
    assert r.status_code == 200, r.text
    return r.json().get("token")


# ---- Postmortem summary ----
def test_summary_all_seeded():
    r = requests.get(f"{API}/ai/postmortem/summary", params={"days": 7}, timeout=20)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["reviews"] == 12, j["reviews"]
    setups = {s["setup"]: s for s in j["setups"]}
    assert "breakout" in setups and "pullback" in setups
    for name, s in setups.items():
        assert s["trades"] == 6
        assert s["enough_data"] is False
        assert s["finding"] is None
        # variant keys present
        for key in ("tp_x0.75", "tp_x1.25", "tp_x1.5", "tp_x2",
                    "sl_x0.75", "sl_x1.25", "sl_x1.5", "runner"):
            assert key in s["variants"], (name, key)
            v = s["variants"][key]
            assert "median_delta_r" in v and "robust" in v and "reason" in v
    assert j["limits"]["n"] >= 1
    assert isinstance(j["recent"], list) and len(j["recent"]) == 12
    assert j["rules"]["min_sample"] == 8
    assert j["rules"]["min_consistency"] == 0.6
    assert j["rules"]["min_gain_r"] == 0.15


def test_summary_mode_split_live_paper_sums_to_all():
    def n(mode):
        r = requests.get(f"{API}/ai/postmortem/summary", params={"days": 7, "mode": mode}, timeout=20)
        assert r.status_code == 200, r.text
        return r.json()["reviews"]
    live = n("live")
    paper = n("paper")
    total = n("all")
    assert live == 6 and paper == 6 and total == 12, (live, paper, total)


# ---- Postmortem per-trade ----
def test_trade_detail_seeded():
    r = requests.get(f"{API}/ai/postmortem/trade/seed-pm-0", timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["trade_id"] == "seed-pm-0"
    assert "base_r" in j
    assert j["verdict"] in {"early_exit", "stopped_before_move", "good_exit", "right_stop", "neutral"}
    for key in ("tp_x0.75", "tp_x1.25", "tp_x1.5", "tp_x2",
                "sl_x0.75", "sl_x1.25", "sl_x1.5", "runner"):
        assert key in j["variants"]
    assert "minutes" in j["after"] and "mfe_r" in j["after"] and "mae_r" in j["after"]


def test_trade_detail_missing_returns_pending():
    r = requests.get(f"{API}/ai/postmortem/trade/does-not-exist", timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("trade_id") == "does-not-exist"
    assert j.get("status") == "pending"


# ---- Postmortem context (must be empty when n<8 per setup) ----
def test_context_empty_when_not_enough_data():
    r = requests.get(f"{API}/ai/postmortem/context", timeout=15)
    assert r.status_code == 200, r.text
    j = r.json()
    assert "text" in j
    assert j["text"] == "" or j["text"].strip() == ""


# ---- Postmortem run auth & idempotence ----
def test_run_requires_admin():
    r = requests.post(f"{API}/ai/postmortem/run", timeout=15)
    assert r.status_code in (401, 403), (r.status_code, r.text)


def test_run_with_admin_idempotent():
    tok = _token()
    hdr = {"Authorization": f"Bearer {tok}"}
    r = requests.post(f"{API}/ai/postmortem/run", headers=hdr, timeout=60)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j.get("status") == "ok"
    assert "trades" in j and "limits" in j and "skipped" in j
    # second run should be idempotent (trades already reviewed)
    r2 = requests.post(f"{API}/ai/postmortem/run", headers=hdr, timeout=60)
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2.get("trades", 0) == 0, j2


# ---- Regression: existing endpoints still 200 ----
def test_regression_health_ai_status():
    for path in ("/health", "/ai/status"):
        r = requests.get(f"{API}{path}", timeout=15)
        assert r.status_code == 200, (path, r.status_code, r.text[:200])
