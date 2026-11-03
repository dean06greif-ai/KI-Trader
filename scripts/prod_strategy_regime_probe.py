"""Read-only Probe: statische & dynamische Strategien + Regime-Erkennung (nur find/aggregate).

Aufruf: PROD_MONGO_URL=... python scripts/prod_strategy_regime_probe.py
"""
import asyncio
import os
from collections import defaultdict
from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorClient


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def agg(rows):
    n = len(rows)
    if not n:
        return "n=0"
    pnl = sum(f(t.get("realized_pnl")) for t in rows)
    fee = sum(f(t.get("fees_paid")) for t in rows)
    rs = [t for t in rows if f(t.get("risk_usdt")) > 0]
    r = (sum(f(t["realized_pnl"]) for t in rs) / sum(f(t["risk_usdt"]) for t in rs)) if rs else None
    gr = (sum(f(t["realized_pnl"]) + f(t.get("fees_paid")) for t in rs) / sum(f(t["risk_usdt"]) for t in rs)) if rs else None
    wr = sum(1 for t in rows if f(t.get("realized_pnl")) > 0) / n * 100
    rtxt = f"net={r:+.3f}R gross={gr:+.3f}R" if r is not None else "R=–"
    return f"n={n:4d} {rtxt} WR={wr:4.1f}% PnL={pnl:+9.1f} fees={fee:7.1f}"


def group(title, rows, keyf, min_n=5, limit=25):
    acc = defaultdict(list)
    for t in rows:
        k = keyf(t)
        if k is not None:
            acc[str(k)].append(t)
    print(f"\n--- {title} ---")
    for k, v in sorted(acc.items(), key=lambda kv: -len(kv[1]))[:limit]:
        if len(v) >= min_n:
            print(f"  {k[:44]:44s} {agg(v)}")


async def main():
    db = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"], serverSelectionTimeoutMS=20000)["crypto_scanner"]
    rows = await db.auto_trades.find({"status": "closed", "strategy_id": {"$ne": "ai_trader"}},
                                     {"_id": 0, "events": 0, "paper_exec": 0, "entry_checks": 0}).to_list(20000)
    print("Nicht-KI-Trades:", len(rows))
    group("Strategie", rows, lambda t: f"{t.get('strategy_id')} | {str(t.get('strategy_name'))[:24]}", 3)
    group("Strategie × Modus", rows, lambda t: f"{t.get('strategy_id')} {t.get('mode')}", 3)
    group("Dynamisch: Regime beim Einstieg", [t for t in rows if t.get("dynamic")],
          lambda t: (t.get("dynamic") or {}).get("regime") or (t.get("dynamic") or {}).get("label"), 3)
    group("Dynamisch: Sub-Strategie", [t for t in rows if t.get("dynamic")],
          lambda t: (t.get("dynamic") or {}).get("sub_strategy_name"), 3)
    group("Timeframe", rows, lambda t: t.get("timeframe"), 3)
    group("Seite", rows, lambda t: t.get("side"), 3)
    group("Symbol", rows, lambda t: t.get("symbol"), 8, 15)
    group("Woche", rows, lambda t: datetime.fromisoformat(str(t["opened_at"]).replace("Z", "+00:00")).strftime("%G-W%V")
          if t.get("opened_at") else None, 3)
    group("Kurzfrist-Regime beim Einstieg", rows,
          lambda t: ((t.get("entry_market_snapshot") or {}).get("features") or {}).get("regime"), 5)
    # Dynamische Strategien + Wechsel
    dyn = await db.dynamic_strategies.find({}, {"_id": 0}).to_list(50)
    print("\n--- dynamic_strategies ---")
    for d in dyn:
        phases = d.get("phases") or d.get("regimes") or []
        print(f"  {d.get('id')} {str(d.get('name'))[:40]} tf={d.get('timeframe')} active={d.get('active') or d.get('enabled')} "
              f"phases={len(phases)} keys={sorted(d.keys())[:18]}")
        for p in phases[:8]:
            print(f"     · {p.get('regime') or p.get('label')}: {str(p.get('strategy_name') or p.get('strategy_id'))[:30]} traded={p.get('traded')}")
    sw = await db.dynamic_switch_log.count_documents({})
    last = await db.dynamic_switch_log.find({}, {"_id": 0}).sort([("ts", -1)]).to_list(400)
    print(f"\ndynamic_switch_log: {sw} Einträge; letzte 400 nach (von→nach):")
    pairs = defaultdict(int)
    for x in last:
        pairs[f"{x.get('from') or x.get('from_regime')}→{x.get('to') or x.get('to_regime')}"] += 1
    for k, n in sorted(pairs.items(), key=lambda kv: -kv[1])[:12]:
        print(f"  {k}: {n}")
    if last:
        print("  Beispiel:", {k: last[0][k] for k in list(last[0])[:12]})
    # Regime-Engine-Snapshots: Wechselhäufigkeit je Symbol
    snaps = await db.regime_engine_snapshots.find({}, {"_id": 0}).sort([("ts", -1)]).to_list(3000)
    print(f"\nregime_engine_snapshots (letzte {len(snaps)}):")
    if snaps:
        print("  Felder:", sorted(snaps[0].keys())[:25])
        by_sym = defaultdict(list)
        for s in snaps:
            by_sym[s.get("symbol")].append(s)
        for sym, xs in list(by_sym.items())[:8]:
            xs.sort(key=lambda s: str(s.get("ts")))
            labs = [s.get("regime") or s.get("label") or (s.get("state") or {}).get("regime") for s in xs]
            flips = sum(1 for a, b in zip(labs, labs[1:]) if a != b)
            span_h = None
            try:
                a = datetime.fromisoformat(str(xs[0]["ts"]).replace("Z", "+00:00"))
                b = datetime.fromisoformat(str(xs[-1]["ts"]).replace("Z", "+00:00"))
                span_h = (b - a).total_seconds() / 3600
            except Exception:
                pass
            dist = defaultdict(int)
            for lab in labs:
                dist[lab] += 1
            print(f"  {sym}: n={len(xs)} Wechsel={flips} über {span_h and round(span_h)}h · Verteilung={dict(dist)}")
    cal = await db.regime_calibrations.find({}, {"_id": 0}).sort([("ts", -1)]).to_list(3)
    print("\nregime_calibrations (letzte 3):")
    for c in cal:
        print("  ", {k: (str(v)[:120]) for k, v in list(c.items())[:10]})
    sh = await db.structural_regime_history.find({}, {"_id": 0}).sort([("ts", -1)]).to_list(1)
    print("\nstructural_regime_history Beispiel:", {k: str(v)[:100] for k, v in (sh[0] if sh else {}).items()})
    cs = await db.custom_strategies.find({}, {"_id": 0, "id": 1, "name": 1, "timeframe": 1, "rules": 1,
                                              "indicators": 1, "enabled": 1}).to_list(50)
    print("\n--- custom_strategies ---")
    for c in cs:
        rules = c.get("rules") or []
        print(f"  {c.get('id')} {str(c.get('name'))[:40]} tf={c.get('timeframe')} rules={len(rules)} "
              f"{[str(r.get('type') or r.get('indicator') or r)[:28] for r in rules][:6]}")


asyncio.run(main())
