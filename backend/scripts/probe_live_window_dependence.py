"""Nur LESEND. Beweis-Experiment: Hängt das LIVE-Regime-Label vom Datenfenster ab?

Der KI-Trader (services/structural_regime.py) berechnet das Struktur-Regime auf
DETECT_DAYS=30 Tagen Historie. Das Lab bewertet die Live-Sicht dagegen auf der
vollen Historie (z.B. 1080 Tage). Hier wird für echte Kerzen geprüft, wie oft
das Label „30-Tage-Fenster“ vom Label „volle Historie“ am selben Zeitpunkt abweicht.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

import aiohttp  # noqa: E402
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402

from services import regime_engine as eng  # noqa: E402
from services.backtester import fetch_history  # noqa: E402
from services.timeframes import aggregate_candles  # noqa: E402

SYMBOL = os.environ.get("PROBE_SYMBOL", "BTCUSDT")
DAYS = int(os.environ.get("PROBE_DAYS", "400"))
TF = "1h"
WINDOW_BARS = int(os.environ.get("PROBE_WINDOW_BARS", 24 * 30))  # DETECT_DAYS=30 im structural_regime
WINDOW_FROM_MODEL = os.environ.get("PROBE_WINDOW_FROM_MODEL") == "1"
EVAL_DAYS = 90
STEP = 6                       # alle 6 Stunden ein Vergleichspunkt


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    async with aiohttp.ClientSession() as s:
        raw = await fetch_history(s, SYMBOL, DAYS)
    candles = aggregate_candles(raw, TF, drop_partial=True)
    print(f"{SYMBOL} {TF}: {len(candles)} Kerzen")
    models = {}
    for aid in ("ra_0d9f787d", "ra_5c8e1115"):
        doc = await db.regime_analyses.find_one({"id": aid}, {"combined.model": 1, "name": 1})
        if doc:
            models[f"{aid} ({doc['name']})"] = doc["combined"]["model"]
    # zusätzlich: reaktiver Detektor 1h/5er auf denselben Daten (Standard-Config)
    cut = int(len(candles) * 0.75)
    m_rx = eng.build_model({SYMBOL: candles[:cut]}, TF, {"detector": "reactive", "regime_mode": 5})
    if m_rx:
        models["reactive 1h mode5 (frisch)"] = m_rx
    n = len(candles)
    idx = list(range(n - EVAL_DAYS * 24, n, STEP))
    for name, model in models.items():
        cfg = model["config"]
        mode = eng.norm_mode(cfg.get("regime_mode"))
        full = eng.classify_series(model, candles)
        wb = WINDOW_BARS
        if WINDOW_FROM_MODEL:
            wb = int(eng.required_history_bars(cfg))
        mism_id = mism_dir = tot = 0
        for t in idx:
            win = eng.classify_series(model, candles[max(0, t - wb):t + 1])
            a, b = full[t], win[-1]
            if a is None or b is None:
                continue
            tot += 1
            mism_id += int(a != b)
            mism_dir += int(eng.split_id(a, mode)[0] != eng.split_id(b, mode)[0])
        print(f"\n{name}: detector={cfg.get('detector')} mode={mode} warmup_bars={cfg.get('warmup_bars')} "
              f"vol_ref_bars={cfg.get('vol_ref_bars')} ema_slow_bars={cfg.get('ema_slow_bars')}")
        print(f"  Vergleichspunkte: {tot} | Regime-ID abweichend: {mism_id} ({mism_id / max(tot, 1) * 100:.1f}%) "
              f"| Richtung abweichend: {mism_dir} ({mism_dir / max(tot, 1) * 100:.1f}%)")


asyncio.run(main())
