"""Read-only Deep-Probe KI-Trader gegen Prod (nur find, keine Writes).

Aufruf: PROD_MONGO_URL=... python scripts/prod_ki_trader_deep_probe.py [since_iso]
"""
import asyncio
import os
import sys
from collections import defaultdict
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def hold(t):
    try:
        a = datetime.fromisoformat(str(t["opened_at"]).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(t["closed_at"]).replace("Z", "+00:00"))
        return (b - a).total_seconds() / 60
    except Exception:
        return None


def mfe_mae(t):
    e, sl0, side = f(t.get("entry")), f(t.get("initial_sl")) or f(t.get("sl")), t.get("side")
    risk = abs(e - sl0)
    if not e or not risk:
        return None, None
    pk, tr = f(t.get("peak_price")), f(t.get("trough_price"))
    if not pk or not tr:
        return None, None
    if side == "LONG":
        return (pk - e) / risk, (e - tr) / risk
    return (e - tr) / risk, (pk - e) / risk


def agg(rows):
    rs = [r for r in rows if f(r.get("risk_usdt")) > 0]
    n = len(rs)
    if not n:
        return f"n={len(rows):4d} (ohne risk)"
    risk = sum(f(r["risk_usdt"]) for r in rs)
    pnl = sum(f(r.get("realized_pnl")) for r in rs)
    fee = sum(f(r.get("fees_paid")) for r in rs)
    slip = sum(f(r.get("slippage_usdt")) for r in rs)
    wins = sum(1 for r in rs if f(r.get("realized_pnl")) > 0)
    tp1 = sum(1 for r in rs if r.get("tp1_hit"))
    return (f"n={n:4d} net={pnl / risk:+.3f}R gross={(pnl + fee) / risk:+.3f}R fee={fee / risk:.3f}R "
            f"slip={slip / risk:.3f}R WR={wins / n * 100:4.1f}% TP1={tp1 / n * 100:4.1f}% PnL={pnl:+9.1f}")


def group(title, rows, keyf, min_n=8, limit=40):
    acc = defaultdict(list)
    for r in rows:
        k = keyf(r)
        if k is not None:
            acc[str(k)].append(r)
    print(f"\n--- {title} ---")
    items = sorted(acc.items(), key=lambda kv: -len(kv[1]))[:limit]
    for k, v in items:
        if len(v) >= min_n:
            print(f"  {k[:34]:34s} {agg(v)}")


def bucket(v, edges):
    if v is None:
        return None
    for lo, hi in zip(edges, edges[1:]):
        if lo <= v < hi:
            return f"{lo}-{hi}"
    return f">={edges[-1]}"


async def main():
    url = os.environ["PROD_MONGO_URL"]
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
    db = AsyncIOMotorClient(url, serverSelectionTimeoutMS=20000)["crypto_scanner"]
    proj = {"_id": 0, "events": 0, "paper_exec": 0, "entry_market_snapshot.ts": 0}
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": since}}, proj).to_list(20000)
    ids = [r["decision_id"] for r in rows if r.get("decision_id")]
    dec = {}
    async for d in db.ai_decisions.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "source": 1, "model": 1,
                                                                "provider": 1}):
        dec[d["id"]] = d
    print("KI-Trades seit", since, "->", len(rows))
    print("GESAMT  ", agg(rows))
    group("Modus", rows, lambda t: f"{t.get('mode')}{' (Datensammlung)' if t.get('data_collection') else ''}", 1)
    group("Woche", rows, lambda t: datetime.fromisoformat(t["opened_at"].replace("Z", "+00:00")).strftime("%G-W%V"), 1)
    group("Setup", rows, lambda t: t.get("setup"))
    group("Quelle", rows, lambda t: (dec.get(t.get("decision_id")) or {}).get("source") or "?", 1)
    group("Signal-Quelle", rows, lambda t: t.get("signal_source"), 1)
    group("Modell", rows, lambda t: (dec.get(t.get("decision_id")) or {}).get("model"), 5)
    group("Seite", rows, lambda t: t.get("side"), 1)
    group("Timeframe", rows, lambda t: t.get("timeframe"), 1)
    group("Horizont", rows, lambda t: t.get("horizon"), 1)
    group("Order-Art", rows, lambda t: t.get("order_kind"), 1)
    group("Konfidenz", rows, lambda t: bucket(f(t.get("ai_confidence")), [0, 55, 65, 75, 85]), 1)
    group("Haltedauer min", rows, lambda t: bucket(hold(t), [0, 5, 15, 60, 240, 1440]), 1)
    group("Regime", rows, lambda t: ((t.get("entry_market_snapshot") or {}).get("features") or {}).get("regime"))
    group("Daily-Bias vs Seite", rows, lambda t: f"{((t.get('entry_market_snapshot') or {}).get('features') or {}).get('daily_bias')}/{t.get('side')}")
    group("Symbol (Top)", rows, lambda t: t.get("symbol"), 15, 30)
    group("Fee-Wächter erweitert", rows, lambda t: bool(t.get("fee_guard_widened")), 1)
    group("Stunde UTC", rows, lambda t: t["opened_at"][11:13], 15, 24)
    group("Ergebnis", rows, lambda t: t.get("result"), 1)
    group("SL-Abstand % vom Entry", rows,
          lambda t: bucket(abs(f(t.get("entry")) - f(t.get("initial_sl"))) / f(t.get("entry")) * 100 if f(t.get("entry")) else None,
                           [0, 0.2, 0.4, 0.7, 1.0, 2.0]), 1)
    group("Gebühr/Risiko", rows,
          lambda t: bucket(f(t.get("fees_paid")) / f(t.get("risk_usdt")) if f(t.get("risk_usdt")) else None,
                           [0, 0.05, 0.1, 0.2, 0.3, 0.5]), 1)
    # MFE/MAE: was die Trades unterwegs hätten erreichen können
    mf = [mfe_mae(t) for t in rows]
    mf = [(a, b, t) for (a, b), t in zip(mf, rows) if a is not None]
    if mf:
        print(f"\n--- MFE/MAE in R (n={len(mf)}) ---")
        for lvl in (0.5, 1.0, 1.5, 2.0, 3.0):
            print(f"  Anteil Trades mit MFE >= {lvl}R: {sum(1 for a, _, _ in mf if a >= lvl) / len(mf) * 100:4.1f}%")
        losers = [(a, b) for a, b, t in mf if f(t.get("realized_pnl")) < 0]
        if losers:
            print(f"  Verlierer: {len(losers)}, davon vorher MFE>=1R: {sum(1 for a, _ in losers if a >= 1) / len(losers) * 100:.1f}% "
                  f"MFE>=0.5R: {sum(1 for a, _ in losers if a >= .5) / len(losers) * 100:.1f}%")
        group("MFE-Bucket", [t for _, _, t in mf], lambda t: bucket(mfe_mae(t)[0], [0, 0.25, 0.5, 1, 1.5, 2, 3]), 1)
    group("TP1-CRV", rows, lambda t: t.get("tp1_crv"), 1)
    group("TP-Full-CRV", rows, lambda t: t.get("tp_full_crv"), 1)
    group("Breakeven verschoben", rows, lambda t: t.get("breakeven_moved"), 1)
    group("Leverage", rows, lambda t: bucket(f(t.get("leverage")), [0, 5, 10, 25, 50, 100, 200]), 1)
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    keys = ["enabled", "mode", "live_gate_bypass_enabled", "fee_guard_mult", "fee_guard_atr_mult", "maker_mode",
            "entry_mode", "min_confidence", "risk_per_trade_pct", "max_open_trades", "interval_sec", "scan_interval",
            "horizon", "allowed_setups", "data_collection_enabled"]
    print("\n--- ai_trader_config (Auszug) ---")
    for k in keys:
        if k in cfg:
            print(f"  {k}: {cfg[k]}")
    print("  alle keys:", sorted(k for k in cfg.keys())[:200])
    roles = await db.settings.find_one({"_id": "ai_roles_config"}) or {}
    print("\n--- ai_roles_config ---")
    for k, v in roles.items():
        if isinstance(v, dict):
            print(f"  {k}: model={v.get('model')} fallbacks={v.get('fallbacks') or v.get('fallback_models')}")


asyncio.run(main())
