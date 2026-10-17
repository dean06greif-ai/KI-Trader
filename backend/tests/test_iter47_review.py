"""Iteration 47 review testing: Strategy lab migration, observer LLM off, seeding auto history."""
import os
import requests
import pytest

BASE_URL = os.environ.get('REACT_APP_BACKEND_URL', 'https://trader-recovery-1.preview.emergentagent.com').rstrip('/')
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{API}/auth/login", json={"username": "Admin", "password": "Dean06Greif!/Admin"}, timeout=15)
    assert r.status_code == 200, f"login failed {r.status_code} {r.text[:200]}"
    return r.json()["token"]


@pytest.fixture(scope="module")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


def test_login_ok(token):
    assert isinstance(token, str) and len(token) > 5


def test_ai_strategies_flags(auth):
    r = requests.get(f"{API}/ai/strategies", headers=auth, timeout=20)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    # inspect structure to find settings
    settings = data.get("settings") or data.get("status", {}).get("settings") or {}
    print("SETTINGS_KEYS:", list(settings.keys())[:30])
    assert settings.get("allow_ai_create") is False, f"allow_ai_create={settings.get('allow_ai_create')} settings={settings}"
    assert settings.get("auto_develop_enabled") is False, f"auto_develop_enabled={settings.get('auto_develop_enabled')}"

    # candidates
    candidates = data.get("candidates") or data.get("status", {}).get("candidates") or []
    print("CANDIDATE_COUNT:", len(candidates))
    test_prefixed = [c for c in candidates if str(c.get("name", "")).startswith("TEST_")]
    assert not test_prefixed, f"Found TEST_ candidates: {[c.get('name') for c in test_prefixed]}"

    bollinger = [c for c in candidates if "Bollinger" in str(c.get("name", ""))]
    assert bollinger, "Bollinger candidate not found"
    b = bollinger[0]
    print("BOLLINGER:", b.get("name"), b.get("stage"), (b.get("decision_note") or "")[:200])
    assert b.get("stage") == "rejected", f"stage={b.get('stage')}"
    note = str(b.get("decision_note") or "")
    assert "Migration 06.09." in note, f"decision_note missing migration marker: {note}"
    assert "trend_follow" in note, f"decision_note missing trend_follow: {note}"


def test_recovery_report_ok(auth):
    r = requests.get(f"{API}/admin/recovery/report", headers=auth, timeout=15)
    assert r.status_code == 200, r.text[:200]


def test_ai_status_observer_llm_off(auth):
    r = requests.get(f"{API}/ai/status", headers=auth, timeout=15)
    assert r.status_code == 200
    data = r.json()
    # try to find market_observer / summarizer roles
    print("STATUS_TOP_KEYS:", list(data.keys()))
    # Try /api/ai/roles first
    r2 = requests.get(f"{API}/ai/roles", headers=auth, timeout=15)
    roles_data = None
    if r2.status_code == 200:
        roles_data = r2.json()
        print("ROLES_KEYS:", list(roles_data.keys()) if isinstance(roles_data, dict) else type(roles_data))
    # search recursively
    def find_role(obj, name):
        if isinstance(obj, dict):
            if obj.get("id") == name or obj.get("name") == name or obj.get("role") == name:
                return obj
            if name in obj and isinstance(obj[name], dict):
                return obj[name]
            for v in obj.values():
                res = find_role(v, name)
                if res is not None:
                    return res
        elif isinstance(obj, list):
            for it in obj:
                res = find_role(it, name)
                if res is not None:
                    return res
        return None

    combined = {"status": data, "roles": roles_data}
    mo = find_role(combined, "market_observer")
    summ = find_role(combined, "summarizer")
    print("MARKET_OBSERVER:", mo)
    print("SUMMARIZER:", summ)
    assert mo is not None, "market_observer role not found"
    # llm_summary flag
    llm_summary = mo.get("llm_summary")
    if llm_summary is None and isinstance(mo.get("config"), dict):
        llm_summary = mo["config"].get("llm_summary")
    if llm_summary is None and isinstance(mo.get("settings"), dict):
        llm_summary = mo["settings"].get("llm_summary")
    assert llm_summary is False, f"market_observer.llm_summary={llm_summary} full={mo}"

    if summ is not None:
        enabled = summ.get("enabled")
        if enabled is None and isinstance(summ.get("config"), dict):
            enabled = summ["config"].get("enabled")
        assert enabled is not False, f"summarizer disabled? {summ}"


def test_seed_auto_history(auth):
    r = requests.get(f"{API}/ai/playbook/backtest", headers=auth, timeout=20)
    assert r.status_code == 200, r.text[:200]
    data = r.json()
    auto = data.get("auto") or {}
    history = auto.get("history") or []
    print("HISTORY_LEN:", len(history))
    print("HIST_ENTRY_KEYS:", list(history[0].keys()) if history else None)
    assert len(history) == 2, f"expected 2, got {len(history)}"
    required = {"at", "passed", "tested", "mode", "days", "asset_classes", "error"}
    for i, h in enumerate(history):
        missing = required - set(h.keys())
        assert not missing, f"entry {i} missing fields {missing}: {h}"

    r2 = requests.get(f"{API}/ai/playbook/backtest/auto", headers=auth, timeout=15)
    assert r2.status_code == 200
    hist2 = (r2.json() or {}).get("history") or []
    assert hist2 == history, "history differs between endpoints"


def test_seed_auto_post_regression(auth):
    payload = {"enabled": False, "interval_hours": 24, "days": 90,
               "asset_classes": ["crypto", "indices", "resources", "forex"]}
    r = requests.post(f"{API}/ai/playbook/backtest/auto", headers=auth, json=payload, timeout=20)
    assert r.status_code == 200, r.text[:300]
    data = r.json()
    print("POST_AUTO_RESP:", {k: data.get(k) for k in ["enabled", "interval_hours", "days"]})
    assert data.get("enabled") is False
    assert data.get("interval_hours") == 24
    hist = data.get("history") or []
    assert len(hist) == 2, f"history length changed: {len(hist)}"
