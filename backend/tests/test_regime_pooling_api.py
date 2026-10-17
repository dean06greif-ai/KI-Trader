"""API-Tests Partial Pooling (Iter 66): Overview + Validierung von POST,
ohne lange Cloud-Analyse laufen zu lassen (verwendet Seeds in test_database)."""
import os
import uuid
import time
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ai-daytrader-4.preview.emergentagent.com").rstrip("/")


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "admin"}, timeout=20)
    assert r.status_code == 200, r.text
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


def _seed_source(scope_doc_overrides=None):
    """Insert a group analysis doc directly into local test_database via API if possible.
    Falls back to direct Mongo write."""
    from pymongo import MongoClient
    cli = MongoClient("mongodb://localhost:27017")
    db = cli["test_database"]
    aid = "ra_test_" + uuid.uuid4().hex[:8]
    doc = {
        "id": aid, "name": "TEST_Group 1h", "timeframe": "1h", "days": 720,
        "symbols": ["BTCUSDT", "ETHUSDT", "SOLUSDT"], "created_at": time.time(),
        "settings": {"engine": "v2", "train_pct": 75, "engine_config": {"detector": "reactive"},
                     "confidence_min": 70, "min_hold_days": 2},
        "combined": {"model": {"config": {"detector": "reactive", "rev_atr_mult": 3.0,
                                          "side_leg_atr_mult": 1.5, "adapt_applied": "standard"}}},
    }
    if scope_doc_overrides:
        doc.update(scope_doc_overrides)
    db.regime_analyses.insert_one(doc)
    return aid


def _cleanup(aid):
    from pymongo import MongoClient
    MongoClient("mongodb://localhost:27017")["test_database"].regime_analyses.delete_one({"id": aid})


def test_pooling_overview_shape():
    r = requests.get(f"{BASE_URL}/api/regime-lab/pooling", timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(["sources", "pooled", "rules"]).issubset(data.keys())
    rules = data["rules"]
    assert rules["prior_phases"] == 40
    assert rules["prior_choices"] == [20, 40, 80]
    assert "factors" in rules and "scale_keys" in rules
    for s in data["sources"]:
        assert "check" in s and set(["ok", "reasons", "warnings"]).issubset(s["check"].keys())


def test_pooling_post_requires_admin():
    r = requests.post(f"{BASE_URL}/api/regime-lab/pooling", json={"analysis_id": "whatever"}, timeout=15)
    assert r.status_code in (401, 403), r.status_code


def test_pooling_post_rejects_unsuitable_source(auth_headers):
    aid = _seed_source({"symbols": ["BTCUSDT"]})  # <3 coins
    try:
        r = requests.post(f"{BASE_URL}/api/regime-lab/pooling",
                          json={"analysis_id": aid, "prior_phases": 40},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 400, r.text
        detail = r.json().get("detail", "")
        assert "mindestens 3" in detail or "Coin" in detail
    finally:
        _cleanup(aid)


def test_pooling_post_rejects_bad_prior(auth_headers):
    aid = _seed_source()
    try:
        r = requests.post(f"{BASE_URL}/api/regime-lab/pooling",
                          json={"analysis_id": aid, "prior_phases": 99},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 400, r.text
        assert "prior_phases" in r.json().get("detail", "")
    finally:
        _cleanup(aid)


def test_pooling_post_rejects_already_pooled(auth_headers):
    aid = _seed_source({"settings": {"engine": "v2", "train_pct": 75,
                                     "engine_config": {"detector": "reactive"},
                                     "pooling": {"coins": {}}}})
    try:
        r = requests.post(f"{BASE_URL}/api/regime-lab/pooling",
                          json={"analysis_id": aid, "prior_phases": 40},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 400, r.text
        assert "Pooling" in r.json().get("detail", "")
    finally:
        _cleanup(aid)


def test_pooling_post_rejects_train_100(auth_headers):
    aid = _seed_source({"settings": {"engine": "v2", "train_pct": 100,
                                     "engine_config": {"detector": "reactive"}}})
    try:
        r = requests.post(f"{BASE_URL}/api/regime-lab/pooling",
                          json={"analysis_id": aid, "prior_phases": 40},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 400, r.text
        assert "Holdout" in r.json().get("detail", "") or "100" in r.json().get("detail", "")
    finally:
        _cleanup(aid)
