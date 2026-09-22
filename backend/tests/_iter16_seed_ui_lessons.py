import asyncio, os, sys, json
from motor.motor_asyncio import AsyncIOMotorClient

MONGO = os.environ.get("MONGO_URL", "mongodb://localhost:27017")
DB = os.environ.get("DB_NAME", "crypto_scanner")

DORM = {"id": "les_UITESTdorm", "title": "UI Test Dormant", "detail": "x", "origin": "ai",
        "locked": False, "status": "dormant", "dormant_since": "2026-08-20T00:00:00+00:00"}
ACT = {"id": "les_UITESTact", "title": "UI Test Aktiv", "detail": "y", "origin": "ai",
       "locked": False, "confirmations": 3}


async def main(action):
    cli = AsyncIOMotorClient(MONGO)
    db = cli[DB]
    doc = await db.settings.find_one({"_id": "ai_lessons"}) or {}
    lessons = [l for l in (doc.get("lessons") or []) if not str(l.get("id", "")).startswith("les_UITEST")]
    if action == "seed":
        lessons = lessons + [DORM, ACT]
    await db.settings.update_one({"_id": "ai_lessons"}, {"$set": {"lessons": lessons}}, upsert=True)
    doc2 = await db.settings.find_one({"_id": "ai_lessons"})
    out = [{"id": l.get("id"), "status": l.get("status"), "confirmations": l.get("confirmations")}
           for l in (doc2.get("lessons") or [])]
    print(json.dumps(out, indent=1))
    cli.close()


asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "seed"))
