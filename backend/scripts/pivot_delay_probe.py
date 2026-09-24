"""Nur LESEND/offline: Umkehrpunkt-Verzögerung (corrections.avg_delay_days) und
Referenz-v2-Kennzahlen je Coin für ein gespeichertes Modell nachrechnen –
deckt „hängende“ Pivot-Scans auf (z.B. DOT/SUI nach dem Crash-Wick 10.10.2025).
  PROD_MONGO_URL=... python scripts/pivot_delay_probe.py ra_d41ad11b DOTUSDT,SUIUSDT,BTCUSDT
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient  # noqa: E402
from services import regime_lab as lab  # noqa: E402
from scripts.reference_window_probe import load_1h  # noqa: E402


async def main():
    aid, syms = sys.argv[1], sys.argv[2].split(",")
    doc = MongoClient(os.environ["PROD_MONGO_URL"])["crypto_scanner"].regime_analyses.find_one(
        {"id": aid}, {"combined.model": 1, "bounds": 1, "days": 1})
    model, b = doc["combined"]["model"], doc.get("bounds") or {}
    cfg = model["config"]
    for s in syms:
        c = await load_1h(s, int(doc["days"]) + 5)
        bs = b.get(s) or {}
        c = [k for k in c if bs.get("start_ts", 0) <= k["timestamp"] < bs.get("end_ts", 1 << 62)]
        _, e = lab._symbol_payload(model, c, "1h", float(cfg.get("confidence_min") or 0.55),
                                   float(cfg.get("min_hold_days") or 0), False,
                                   bs.get("train_end_ts"), bs.get("inner_start_ts"))
        corr, ref, la = e.get("corrections") or {}, e.get("reference") or {}, e.get("live_agreement") or {}
        print(f"{s}: Pivots {corr.get('pivots')} Ø Verzögerung {corr.get('avg_delay_days')} d "
              f"(max {corr.get('max_delay_days')}) | LF hold {la.get('holdout_direction_pct')} | "
              f"F1 hold {ref.get('holdout_f1_pct')} κ {ref.get('holdout_kappa_pct')} | "
              f"Richtungs-Phase {ref.get('live_direction_phase_days')} d", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
