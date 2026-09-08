"""Live regression tests for iteration 3 bugfix (max_trades_per_day=0 semantics + related).
Hits the running backend via localhost:8001 (external ingress can be flaky in preview).
GET-only + one /api/auth/login POST + one 404 POST on candidates/approve with fake key.
"""
import os
import requests
import pytest

BASE_URL = "http://localhost:8001"
ADMIN_USER = "Admin"
ADMIN_PASS = "Dean06Greif!/Admin"


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": ADMIN_USER, "password": ADMIN_PASS}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("session")
    assert tok, f"no token in response: {data}"
    return tok


@pytest.fixture(scope="module")
def headers(token):
    return {"Authorization": f"Bearer {token}"}


def test_ai_master_prompt_returns_rules_dict(headers):
    r = requests.get(f"{BASE_URL}/api/ai/master-prompt", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)
    # rules dict must still be present (regression)
    assert "rules" in data or ("master_prompt" in data and "rules" in data["master_prompt"]), f"rules missing. keys={list(data.keys())}"
    rules = data.get("rules") or data.get("master_prompt", {}).get("rules")
    assert isinstance(rules, dict)


def test_ai_status_ok(headers):
    r = requests.get(f"{BASE_URL}/api/ai/status", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data, dict)
    # day_risk should exist; when trades_per_day is unlimited it should be null (not 0)
    dr = data.get("day_risk")
    if isinstance(dr, dict) and "max_trades_per_day" in dr:
        v = dr["max_trades_per_day"]
        assert v is None or v > 0, f"day_risk.max_trades_per_day should be null (unlimited) or positive, got {v!r}"


def test_ai_insights_has_lesson_candidates(headers):
    r = requests.get(f"{BASE_URL}/api/ai/insights", headers=headers, timeout=20)
    assert r.status_code == 200, r.text
    data = r.json()
    assert "lesson_candidates" in data, f"lesson_candidates missing. keys={list(data.keys())}"
    assert isinstance(data["lesson_candidates"], list)


def test_lessons_candidates_approve_404_for_missing_key(headers):
    r = requests.post(
        f"{BASE_URL}/api/ai/lessons/candidates/approve",
        headers=headers,
        json={"key": "nicht-existent-xyz-iter3-testing-agent"},
        timeout=15,
    )
    assert r.status_code == 404, f"expected 404, got {r.status_code}: {r.text}"
    body = r.text
    assert "nicht gefunden" in body.lower() or "not found" in body.lower(), body


def test_autotrade_strategy_scalping_ai_manage_default_false(headers):
    r = requests.get(f"{BASE_URL}/api/autotrade/strategy/scalping", headers=headers, timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    # ai_manage should be present and default False
    # It might be nested under "override" or "defaults" – search both
    def find_ai_manage(obj):
        if isinstance(obj, dict):
            if "ai_manage" in obj:
                return obj["ai_manage"]
            for v in obj.values():
                r = find_ai_manage(v)
                if r is not None:
                    return r
        return None
    val = find_ai_manage(data)
    assert val is False, f"ai_manage should default to False, got {val!r}. payload keys={list(data.keys()) if isinstance(data,dict) else type(data)}"
