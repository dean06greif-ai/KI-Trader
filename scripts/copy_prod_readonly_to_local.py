"""Kopiert NUR Lesedaten (geschlossene KI-Trades + Playbook-Status) aus der
Produktions-DB in die lokale Test-DB – Prod wird ausschließlich gelesen."""
import asyncio

from motor.motor_asyncio import AsyncIOMotorClient


def _env(path):
    out = {}
    for line in open(path):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            out[k] = v.strip().strip('"')
    return out


async def main():
    env = _env("/app/backend/.env.prod")
    prod = AsyncIOMotorClient(env["MONGO_URL"])[env.get("DB_NAME", "crypto_scanner")]
    local = AsyncIOMotorClient("mongodb://localhost:27017")["crypto_scanner"]
    rows = await prod.auto_trades.find({"strategy_id": "ai_trader", "status": "closed"}).to_list(20000)
    await local.auto_trades.delete_many({"strategy_id": "ai_trader"})
    if rows:
        await local.auto_trades.insert_many(rows)
    doc = await prod.settings.find_one({"_id": "ai_playbook_state"})
    if doc:
        await local.settings.replace_one({"_id": "ai_playbook_state"}, doc, upsert=True)
    print(f"kopiert: {len(rows)} Trades, playbook_state={'ja' if doc else 'nein'}")


asyncio.run(main())
