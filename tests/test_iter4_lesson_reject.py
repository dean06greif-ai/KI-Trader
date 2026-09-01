"""Backend tests for the 'reject lesson candidate' feature (iteration 4).

- 404 for unknown key with admin
- 401 without auth
- Synthetic roundtrip: insert temp candidate -> delete -> verify removal & block-list
- Unit test for ai_learning._bump_lesson_candidate returning None on blocked key
- Cleanup ai_lesson_rejects.keys of the test key at the end
"""
import os
import sys
import asyncio
import pytest
import requests
from datetime import datetime, timezone

sys.path.insert(0, "/app/backend")

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://ml-fx-pro.preview.emergentagent.com").rstrip("/")
TEST_KEY = "testagent-reject-demo"


@pytest.fixture(scope="module")
def admin_token():
    r = requests.post(f"{BASE_URL}/api/auth/login",
                      json={"username": "Admin", "password": "Dean06Greif!/Admin"},
                      timeout=15)
    assert r.status_code == 200, r.text
    data = r.json()
    tok = data.get("token") or data.get("access_token") or data.get("session_token")
    assert tok, f"no token in {data}"
    return tok


@pytest.fixture(scope="module")
def auth_headers(admin_token):
    return {"Authorization": f"Bearer {admin_token}"}


@pytest.fixture(scope="module")
def db():
    from motor.motor_asyncio import AsyncIOMotorClient
    from dotenv import load_dotenv
    load_dotenv("/app/backend/.env")
    client = AsyncIOMotorClient(os.environ["MONGO_URL"])
    return client[os.environ["DB_NAME"]]


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.new_event_loop().run_until_complete(coro)


def test_reject_without_auth():
    r = requests.post(f"{BASE_URL}/api/ai/lessons/candidates/delete",
                      json={"key": "whatever"}, timeout=15)
    assert r.status_code == 401, r.text


def test_reject_unknown_key_404(auth_headers):
    r = requests.post(f"{BASE_URL}/api/ai/lessons/candidates/delete",
                      json={"key": "definitely-not-existing-xyz-123"},
                      headers=auth_headers, timeout=15)
    assert r.status_code == 404
    assert "nicht gefunden" in r.text


def test_roundtrip_synthetic_candidate(auth_headers, db):
    async def _seed():
        await db.ai_lesson_candidates.delete_one({"key": TEST_KEY})
        await db.settings.update_one(
            {"_id": "ai_lesson_rejects"},
            {"$pull": {"keys": TEST_KEY}}
        )
        await db.ai_lesson_candidates.insert_one({
            "key": TEST_KEY,
            "title": "Testagent Reject Demo",
            "detail": "nur test",
            "confirmations": 1,
            "weight": 2,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })

    async def _check_removed():
        cand = await db.ai_lesson_candidates.find_one({"key": TEST_KEY})
        doc = await db.settings.find_one({"_id": "ai_lesson_rejects"}) or {}
        return cand, (doc.get("keys") or [])

    async def _cleanup():
        await db.ai_lesson_candidates.delete_one({"key": TEST_KEY})
        await db.settings.update_one(
            {"_id": "ai_lesson_rejects"},
            {"$pull": {"keys": TEST_KEY}}
        )

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed())

        r = requests.post(f"{BASE_URL}/api/ai/lessons/candidates/delete",
                          json={"key": TEST_KEY},
                          headers=auth_headers, timeout=15)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("status") == "success"
        assert data.get("deleted") == TEST_KEY

        cand, reject_keys = loop.run_until_complete(_check_removed())
        assert cand is None, "candidate must be removed from ai_lesson_candidates"
        assert TEST_KEY in reject_keys, f"key must be in ai_lesson_rejects.keys, got {reject_keys[-5:]}"
    finally:
        loop.run_until_complete(_cleanup())
        loop.close()


def test_bump_lesson_candidate_returns_none_when_blocked():
    """Unit-Check: _bump_lesson_candidate returns None for blocked keys."""
    from services.ai_learning import AILearning
    from motor.motor_asyncio import AsyncIOMotorClient

    async def _run_check():
        client = AsyncIOMotorClient(os.environ["MONGO_URL"])
        db = client[os.environ["DB_NAME"]]
        # add test key to block list
        await db.settings.update_one(
            {"_id": "ai_lesson_rejects"},
            {"$addToSet": {"keys": TEST_KEY}},
            upsert=True,
        )
        try:
            learning = AILearning(type("E", (), {"db": db})())
            result = await learning._bump_lesson_candidate(
                key=TEST_KEY, title="t", detail="d", model="m", weight=2, stats={}
            )
            assert result is None, f"expected None for blocked key, got {result}"
            # verify no candidate was inserted
            leaked = await db.ai_lesson_candidates.find_one({"key": TEST_KEY})
            assert leaked is None, "candidate must not be inserted for blocked key"
        finally:
            # cleanup
            await db.settings.update_one(
                {"_id": "ai_lesson_rejects"},
                {"$pull": {"keys": TEST_KEY}},
            )
            await db.ai_lesson_candidates.delete_one({"key": TEST_KEY})

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run_check())
    finally:
        loop.close()
