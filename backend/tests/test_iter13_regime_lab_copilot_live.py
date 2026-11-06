"""Iteration 13 live regression: Regime-Lab Copilot isolation + history separation.
Exercises the real backend behind REACT_APP_BACKEND_URL.
"""
import os
import re
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://strategy-optimize-1.preview.emergentagent.com").rstrip("/")
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"

OPTIMIZER_TERMS = [
    r"discovery", r"deep[- ]?test", r"iterationen", r"objective",
    r"strategien[- ]?optimier", r"suchmod",
]


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=30)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    tok = r.json().get("token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _poll(job_id, headers, max_wait=180):
    deadline = time.time() + max_wait
    last = None
    while time.time() < deadline:
        r = requests.get(f"{BASE_URL}/api/copilot/chat/job/{job_id}", headers=headers, timeout=20)
        assert r.status_code == 200, r.text
        last = r.json()
        if last.get("status") in ("done", "error"):
            return last
        time.sleep(3)
    return last


def test_copilot_status_ready(headers):
    r = requests.get(f"{BASE_URL}/api/copilot/status", headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("ready") is True, body


def test_regime_lab_chat_reply_is_scoped(headers):
    payload = {
        "message": "Was ist mein nächster Schritt?",
        "context": {
            "panel": "regime_lab",
            "settings": {"coins": ["BTCUSDT"], "timeframe": "15m", "days": 360, "train_pct": 75},
            "regime": {
                "detector": "ema",
                "calibration": {"after_pct": 48.9},
                "analysis": None,
            },
        },
    }
    r = requests.post(f"{BASE_URL}/api/copilot/chat/start", headers=headers, json=payload, timeout=30)
    assert r.status_code == 200, r.text
    job_id = r.json().get("job_id")
    assert job_id
    result = _poll(job_id, headers, max_wait=180)

    # Retry once on overload/error before failing
    if not result or result.get("status") == "error":
        detail = (result or {}).get("error") or (result or {}).get("reply") or ""
        if "überlastet" in detail.lower() or "overload" in detail.lower() or result is None:
            time.sleep(5)
            r2 = requests.post(f"{BASE_URL}/api/copilot/chat/start", headers=headers, json=payload, timeout=30)
            assert r2.status_code == 200, r2.text
            result = _poll(r2.json()["job_id"], headers, max_wait=180)

    assert result and result.get("status") == "done", f"result={result}"
    inner = result.get("result") or {}
    reply = (result.get("reply") or inner.get("reply") or "").strip()
    assert reply, f"empty reply: {result}"

    low = reply.lower()
    # Must sound German-ish (simple heuristic – contains at least one common German word)
    german_markers = ["der", "die", "das", "und", "ist", "kalibr", "regime", "analyse"]
    assert any(w in low for w in german_markers), f"reply not German? {reply[:400]}"

    # Must mention Regime-Lab domain (at least one)
    domain = ["kalibr", "regime", "autopilot", "shadow", "analyse", "detector"]
    assert any(w in low for w in domain), f"reply not scoped to Regime-Lab: {reply[:400]}"

    # Must NOT reference optimizer search modes
    for pat in OPTIMIZER_TERMS:
        assert not re.search(pat, low), f"reply leaked optimizer term {pat!r}: {reply[:400]}"

    # store reply excerpt for the next test via module attribute
    test_regime_lab_chat_reply_is_scoped.reply = reply[:200]


def test_history_separation(headers):
    r_rl = requests.get(f"{BASE_URL}/api/copilot/history", headers=headers,
                        params={"panel": "regime_lab", "limit": 20}, timeout=20)
    assert r_rl.status_code == 200, r_rl.text
    rl_items = r_rl.json().get("messages") or r_rl.json().get("items") or r_rl.json().get("history") or []
    assert isinstance(rl_items, list) and len(rl_items) > 0, r_rl.json()
    rl_text = " ".join((it.get("content", "") or it.get("message", "") or it.get("reply", "")) for it in rl_items).lower()
    assert "nächster schritt" in rl_text or "kalibr" in rl_text, "regime_lab history empty of new msg"

    r_opt = requests.get(f"{BASE_URL}/api/copilot/history", headers=headers,
                         params={"panel": "optimizer", "limit": 20}, timeout=20)
    assert r_opt.status_code == 200, r_opt.text
    opt_items = r_opt.json().get("messages") or r_opt.json().get("items") or r_opt.json().get("history") or []
    opt_text = " ".join((it.get("content", "") or it.get("message", "") or it.get("reply", "")) for it in (opt_items or [])).lower()
    assert "was ist mein nächster schritt" not in opt_text, "leak into optimizer history"
