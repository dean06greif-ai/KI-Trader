# Iteration 8: dynamic test-leftovers endpoints, delete regression, evidence regime_evidence/warnings
import os
import pytest
import requests
from pymongo import MongoClient
from dotenv import dotenv_values

FE = dotenv_values("/app/frontend/.env")
BE = dotenv_values("/app/backend/.env")
BASE_URL = FE["REACT_APP_BACKEND_URL"].rstrip("/")
IDS = ["tl-a", "tl-b", "tl-c", "tl-d", "tl-e"]


@pytest.fixture(scope="module")
def db():
    c = MongoClient(BE["MONGO_URL"])
    d = c[BE["DB_NAME"]]
    d.dynamic_strategies.delete_many({"id": {"$in": IDS}})
    d.dynamic_strategies.insert_many([
        {"id": "tl-a", "name": "TEST_cleanup_demo", "created_at": "2026-11-01T00:00:00+00:00"},
        {"id": "tl-b", "name": "AP04-Test-x", "release": {"status": "approved"}, "created_at": "2026-11-01T00:00:00+00:00"},
        {"id": "tl-c", "name": "Backtest Champion", "created_at": "2026-11-01T00:00:00+00:00"},
        {"id": "tl-d", "name": "Regime9 Fast", "model": {"regime_mode": 9, "timeframe": "15m", "regimes": []},
         "created_at": "2026-11-01T00:00:00+00:00"},
        {"id": "tl-e", "name": "Regime5 Slow", "model": {"regime_mode": 5, "timeframe": "4h", "regimes": []},
         "created_at": "2026-11-01T00:00:00+00:00"},
    ])
    yield d
    d.dynamic_strategies.delete_many({"id": {"$in": IDS}})
    c.close()


@pytest.fixture(scope="module")
def token():
    r = requests.post(f"{BASE_URL}/api/auth/login", json={"username": "Admin", "password": "admin"})
    if r.status_code != 200:
        pytest.skip(f"login failed {r.status_code} {r.text}")
    d = r.json()
    return d.get("token") or d.get("access_token")


def H(t):
    return {"Authorization": f"Bearer {t}"}


def test_leftovers_preview(db):
    r = requests.get(f"{BASE_URL}/api/dynamic/test-leftovers")
    assert r.status_code == 200
    d = r.json()
    a = [x["id"] for x in d["archive"]]
    s = [x["id"] for x in d["skipped"]]
    assert "tl-a" in a and "tl-b" not in a and "tl-c" not in a
    assert "tl-b" in s and "tl-c" not in s
    assert "tl-d" not in a and "tl-e" not in a


def test_archive_requires_admin(db):
    r = requests.post(f"{BASE_URL}/api/dynamic/test-leftovers/archive")
    assert r.status_code == 401


def test_evidence_warnings(db):
    r = requests.get(f"{BASE_URL}/api/dynamic/tl-d/evidence")
    assert r.status_code == 200, r.text
    d = r.json()["evidence"]
    assert "regime_evidence" in d["validation"]
    assert d["validation"]["regime_evidence"] is None
    w = " ".join(d["warnings"])
    assert "9-Regime" in w and "15m" in w
    r2 = requests.get(f"{BASE_URL}/api/dynamic/tl-e/evidence").json()["evidence"]
    assert not any("9-Regime" in x or "Regime auf" in x for x in r2["warnings"])


def test_archive_with_admin(db, token):
    r = requests.post(f"{BASE_URL}/api/dynamic/test-leftovers/archive", headers=H(token))
    assert r.status_code == 200, r.text
    d = r.json()
    assert "tl-a" in [x["id"] for x in d["archived"]]
    assert "tl-b" in [x["id"] for x in d["skipped"]]
    assert db.dynamic_strategies.find_one({"id": "tl-a"})["archived"] is True
    assert not db.dynamic_strategies.find_one({"id": "tl-b"}).get("archived")
    assert not db.dynamic_strategies.find_one({"id": "tl-c"}).get("archived")
    ids = [x["id"] for x in requests.get(f"{BASE_URL}/api/dynamic/list").json()["strategies"]]
    assert "tl-a" not in ids and "tl-c" in ids
    left = requests.get(f"{BASE_URL}/api/dynamic/test-leftovers").json()
    assert "tl-a" not in [x["id"] for x in left["archive"]]


def test_delete_still_archives(db, token):
    assert requests.delete(f"{BASE_URL}/api/dynamic/tl-c").status_code == 401
    r = requests.delete(f"{BASE_URL}/api/dynamic/tl-c", headers=H(token))
    assert r.status_code == 200, r.text
    assert r.json()["archived"] is True
    assert db.dynamic_strategies.find_one({"id": "tl-c"})["archived"] is True
    ids = [x["id"] for x in requests.get(f"{BASE_URL}/api/dynamic/list").json()["strategies"]]
    assert "tl-c" not in ids
    assert requests.delete(f"{BASE_URL}/api/dynamic/nope-xyz", headers=H(token)).status_code == 404
