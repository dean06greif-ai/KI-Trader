"""Read-only: Warum lief der Analyst nicht auf seinem Primär-Modell? (Prod)"""
import asyncio, os, re, json
from dotenv import load_dotenv; load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient
async def main():
    raw = os.environ['PROD_MONGO_URL']; url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    db = AsyncIOMotorClient(url)['crypto_scanner']
    print("collections:", sorted(await db.list_collection_names()))
    for sid in ("ai_model_catalog_removed", "model_catalog", "ai_catalog"):
        d = await db.settings.find_one({"_id": sid})
        if d: print(sid, json.dumps(d, default=str)[:600])
    ids = [d["_id"] async for d in db.settings.find({}, {"_id": 1})]
    print("settings ids:", ids)
    async for c in db.ai_chat.find({"$or": [{"text": {"$regex": "luna", "$options": "i"}}, {"model": {"$regex": "luna"}}]}).sort("_id", -1).limit(6):
        print("chat", c.get("ts") or c.get("timestamp"), c.get("model"), str(c.get("text"))[:220])
asyncio.run(main())
