import asyncio
import sys
import time

sys.path.insert(0, "/app/backend")
import aiohttp
from services import history_sources as hs


async def main():
    end = int(time.time() * 1000) - 400 * 86400 * 1000
    start = end - 3 * 86400 * 1000
    async with aiohttp.ClientSession() as s:
        blocks = await hs.fetch_backup(s, "GOLD", start, end)
    n = sum(b.shape[0] for b in blocks)
    print("blocks:", len(blocks), "candles:", n)
    if blocks:
        print("first row:", blocks[0][0].tolist())
        print("last row:", blocks[-1][-1].tolist())


asyncio.run(main())
