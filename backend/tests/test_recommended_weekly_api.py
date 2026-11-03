# Recommended settings + edge-weekly + diagnosis edge_readiness API tests (public URL, local Mongo seed)
import os
import uuid
from datetime import datetime, timedelta, timezone

import pytest
import requests
from pymongo import MongoClient

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL").rstrip("/")
KEYS = ["live_gate_bypass_enabled", "edge_filter_enabled", "fee_guard_mult",
        "fee_guard_atr_mult", "guard_autotune_enabled", "maker_policy"]
EXPECTED = {"fee_guard_mult": 6.0, "fee_guard_atr_mult": 4.0, "guard_autotune_enabled": False,
            "maker_policy": "all", "live_gate_bypass_enabled": False, "edge_filter_enabled": True}


def _cfg(s):
    d = s.get(f"{BASE_URL}/api/ai/status", timeout=20).json()
    return d.get("config", d)


@pytest.fixture(scope="module")
def admin():
    s = requests.Session()
    r = s.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=20)
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code}")
    s.headers.update({"Authorization": f"Bearer {r.json()['token']}"})
    orig = {k: _cfg(s).get(k) for k in KEYS}
    print("ORIGINAL", orig)
    yield s
    rr = s.post(f"{BASE_URL}/api/ai/config", json=orig, timeout=20)
    print("RESTORE", rr.status_code, {k: _cfg(s).get(k) for k in KEYS})


# Recommended settings
def test_recommended_public():
    r = requests.get(f"{BASE_URL}/api/ai/recommended-settings", timeout=20)
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["key"] for i in items] == KEYS
    for i in items:
        for f in ("key", "label", "value", "current", "differs", "why"):
            assert f in i
        assert i["value"] == EXPECTED[i["key"]]


def test_apply_requires_admin():
    r = requests.post(f"{BASE_URL}/api/ai/recommended-settings/apply", timeout=20)
    assert r.status_code in (401, 403)


def test_apply_and_verify(admin):
    # force at least two diffs first
    admin.post(f"{BASE_URL}/api/ai/config", json={"fee_guard_mult": 3.5, "maker_policy": "off"}, timeout=20)
    before = {i["key"]: i for i in requests.get(f"{BASE_URL}/api/ai/recommended-settings", timeout=20).json()["items"]}
    differing = {k for k, i in before.items() if i["differs"]}
    r = admin.post(f"{BASE_URL}/api/ai/recommended-settings/apply", timeout=20)
    assert r.status_code == 200, r.text
    d = r.json()
    print("APPLIED", d["applied"])
    assert set(d["applied"].keys()) == differing
    after = requests.get(f"{BASE_URL}/api/ai/recommended-settings", timeout=20).json()["items"]
    assert all(not i["differs"] for i in after), after
    c = _cfg(admin)
    for k, v in EXPECTED.items():
        assert c.get(k) == v, (k, c.get(k))
    # idempotent
    r2 = admin.post(f"{BASE_URL}/api/ai/recommended-settings/apply", timeout=20)
    assert r2.json()["applied"] == {}


# Edge weekly
def test_edge_weekly_get():
    d = requests.get(f"{BASE_URL}/api/ai/edge-weekly", timeout=20).json()
    assert d["enabled"] is True and d["weekday"] == 6 and d["hour"] == 19


def test_edge_weekly_config_requires_admin():
    r = requests.post(f"{BASE_URL}/api/ai/edge-weekly/config", json={"hour": 5}, timeout=20)
    assert r.status_code in (401, 403)
    r = requests.post(f"{BASE_URL}/api/ai/edge-weekly/send", timeout=20)
    assert r.status_code in (401, 403)


def test_edge_weekly_config_persist(admin):
    try:
        r = admin.post(f"{BASE_URL}/api/ai/edge-weekly/config", json={"hour": 8}, timeout=20)
        assert r.status_code == 200 and r.json()["hour"] == 8
        assert requests.get(f"{BASE_URL}/api/ai/edge-weekly", timeout=20).json()["hour"] == 8
        r = admin.post(f"{BASE_URL}/api/ai/edge-weekly/config", json={"hour": 99}, timeout=20)
        assert r.json()["hour"] == 23
    finally:
        admin.post(f"{BASE_URL}/api/ai/edge-weekly/config", json={"hour": 19}, timeout=20)
    assert requests.get(f"{BASE_URL}/api/ai/edge-weekly", timeout=20).json()["hour"] == 19


def test_edge_weekly_config_bad_input(admin):
    r = admin.post(f"{BASE_URL}/api/ai/edge-weekly/config", json={"hour": "abc"}, timeout=20)
    print("BAD INPUT status", r.status_code, r.text[:200])
    assert r.status_code != 500 and r.status_code < 500


def test_edge_weekly_send(admin):
    r = admin.post(f"{BASE_URL}/api/ai/edge-weekly/send", timeout=60)
    assert r.status_code == 200, r.text
    d = r.json()
    print("SEND", d["sent"], d["report"][:300])
    assert isinstance(d["sent"], bool)
    assert "KI-TRADER WOCHEN-EDGE" in d["report"]


# Diagnosis with seeded trades
@pytest.fixture(scope="module")
def seeded():
    col = MongoClient("mongodb://localhost:27017")["test_database"]["auto_trades"]
    tag = f"TEST_{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc)
    docs = []
    for i in range(5):
        docs.append({"id": f"{tag}_{i}", "test_tag": tag, "strategy_id": "ai_trader", "status": "closed",
                     "risk_usdt": 10.0, "realized_pnl": [5, -10, 12, -3, 8][i], "fees_paid": 0.5,
                     "opened_at": (now - timedelta(days=1, hours=i)).isoformat(),
                     "closed_at": (now - timedelta(days=1, hours=i) + timedelta(minutes=40)).isoformat(),
                     "side": "long", "symbol": "BTCUSDT", "mode": "paper", "edge_filter": {"ok": True}})
    docs.append({"id": f"{tag}_s", "test_tag": tag, "strategy_id": "ai_trader", "status": "closed",
                 "risk_usdt": 10.0, "realized_pnl": -6, "fees_paid": 0.5, "side": "short", "symbol": "BTCUSDT",
                 "opened_at": (now - timedelta(days=2)).isoformat(), "mode": "paper",
                 "guard_shadow": {"guard": "edge_filter"}, "edge_filter": {"ok": False}})
    col.insert_many(docs)
    yield tag
    col.delete_many({"test_tag": tag})
    assert col.count_documents({"test_tag": tag}) == 0


def test_diagnosis_edge_readiness(seeded):
    r = requests.get(f"{BASE_URL}/api/ai/diagnosis", timeout=60)
    assert r.status_code == 200, r.text
    edge = r.json().get("edge") or {}
    er = edge.get("edge_readiness")
    print("READINESS", er, "BLOCKS", edge.get("edge_filter_blocks"))
    assert er and er["trades"] >= 5 and er["min_trades"] == 60 and er["ready"] is False
    assert "Noch nicht bereit" in er["text"]
    assert edge["edge_filter_blocks"]["with_risk"] >= 1


def test_weekly_report_with_seed(admin, seeded):
    d = admin.post(f"{BASE_URL}/api/ai/edge-weekly/send", timeout=60).json()
    print("REPORT\n", d["report"])
    assert "Trades:" in d["report"] and "Live-Freigabe-Check" in d["report"]
