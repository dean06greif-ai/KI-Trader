"""Nur LESEND: Überblick über die gespeicherten Regime-Forschungsergebnisse (Atlas)."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
from motor.motor_asyncio import AsyncIOMotorClient  # noqa: E402


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    names = await db.list_collection_names()
    print("Collections (regime/research):", sorted(n for n in names if "regime" in n or "research" in n
                                                    or "calib" in n or "dynamic" in n))
    n = await db.regime_analyses.count_documents({})
    print(f"\nregime_analyses: {n}")
    cur = db.regime_analyses.find({}, {"chart": 0, "chart_emas": 0}).sort("created_at", -1)
    async for d in cur:
        st = d.get("settings") or {}
        ec = st.get("engine_config") or {}
        comb = d.get("combined") or {}
        model = comb.get("model") or {}
        cfg = model.get("config") or {}
        val = comb.get("validation") or {}
        q = None
        try:
            from services import regime_quality
            q = regime_quality.summarize(d)
        except Exception as e:  # noqa: BLE001
            q = {"err": str(e)[:80]}
        rel = d.get("release") or {}
        print("-" * 100)
        print(f"{d.get('id')} | {d.get('name')} | {d.get('timeframe')} {d.get('days')}d | scope={d.get('scope')} "
              f"| syms={d.get('symbols')} | created={str(d.get('created_at'))[:19]}")
        print(f"  engine={st.get('engine')} detector={cfg.get('detector')} mode={cfg.get('regime_mode')} "
              f"train_pct={st.get('train_pct')} adapt={cfg.get('adapt_applied')} total_days={cfg.get('total_days')} "
              f"warmup_bars={cfg.get('warmup_bars')} horizons={cfg.get('horizons_days')} "
              f"min_hold_days={cfg.get('min_hold_days')} stall_bars={cfg.get('stall_bars')}")
        print(f"  kept={d.get('kept')} assignments={list((d.get('assignments') or {}).keys())[:3]} "
              f"wf={list((d.get('walkforward') or {}).keys())[:3]} release={rel.get('stage')} "
              f"calibrations={len(d.get('calibrations') or [])} ablation={'ablation' in d}")
        for sym, ps in (comb.get("per_symbol") or {}).items():
            la = ps.get("live_agreement") or {}
            v = ps.get("validation") or {}
            print(f"    {sym}: live=final {la.get('direction_pct')}% holdout {la.get('holdout_direction_pct')}% "
                  f"(n={la.get('holdout_bars')}) inner {la.get('inner_direction_pct')}% trend_hit {la.get('trend_hit_pct')}% "
                  f"| viol {v.get('violation_bars_pct')}% segdays {v.get('avg_segment_days')} "
                  f"| segments={len(ps.get('segments') or [])} live_segments={len(ps.get('live_segments') or [])}")
        for sym, pc in (d.get("per_coin") or {}).items():
            la = pc.get("live_agreement") or {}
            print(f"    [per_coin] {sym}: live=final {la.get('direction_pct')}% holdout {la.get('holdout_direction_pct')}% "
                  f"det={((pc.get('model') or {}).get('config') or {}).get('detector')}")
        if q and "err" not in q:
            print("  quality:", json.dumps(q, ensure_ascii=False)[:400])
    for coll in ("regime_calibrations", "regime_lab_runs", "regime_releases", "dynamic_strategies",
                 "regime_cockpit_history", "regime_structural_history"):
        if coll in names:
            c = await db[coll].count_documents({})
            print(f"\n{coll}: {c}")
            async for d in db[coll].find({}, {"_id": 0}).sort("created_at", -1).limit(3):
                s = json.dumps(d, default=str, ensure_ascii=False)
                print("  ", s[:600])
    s = await db.settings.find_one({"_id": "structural_regime_cache"})
    print("\nstructural_regime_cache:", json.dumps(s, default=str)[:800] if s else None)


asyncio.run(main())
