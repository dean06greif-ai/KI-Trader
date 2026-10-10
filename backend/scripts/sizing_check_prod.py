"""Read-only Prod-Analyse: Positionsgröße, Hebel, Fees vs. PnL des KI-Traders. NIE schreiben."""
import os, asyncio, json, statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient


async def main():
    c = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"], serverSelectionTimeoutMS=15000)
    db = c[os.environ["PROD_DB_NAME"]]

    print("## AI-Config (Größe/Hebel-relevant)")
    ai = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    cfg = ai.get("config") or ai
    for k in ("enabled", "mode", "lev_mode", "lev_auto_max", "lev_fixed", "max_capital_per_trade",
              "min_confidence", "swing_max_leverage", "use_ai_levels", "maker_mode",
              "max_open_trades", "max_same_direction", "interval_min", "collection_mode",
              "runner_enabled", "fee_guard_enabled", "fee_guard_max_fee_r", "max_daily_loss",
              "max_trades_per_day", "sweep_trigger_enabled", "symbols", "groups"):
        if k in cfg:
            v = cfg[k]
            print(f"  {k}: {json.dumps(v, ensure_ascii=False)[:200]}")
    print("  alle keys:", sorted(cfg.keys())[:400])

    print("\n## ML-Gate settings")
    mg = await db.settings.find_one({"_id": "capital_allocation"}) or {}
    print(json.dumps({k: v for k, v in mg.items() if k != "_id"}, default=str)[:800])
    for cid in ("ml_gate_settings", "ml_gate_model"):
        d = await db.settings.find_one({"_id": cid}) or {}
        print(cid, json.dumps({k: v for k, v in d.items() if k not in ("_id", "model_b64", "booster")}, default=str)[:600])

    print("\n## Capital Allocation")
    at = await db.settings.find_one({"_id": "autotrade_config"}) or {}
    print("  capital_allocation:", json.dumps(at.get("capital_allocation"), default=str))
    coins = at.get("coins") or {}
    keys = ("enabled", "mode", "max_capital", "leverage", "auto_leverage_enabled", "auto_lev_max",
            "auto_lev_value", "auto_lev_mode", "sl_mode", "tp1_crv", "tp_full_crv")
    print("  Coins:")
    for sym in sorted(coins):
        cc = coins[sym] or {}
        if cc.get("enabled") or cc.get("mode") == "live":
            print(f"    {sym:10s} {json.dumps({k: cc.get(k) for k in keys if k in cc}, ensure_ascii=False)}")
    scc = at.get("strategy_coin_configs") or {}
    print("  ai_trader coin overrides:")
    for k in sorted(scc):
        if k.startswith("ai_trader_"):
            cc = scc[k] or {}
            print(f"    {k:24s} {json.dumps({kk: cc.get(kk) for kk in keys if kk in cc}, ensure_ascii=False)}")

    print("\n## Trades KI-Trader letzte 30 Tage (geschlossen)")
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    cur = db.auto_trades.find({"strategy_id": "ai_trader", "status": {"$in": ["closed", "CLOSED"]},
                               "opened_at": {"$gte": since}},
                              {"mode": 1, "data_collection": 1, "max_capital": 1, "leverage": 1, "realized_pnl": 1, "closed_by": 1, "horizon": 1, "fees_paid": 1, "ai_size_reason": 1,
                               "pnl_usdt": 1, "fees": 1, "fee_usdt": 1, "fees_usdt": 1, "ai_capital_pct": 1,
                               "ml_risk_scale": 1, "symbol": 1, "ai_setup": 1, "setup": 1, "entry": 1,
                               "sl": 1, "stop_loss": 1, "qty": 1, "close_reason": 1, "result": 1,
                               "ai_confidence": 1, "confidence": 1, "ai_horizon": 1, "auto_leverage": 1,
                               "effective_leverage": 1, "timeframe": 1, "opened_at": 1, "closed_at": 1})
    rows = await cur.to_list(5000)
    print(f"  n={len(rows)}")
    if rows:
        print("  Beispiel-Felder:", sorted(rows[0].keys()))
    groups = defaultdict(list)
    for r in rows:
        key = ("live" if r.get("mode") == "live" else "paper") + ("/dc" if r.get("data_collection") else "")
        groups[key].append(r)

    def f(x):
        try:
            return float(x or 0)
        except (TypeError, ValueError):
            return 0.0

    for key, rs in groups.items():
        caps = [f(r.get("max_capital")) for r in rs]
        levs = [f(r.get("leverage")) for r in rs]
        pnls = [f(r.get("realized_pnl")) for r in rs]
        fees = [f(r.get("fees_paid")) for r in rs]
        pcts = [f(r.get("ai_capital_pct")) for r in rs if r.get("ai_capital_pct")]
        mls = [f(r.get("ml_risk_scale")) for r in rs if r.get("ml_risk_scale")]
        sld = [abs(f(r.get("entry")) - f(r.get("sl") or r.get("stop_loss"))) / f(r.get("entry")) * 100
               for r in rs if f(r.get("entry")) and (r.get("sl") or r.get("stop_loss"))]
        wins = sum(1 for p in pnls if p > 0)
        print(f"\n  [{key}] n={len(rs)} win={wins/len(rs)*100:.0f}% ΣPnL={sum(pnls):.2f} ΣFees={sum(fees):.2f}")
        print(f"     Marge: Ø{statistics.mean(caps):.1f} med={statistics.median(caps):.1f} min={min(caps):.1f} max={max(caps):.1f}")
        print(f"     Hebel: Ø{statistics.mean(levs):.1f} med={statistics.median(levs):.1f} min={min(levs):.1f} max={max(levs):.1f}")
        if pcts:
            print(f"     capital_pct: Ø{statistics.mean(pcts):.0f} Verteilung={Counter(int(p) for p in pcts).most_common(8)}")
        if mls:
            print(f"     ml_risk_scale gesetzt bei {len(mls)}/{len(rs)}: {Counter(mls).most_common(3)}")
        if sld:
            print(f"     SL-Abstand %: Ø{statistics.mean(sld):.3f} med={statistics.median(sld):.3f}")
        notional = [c * l for c, l in zip(caps, levs)]
        print(f"     Notional: Ø{statistics.mean(notional):.0f} med={statistics.median(notional):.0f}")
        print(f"     Ø PnL/Trade={statistics.mean(pnls):.3f}  Ø Fee/Trade={statistics.mean(fees):.3f}")
        setups = Counter((r.get("ai_setup") or r.get("setup") or "?") for r in rs)
        print(f"     Setups: {setups.most_common(10)}")
        hz = Counter(r.get("horizon") or "?" for r in rs)
        print(f"     Horizon: {hz.most_common()}")
        cr = Counter(r.get("closed_by") or "?" for r in rs)
        print(f"     Close: {cr.most_common(8)}")
        al = Counter(bool(r.get("auto_leverage")) for r in rs)
        print(f"     auto_leverage flag: {al}")
        # PnL per setup
        ps = defaultdict(list)
        for r, p in zip(rs, pnls):
            ps[r.get("ai_setup") or r.get("setup") or "?"].append(p)
        for s, ps_ in sorted(ps.items(), key=lambda kv: -len(kv[1]))[:10]:
            w = sum(1 for p in ps_ if p > 0)
            print(f"       {s:28s} n={len(ps_):3d} win={w/len(ps_)*100:3.0f}% Σ={sum(ps_):8.2f} Ø={statistics.mean(ps_):.3f}")

    print("\n## Offene KI-Trades")
    op = await db.auto_trades.find({"strategy_id": "ai_trader", "status": {"$in": ["open", "OPEN"]}},
                                   {"symbol": 1, "mode": 1, "data_collection": 1, "capital": 1, "leverage": 1,
                                    "ai_capital_pct": 1, "ml_risk_scale": 1}).to_list(200)
    for r in op:
        print("  ", {k: v for k, v in r.items() if k != "_id"})

    print("\n## Token-Kosten letzte 7 Tage (ai_token_usage)")
    since7 = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
    tu = await db.ai_token_usage.find({"ts": {"$gte": since7}}, {"role": 1, "model": 1, "provider": 1,
                                                                 "cost_usd": 1, "total_tokens": 1, "tokens": 1}).to_list(50000)
    agg = defaultdict(lambda: [0, 0, 0.0])
    for t in tu:
        k = (t.get("role"), t.get("provider"), t.get("model"))
        agg[k][0] += 1
        agg[k][1] += int(t.get("total_tokens") or t.get("tokens") or 0)
        agg[k][2] += float(t.get("cost_usd") or 0)
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1][0])[:15]:
        print(f"  {str(k):80s} calls={v[0]} tokens={v[1]} cost=${v[2]:.3f}")

    print("\n## Bitunix Live Balance (aus DB-Snapshot falls vorhanden)")
    for cid in ("live_balance", "capital_state", "balance_snapshot"):
        d = await db.settings.find_one({"_id": cid})
        if d:
            print(" ", cid, json.dumps({k: v for k, v in d.items() if k != "_id"}, default=str)[:400])
    d = await db.ai_equity.find_one(sort=[("ts", -1)])
    if d:
        print("  ai_equity latest:", json.dumps({k: v for k, v in d.items() if k != "_id"}, default=str)[:400])


asyncio.run(main())
