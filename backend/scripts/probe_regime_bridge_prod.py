"""Read-only Prod-Befund: Regime-Lab <-> Dynamische Strategien <-> KI-Trader (Struktur-Brücke)."""
import asyncio
import os
import sys
from collections import Counter

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")
from motor.motor_asyncio import AsyncIOMotorClient


def short(d, keys):
    return {k: d.get(k) for k in keys if k in d}


async def main():
    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]

    print("=== REGIME-ANALYSEN (Lab) ===")
    rows = await db.regime_analyses.find({}, {"chart": 0, "chart_emas": 0, "combined.per_symbol": 0, "per_coin": 0}).sort("created_at", -1).to_list(50)
    for r in rows:
        rel = r.get("release") or {}
        det = ((r.get("settings") or {}).get("engine_config") or {}).get("detector") or (r.get("settings") or {}).get("engine")
        print(f"- {r['id']} | {r.get('name')} | tf={r.get('timeframe')} scope={r.get('scope')} syms={len(r.get('symbols') or [])} "
              f"days={r.get('days')} | detector={det} | release={rel.get('stage')} | kept={(r.get('kept_regimes') or r.get('kept'))} "
              f"| created={str(r.get('created_at'))[:16]}")
        val = ((r.get("combined") or {}).get("validation") or {})
        if val:
            print(f"    validation: passed={val.get('passed')} viol={val.get('violation_bars_pct')} dir_acc={val.get('direction_accuracy_pct')} avg_seg={val.get('avg_segment_days')}")
        ds = r.get("dataset") or {}
        if ds:
            print(f"    dataset: {short(ds, ['status','history_days','from','to'])}")

    print("\n=== REGIME-LAB JOBS (letzte 15) ===")
    jobs = await db.regime_lab_runs.find({}, {"result": 0}).sort("created_at", -1).to_list(15)
    for j in jobs:
        print(f"- {j.get('id')} kind={j.get('kind')} status={j.get('status')} aid={j.get('analysis_id')} at={str(j.get('created_at'))[:16]} msg={str(j.get('message') or j.get('error') or '')[:80]}")

    print("\n=== KALIBRIERUNGEN ===")
    cals = await db.regime_calibrations.find({}).sort("created_at", -1).to_list(20)
    for c in cals:
        rep = c.get("report") or {}
        print(f"- {c.get('id')} aid={c.get('analysis_id')} det={rep.get('detector')} improved={rep.get('improved')} "
              f"score {rep.get('baseline_score')}->{rep.get('best_score')} changes={rep.get('changes')} at={str(c.get('created_at'))[:16]}")

    print("\n=== FREIGABEN (regime_release history) ===")
    async for r in db.regime_release_history.find({}).sort("at", -1).limit(10):
        print("-", short(r, ["at", "aid", "asset_class", "from_stage", "to_stage", "actor", "reason"]))

    print("\n=== STRUKTUR-CACHE (structural_regime_cache) ===")
    sc = await db.settings.find_one({"_id": "structural_regime_cache"}) or {}
    print("updated_at:", sc.get("updated_at"))
    for s, ctx in (sc.get("symbols") or {}).items():
        print(f"  {s}: {short(ctx, ['state','stage','direction','phase','label','confidence','since_days','kept','aid'])}")

    print("\n=== DYNAMISCHE STRATEGIEN ===")
    dyns = await db.dynamic_strategies.find({}, {"model": 0, "history": 0}).to_list(50)
    for d in dyns:
        s = d.get("settings") or {}
        ls = d.get("last_state") or {}
        print(f"- {d['id']} | {d.get('name')} | strat={d.get('strategy_id')} tf={d.get('timeframe')} syms={d.get('symbols')} archived={d.get('archived')}")
        print(f"    settings: {short(s, ['analysis_id','engine','auto_check_enabled','auto_apply_enabled','require_confirmation','check_interval_minutes','check_days','confidence_min','min_hold_days','max_regimes','transition_mode'])}")
        print(f"    release_status={d.get('release_status')} app_status={short(d.get('application_status') or {}, ['status','at','error'])} last_applied={str(d.get('last_applied'))[:16]}")
        print(f"    last_state checked_at={str(ls.get('checked_at'))[:16]}")
        for sym, st in (ls.get("per_symbol") or {}).items():
            print(f"      {sym}: {short(st, ['regime','label','confidence','error','last_switch'])}")
        nlog = await db.dynamic_switch_log.count_documents({"dynamic_id": d["id"]})
        last = await db.dynamic_switch_log.find({"dynamic_id": d["id"]}).sort("at", -1).to_list(3)
        print(f"    switch_log={nlog} last={[short(x, ['at','symbol','from_label','to_label','confidence','auto_applied']) for x in last]}")

    print("\n=== KI-TRADER: Regime-Gate / Quelle (autotrade config) ===")
    cfg = await db.settings.find_one({"_id": "autotrade_config"}) or await db.autotrade_config.find_one({}) or {}
    for k in ("mode", "regime_filter_enabled", "regime_block_phases", "regime_gate_source", "structural_regime_autonomy"):
        if k in cfg:
            print(f"  {k}: {cfg[k]}")
    cc = cfg.get("coin_configs") or {}
    gate_on = {k: short(v, ["regime_filter_enabled", "regime_block_phases", "regime_gate_source"]) for k, v in cc.items() if isinstance(v, dict) and v.get("regime_filter_enabled")}
    print("  coin_configs mit Regime-Filter:", gate_on if gate_on else "keine")

    print("\n=== KI-ENTSCHEIDUNGEN (letzte 7 Tage): Regime-Felder ===")
    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    n = await db.ai_decisions.count_documents({"timestamp": {"$gte": since}})
    print("  decisions:", n)
    sample = await db.ai_decisions.find({"timestamp": {"$gte": since}}, {"market_snapshot": 1, "entry_market_snapshot": 1, "structural": 1, "regime": 1, "symbol": 1, "action": 1, "prompt_blocks": 1}).sort("timestamp", -1).to_list(300)
    keys = Counter()
    struct_states = Counter()
    for d in sample:
        for k in d.keys():
            keys[k] += 1
        ms = d.get("market_snapshot") or d.get("entry_market_snapshot") or {}
        if isinstance(ms, dict):
            st = ms.get("structural") or (ms.get(d.get("symbol")) or {}).get("structural") if isinstance(ms.get(d.get("symbol")), dict) else ms.get("structural")
            if st:
                struct_states[(st.get("state"), st.get("stage"), st.get("direction"))] += 1
    print("  felder:", dict(keys))
    print("  structural in snapshots:", dict(struct_states) or "keine")
    if sample:
        ex = sample[0]
        ms = ex.get("market_snapshot") or ex.get("entry_market_snapshot")
        print("  beispiel snapshot keys:", list(ms.keys())[:15] if isinstance(ms, dict) else type(ms))

    print("\n=== REWARDS je Struktur-Regime ===")
    rw = await db.ai_rewards.find({}, {"structural_regime": 1, "structural": 1, "regime": 1, "reward": 1, "pnl": 1, "market_regime": 1}).sort("timestamp", -1).to_list(500)
    c = Counter()
    for r in rw:
        c[str(r.get("structural_regime") or (r.get("structural") or {}).get("direction") if isinstance(r.get("structural"), dict) else r.get("structural_regime"))] += 1
    print("  ", dict(c), "von", len(rw))

asyncio.run(main())
