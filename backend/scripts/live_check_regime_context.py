"""Live-Check (nur lesend): Prompt-Block „Regime-Bilanz“ gegen den echten Datenstand."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    from services import regime_cockpit, regime_context
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    syms = sys.argv[1].split(",") if len(sys.argv) > 1 else ["BTCUSDT", "ETHUSDT"]
    await regime_cockpit.overview(db, syms, 14)          # Cache füllen (im Betrieb macht das der Hintergrund)
    print(await regime_context.prompt_block(db, syms) or "(leer – nichts Belastbares)")

asyncio.run(main())
