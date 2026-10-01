"""Nur LESEND: gespeicherte Regime-Modelle (Atlas) gegen Referenz v1 und v2
auswerten (öffentliche Bitunix-1h-Kerzen). Aufruf (PROD_MONGO_URL setzen):
  python scripts/reference_v2_probe.py ra_d41ad11b BTCUSDT,ETHUSDT,SOLUSDT 810
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pymongo import MongoClient  # noqa: E402
from services import regime_lab as lab, regime_engine as eng, regime_reference as rr  # noqa: E402
from services import regime_truth as rt, research_validation  # noqa: E402
from scripts.reference_window_probe import load_1h  # noqa: E402


async def main():
    aid = sys.argv[1]
    syms = sys.argv[2].split(",")
    days = int(sys.argv[3]) if len(sys.argv) > 3 else 810
    doc = MongoClient(os.environ["PROD_MONGO_URL"])["crypto_scanner"].regime_analyses.find_one(
        {"id": aid}, {"combined.model": 1, "timeframe": 1, "name": 1})
    model = doc["combined"]["model"]
    cfg = model["config"]
    mode = eng.norm_mode(cfg.get("regime_mode", 9))
    print(f"{aid} {doc['name']} det={cfg.get('detector')} mode={mode} horizons={cfg.get('horizons_days')}")
    for sym in syms:
        c = await load_1h(sym, days)
        cut = int(len(c) * 0.75)
        tr_end, inner = int(c[cut - 1]["timestamp"]), research_validation.inner_anchor_ts(c, cut)
        labels, entry = lab._symbol_payload(model, c, "1h", float(cfg.get("confidence_min") or 0.55),
                                            float(cfg.get("min_hold_days") or 0), False, tr_end, inner)
        v2 = entry.get("reference") or {}
        # v1 zum Vergleich (altes Fenster aus dem Modell)
        ideal1 = rt.centered_labels(c, dict(cfg), mode)
        la = entry.get("live_agreement") or {}
        print(f"  {sym}: Live=Final hold {la.get('holdout_direction_pct')} | v2 hold roh {v2.get('holdout_direction_pct')} "
              f"bal {v2.get('holdout_balanced_pct')} base {v2.get('holdout_baseline_pct')} skill {v2.get('holdout_skill_pct')} "
              f"lag {v2.get('mean_lag_days')} missed {v2.get('missed_pct')} | Ø Phase v1-Ref "
              f"{round(len(c) / 24 / max(1, rt._switches(rt._trend_arr(ideal1, mode)) + 1), 1)} d, v2-Ref "
              f"{round(len(c) / 24 / max(1, (v2.get('switches_truth') or 0) + 1), 1)} d, live switches {v2.get('switches_live')}")

asyncio.run(main())
