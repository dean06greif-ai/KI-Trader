"""Read-only: Modell-Metadaten (Preis/Kontext) aus settings.model_watch (Prod)."""
import asyncio, os, re, json
from dotenv import load_dotenv; load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient
WANT = ("luna", "deepseek", "nemotron", "gemini-3.6", "gemini-3.5", "gpt-oss", "kimi", "glm", "qwen3.7", "haiku", "gemma-4", "mistral", "grok")
async def main():
    raw = os.environ['PROD_MONGO_URL']; url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    db = AsyncIOMotorClient(url)['crypto_scanner']
    mw = await db.settings.find_one({"_id": "model_watch"}) or {}
    print("keys:", [k for k in mw if k != "_id"])
    print("dead:", mw.get("dead"))
    for k in ("approved", "approved_meta", "rated", "last_new"):
        v = mw.get(k)
        if isinstance(v, dict):
            for mk, mv in v.items():
                if any(w in mk for w in WANT):
                    print(k, mk, json.dumps(mv, default=str)[:260])
        elif isinstance(v, list):
            for mv in v:
                s = json.dumps(mv, default=str)
                if any(w in s for w in WANT):
                    print(k, s[:260])
    for sid in ("ai_model_catalog", "model_catalog", "dynamic_models"):
        d = await db.settings.find_one({"_id": sid})
        if d: print(sid, json.dumps({k: v for k, v in d.items() if k != "_id"}, default=str)[:1500])
    async for d in db.ai_token_usage.find({"model": "openai/gpt-6-luna-pro"}).sort("date", 1):
        print("luna", d.get("date"), d.get("role"), d.get("calls"), d.get("tokens"))
asyncio.run(main())
