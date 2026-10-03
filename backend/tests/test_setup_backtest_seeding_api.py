"""Iter 46 - backtest seeding API tests (uses production DB - read-only where possible)."""
import os
import time

import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # fallback: read frontend .env
    with open("/app/frontend/.env") as fh:
        for line in fh:
            if line.startswith("REACT_APP_BACKEND_URL="):
                BASE_URL = line.split("=", 1)[1].strip().rstrip("/")

ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    last = None
    for _ in range(6):
        try:
            r = requests.post(f"{BASE_URL}/api/auth/login",
                              json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=60)
            if r.status_code == 200:
                return r.json()["token"]
            last = r.text
        except Exception as e:
            last = str(e)
        time.sleep(5)
    pytest.fail(f"login failed: {last}")


@pytest.fixture(scope="module")
def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def test_overview_shape():
    r = requests.get(f"{BASE_URL}/api/ai/playbook/backtest", timeout=90)
    assert r.status_code == 200, r.text
    data = r.json()
    for key in ["eligible", "not_backtestable", "variants", "rules", "classes",
                "last_result", "running", "active"]:
        assert key in data, f"missing {key}: {list(data.keys())}"
    # alle regelbasierten Detektoren je Klasse (inkl. trend_follow2), no funding_fade
    from services.setup_backtest import detectors
    for cls, items in data["eligible"].items():
        assert len(items) == len(detectors.VARIANTS), f"{cls} eligible={items}"
        assert "trend_follow2" in items
        assert "funding_fade" not in items, f"funding_fade in {cls}"
    rules = data["rules"]
    assert rules["weight"] == 0.5
    assert rules["min_real_trades"] == 2
    assert rules["max_backtest_weighted"] == 3
    assert rules["min_oos_trades"] == 10


def test_run_requires_admin():
    r = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/run",
                      json={"asset_classes": ["indices"], "days": 90, "mode": "single"},
                      timeout=20)
    assert r.status_code in (401, 403), r.text


def test_reset_bad_class(hdr):
    r = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/reset",
                      json={"asset_class": "xyz"}, headers=hdr, timeout=20)
    assert r.status_code == 400


def test_playbook_shape():
    r = requests.get(f"{BASE_URL}/api/ai/playbook", timeout=45)
    assert r.status_code == 200, r.text
    d = r.json()
    for k in ["setups", "stats", "maturity", "classes", "class_order", "rules", "hard_locks"]:
        assert k in d, f"missing {k}"
    assert d["hard_locks"] is False
    assert "backtest_weight" in d["rules"]
    for cls, cd in d["classes"].items():
        assert "backtest" in cd
        assert "bt_promoted" in cd
    # maturity rows all have 'backtest' field
    for row in d.get("maturity", []):
        assert "backtest" in row


def test_run_start_and_status(hdr):
    """Start an indices run, poll status, ensure result shape when done. Also test 409 while running."""
    r = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/run",
                      json={"asset_classes": ["indices"], "days": 90, "mode": "single"},
                      headers=hdr, timeout=30)
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "started"
    job_id = data["job_id"]

    # 409 second run while first is running
    r2 = requests.post(f"{BASE_URL}/api/ai/playbook/backtest/run",
                       json={"asset_classes": ["resources"], "days": 90, "mode": "single"},
                       headers=hdr, timeout=20)
    assert r2.status_code == 409, r2.text

    # 409 on normal backtest while seeding runs
    r3 = requests.post(f"{BASE_URL}/api/backtest/run",
                       json={"symbols": ["QQQUSDT"], "strategy_ids": ["rsi_only"], "days": 30},
                       headers=hdr, timeout=20)
    assert r3.status_code == 409, r3.text
    assert "KI-Trader" in r3.text or "seeding" in r3.text.lower() or "ki-trader" in r3.text.lower()

    # poll status up to 5 min with retry on transient 502s
    deadline = time.time() + 300
    status = None
    sd = {}
    while time.time() < deadline:
        try:
            s = requests.get(f"{BASE_URL}/api/ai/playbook/backtest/status/{job_id}", timeout=30)
            if s.status_code == 200:
                sd = s.json()
                status = sd.get("status")
                if status in ("done", "error", "cancelled"):
                    break
        except Exception:
            pass
        time.sleep(5)
    assert status == "done", f"final status={status}, job={sd}"
    res = sd.get("result") or {}
    assert res.get("kind") == "ai_seed", res
    rows = res.get("rows") or []
    assert len(rows) == 9, f"rows={len(rows)}"
    for row in rows:
        for k in ["setup", "status", "is", "oos", "variant", "variants_total"]:
            assert k in row, f"row missing {k}: {row}"
        assert row["status"] in ("passed", "failed", "exhausted", "live", "no_data")
        for period in ("is", "oos"):
            for stk in ("trades", "wins", "pnl", "winrate"):
                assert stk in row[period], f"{period} missing {stk}"
