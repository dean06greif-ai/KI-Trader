"""Read-only: KI-Modelle je Rolle/Aufgabe + Live-Einstellungen (Prod-DB, nur find).

Aufruf: python scripts/prod_ki_model_probe.py [tage]
"""
import asyncio
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")
DAYS = int(sys.argv[1]) if len(sys.argv) > 1 else 60


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def r_of(t):
    risk = f(t.get("risk_usdt")) or f(t.get("initial_risk_usdt"))
    if not risk:
        e, sl, q = f(t.get("entry")), f(t.get("initial_sl")) or f(t.get("sl")), f(t.get("qty"))
        risk = abs(e - sl) * q
    return f(t.get("realized_pnl")) / risk if risk else None


async def main():
    import re
    raw = os.environ["PROD_MONGO_URL"]
    url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    m = re.search(r"DB_NAME\s*[=:]\s*[\"']?([A-Za-z0-9_\-]+)", raw)
    name = os.environ.get("PROD_DB_NAME") or (m.group(1) if m else None) \
        or url.rsplit("/", 1)[-1].split("?")[0]
    db = AsyncIOMotorClient(url)[name]
    since = (datetime.now(timezone.utc) - timedelta(days=DAYS)).isoformat()

    print("=== Live-Einstellungen ===")
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    keys = ("enabled", "mode", "live_enabled", "paper_only", "model", "provider", "ai_role",
            "signal_broker_enabled", "signal_review_enabled", "signal_review_daily_cap",
            "signal_min_ki_trades", "signal_rule_paper_all", "structure_stop_enabled", "structure_tf",
            "structure_min_pct", "structure_max_pct", "maker_mode", "maker_policy",
            "risk_per_trade_pct", "max_open_trades", "max_leverage", "interval_min", "schedule",
            "edge_filter_enabled", "edge_filter_live_bypass", "edge_filter_fee_factor",
            "edge_filter_atr_factor", "min_confidence", "daily_loss_limit_pct", "live_trading")
    for k in sorted(cfg):
        if k in keys or k.startswith(("edge_filter", "live", "risk", "paper", "setup_trigger")):
            print(f"  {k} = {json.dumps(cfg[k], default=str)[:200]}")
    roles = await db.settings.find_one({"_id": "ai_roles_config"}) or {}
    print("\n=== Rollen (Prod) ===")
    for r, c in (roles.get("roles") or roles).items():
        if isinstance(c, dict) and ("model" in c or "provider" in c):
            print(f"  {r:17s} {c.get('provider')}/{c.get('model')} | fb1 {c.get('fallback_provider')}/{c.get('fallback_model')}"
                  f" | fb2 {c.get('fallback2_provider')}/{c.get('fallback2_model')} | user={c.get('user_configured')} on={c.get('enabled')}")

    print(f"\n=== Token-Nutzung je Rolle/Modell ({DAYS} T) ===")
    tok = defaultdict(lambda: [0, 0])
    async for d in db.ai_token_usage.find({"date": {"$gte": since[:10]}}):
        k = (d.get("role"), d.get("model"))
        tok[k][0] += int(d.get("calls") or 0)
        tok[k][1] += int(d.get("tokens") or 0)
    for (r, m), (c, t) in sorted(tok.items(), key=lambda x: -x[1][1])[:30]:
        print(f"  {str(r):17s} {str(m):45s} calls={c:6d} tokens={t:>11,d}")

    print(f"\n=== Entscheidungen je Modell ({DAYS} T) ===")
    sample = await db.ai_decisions.find_one({"created_at": {"$gte": since}}) or {}
    print("  Felder:", sorted(sample.keys())[:60])
    dec = defaultdict(lambda: Counter())
    async for d in db.ai_decisions.find({"created_at": {"$gte": since}},
                                        {"model": 1, "action": 1, "outcome": 1, "result": 1,
                                         "signal_source": 1, "status": 1}):
        m = d.get("model") or "?"
        dec[m]["n"] += 1
        dec[m][f"a:{d.get('action')}"] += 1
        dec[m][f"o:{d.get('outcome') or d.get('result') or d.get('status')}"] += 1
        dec[m][f"s:{d.get('signal_source')}"] += 1
    for m, c in sorted(dec.items(), key=lambda x: -x[1]["n"]):
        print(f"  {m:45s} {dict(c.most_common(9))}")

    print(f"\n=== KI-Trades je Modell (geschlossen, {DAYS} T) ===")
    tsample = await db.auto_trades.find_one({"strategy_id": "ai_trader", "status": "closed"}) or {}
    print("  Felder:", [k for k in sorted(tsample.keys()) if "model" in k or "ai" in k or "decision" in k or "source" in k])
    agg = defaultdict(lambda: [0, 0, 0.0, 0.0, 0])
    async for t in db.auto_trades.find({"strategy_id": "ai_trader", "status": "closed", "closed_at": {"$gte": since}}):
        m = t.get("ai_model") or t.get("model") or (t.get("ai_decision") or {}).get("model") or "?"
        w = "coll" if t.get("data_collection") else t.get("mode")
        src = t.get("signal_source") or "-"
        for key in (f"{w}|{m}", f"{w}|{m}|{src}"):
            a = agg[key]
            p = f(t.get("realized_pnl"))
            r = r_of(t)
            a[0] += 1
            a[1] += p > 0
            a[2] += p
            if r is not None:
                a[3] += r
                a[4] += 1
    for k in sorted(agg, key=lambda x: -agg[x][0]):
        n, w, p, rs, rn = agg[k]
        print(f"  {k:70s} n={n:4d} WR={100*w/n:5.1f}% PnL={p:9.2f} ØR={(rs/rn if rn else 0):+.2f}")

    print("\n=== Provider-/Fallback-Zustand ===")
    for sid in ("ai_provider_health", "ai_provider_stats", "model_watch", "ai_model_weights", "rate_limits"):
        d = await db.settings.find_one({"_id": sid})
        if d:
            print(f"  {sid}:", json.dumps({k: v for k, v in d.items() if k != "_id"}, default=str)[:900])
    print("\n=== Telegram / Edge ===")
    for sid in ("telegram_notify_config",):
        d = await db.settings.find_one({"_id": sid}) or {}
        print(f"  {sid}:", json.dumps({k: v for k, v in d.items() if k != "_id"}, default=str)[:600])
    print("  offene Live-Trades:", await db.auto_trades.count_documents({"status": "open", "mode": "live"}))
    print("  offene KI-Trades:", await db.auto_trades.count_documents({"status": "open", "strategy_id": "ai_trader"}))


asyncio.run(main())
