"""Diagnose R06: verändert eine Kopf-Erweiterung des candle_cache bestehende Kerzen?"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(os.path.dirname(os.path.dirname(__file__)), "backend", ".env"))

from services import regime_lab as lab  # noqa: E402
from services import research_dataset as rd  # noqa: E402
from services import candle_cache  # noqa: E402

candle_cache.DISK_ENABLED = False


async def main():
    sym, tf, days = "BTCUSDT", "15m", 90
    h1 = (await lab.fetch_histories([sym], days, tf, None))[sym]
    m1 = rd.symbol_manifest(h1)
    print("first ", m1)
    h2 = (await lab.fetch_histories([sym], days, tf, None,
                                    end_ts={sym: m1["end_ts"]}, start_ts={sym: m1["start_ts"]}))[sym]
    m2 = rd.symbol_manifest(h2)
    print("anchor", m2)
    if m1["hash"] != m2["hash"]:
        d1 = {c["timestamp"]: c for c in h1}
        n = 0
        for c in h2:
            a = d1.get(c["timestamp"])
            if a is None or any(abs(float(a[k]) - float(c[k])) > 1e-9 for k in ("open", "high", "low", "close", "volume")):
                n += 1
                if n <= 5:
                    print("DIFF", c["timestamp"], a and {k: a[k] for k in ("open", "high", "low", "close", "volume")},
                          {k: c[k] for k in ("open", "high", "low", "close", "volume")})
        print("total diffs", n, "of", len(h2))


asyncio.run(main())
