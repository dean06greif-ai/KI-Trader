"""Read-only: einzelne settings-Dokumente ausgeben (Prod). Aufruf: ... id1 id2"""
import asyncio, os, re, json, sys
from dotenv import load_dotenv; load_dotenv('/app/backend/.env')
from motor.motor_asyncio import AsyncIOMotorClient
SECRET = re.compile(r"(key|token|secret|password)", re.I)
def scrub(o):
    if isinstance(o, dict): return {k: ("***" if SECRET.search(k) and isinstance(v, str) else scrub(v)) for k, v in o.items()}
    if isinstance(o, list): return [scrub(x) for x in o]
    return o
async def main():
    raw = os.environ['PROD_MONGO_URL']; url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    db = AsyncIOMotorClient(url)['crypto_scanner']
    for sid in sys.argv[1:]:
        d = await db.settings.find_one({"_id": sid})
        print(sid, json.dumps(scrub(d), default=str)[:2500], "\n")
asyncio.run(main())
