"""Ergänzender Read-only-Check gegen Prod: Proposals, Slippage custom-Strategien,
Cerebras-Key-/Token-Detail, Konfidenz-Bucket 70-74, offene Trades Frische. NIE schreiben."""
import os, asyncio, json
from datetime import datetime, timezone, timedelta
from collections import Counter, defaultdict
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), "..", ".env"))
from motor.motor_asyncio import AsyncIOMotorClient

NOW = datetime.now(timezone.utc)
D7 = (NOW - timedelta(days=7)).isoformat()
D14 = (NOW - timedelta(days=14)).isoformat()


def sec(t):
    print(f"\n{'='*70}\n## {t}\n{'='*70}")


async def main():
    c = AsyncIOMotorClient(os.environ["PROD_MONGO_URL"], serverSelectionTimeoutMS=10000)
    db = c[os.environ["PROD_DB_NAME"]]

    sec("P1) OFFENE PROPOSALS (needs_confirmation) — komplett")
    props = await db.ai_proposals.find({"status": "needs_confirmation"}).sort("ts", -1).to_list(500)
    print(f"  needs_confirmation gesamt: {len(props)}")
    kinds = Counter()
    lev_syms = {}
    other = []
    for p in props:
        ch = p.get("changes") or {}
        keys = tuple(sorted(ch.keys()))
        kinds[str(keys)] += 1
        if set(ch.keys()) <= {"leverage", "lev", "max_leverage"} or ("leverage" in ch and len(ch) == 1):
            lev_syms[p.get("symbol")] = ch
        elif len(other) < 25:
            other.append((str(p.get("ts"))[:16], p.get("symbol"), json.dumps(ch, ensure_ascii=False)[:100]))
    print("  Nach Change-Keys:", dict(kinds.most_common(15)))
    print("  Reine Hebel-Vorschläge nach Symbol (letzter Stand):")
    for s, ch in sorted(lev_syms.items()):
        print(f"    {s}: {json.dumps(ch, ensure_ascii=False)}")
    print("  Andere Vorschläge (Beispiele):")
    for row in other:
        print("   ", row)

    sec("P2) SLIPPAGE je Strategie x Mode x order_kind (30d) + custom_23a30b65")
    cutoff = (NOW - timedelta(days=30)).isoformat()
    trades = await db.auto_trades.find(
        {"opened_at": {"$gte": cutoff}},
        {"strategy_id": 1, "mode": 1, "order_kind": 1, "slippage_pct": 1, "slippage_usdt": 1,
         "status": 1, "realized_pnl": 1, "fees_paid": 1, "symbol": 1, "opened_at": 1,
         "leverage": 1, "entry": 1, "signal_price": 1}).to_list(20000)
    grp = defaultdict(lambda: [0, 0.0, 0.0])
    for t in trades:
        if t.get("slippage_pct") is None:
            continue
        k = (str(t.get("strategy_id"))[:24], t.get("mode"), t.get("order_kind") or "market")
        grp[k][0] += 1
        grp[k][1] += float(t.get("slippage_pct") or 0)
        grp[k][2] += float(t.get("slippage_usdt") or 0)
    for k, (n, sp, su) in sorted(grp.items(), key=lambda x: -abs(x[1][2]))[:15]:
        print(f"    {k[0]:24s} {str(k[1]):6s} {k[2]:14s} n={n:4d} Ø slip={sp/max(1,n):+.3f}% Σ={su:+.2f} USDT")
    sec("P2b) custom_23a30b65 im Detail (7d)")
    ctr = [t for t in trades if str(t.get("strategy_id")) == "custom_23a30b65"
           and str(t.get("opened_at") or "") >= D7]
    print(f"  Trades 7d: {len(ctr)}, live: {sum(1 for t in ctr if t.get('mode')=='live')}")
    for t in sorted(ctr, key=lambda x: str(x.get("opened_at")))[-15:]:
        print(f"    {str(t.get('opened_at'))[:16]} {t.get('symbol'):10s} mode={t.get('mode')} "
              f"lev={t.get('leverage')} slip={t.get('slippage_pct')} pnl={t.get('realized_pnl')} "
              f"sig={t.get('signal_price')} entry={t.get('entry')}")
    scfg = await db.strategies.find_one({"id": "custom_23a30b65"}) or \
           await db.strategies.find_one({"_id": "custom_23a30b65"}) or {}
    print("  Strategie-Doc (Kurz):", json.dumps({k: scfg.get(k) for k in
          ("id", "name", "mode", "enabled", "leverage", "order_type", "coins")
          if k in scfg}, ensure_ascii=False)[:300])

    sec("P3) KONFIDENZ 70-74 im Detail (geschlossene KI-Trades)")
    allc = await db.auto_trades.find({"strategy_id": "ai_trader", "status": {"$ne": "open"}},
        {"realized_pnl": 1, "ai_confidence": 1, "data_collection": 1, "fees_paid": 1,
         "closed_at": 1, "setup": 1, "symbol": 1, "side": 1, "leverage": 1,
         "close_reason": 1, "exit_reason": 1, "mode": 1}).to_list(3000)
    b = [t for t in allc if t.get("ai_confidence") is not None and 70 <= int(t["ai_confidence"]) < 75]
    print(f"  n={len(b)}, Σ PnL={sum(float(t.get('realized_pnl') or 0) for t in b):+.1f}")
    bysym = defaultdict(lambda: [0, 0, 0.0])
    bysetup = defaultdict(lambda: [0, 0, 0.0])
    dc_split = defaultdict(lambda: [0, 0, 0.0])
    for t in b:
        pnl = float(t.get("realized_pnl") or 0)
        for d, key in ((bysym, t.get("symbol")), (bysetup, str(t.get("setup"))[:18]),
                       (dc_split, "collection" if t.get("data_collection") else "live-logik")):
            d[key][0] += 1; d[key][1] += 1 if pnl > 0 else 0; d[key][2] += pnl
    for name, d in (("Symbol", bysym), ("Setup", bysetup), ("DC", dc_split)):
        print(f"  Nach {name}:")
        for k, (n, w, p) in sorted(d.items(), key=lambda x: x[1][2])[:8]:
            print(f"    {str(k):20s} n={n:3d} win={100*w/max(1,n):3.0f}% ΣPnL={p:+7.1f}")

    sec("P4) TOKEN-USAGE Detail: Modelle je Rolle (7d) + Cerebras-Anteil")
    tok = await db.ai_token_usage.find().sort("date", -1).to_list(400)
    byrole = defaultdict(lambda: [0, 0])
    bymodel = defaultdict(lambda: [0, 0])
    dates = sorted({t.get("date") for t in tok}, reverse=True)[:7]
    for t in tok:
        if t.get("date") in dates:
            byrole[t.get("role")][0] += int(t.get("tokens") or 0)
            byrole[t.get("role")][1] += int(t.get("calls") or 0)
            bymodel[str(t.get("model"))[:50]][0] += int(t.get("tokens") or 0)
            bymodel[str(t.get("model"))[:50]][1] += int(t.get("calls") or 0)
    print("  7d nach Rolle:")
    for r, (tk, cl) in sorted(byrole.items(), key=lambda x: -x[1][0]):
        print(f"    {str(r):20s} {tk:>12,} tok {cl:>6} calls (Ø {tk/max(1,cl):,.0f}/call)")
    print("  7d nach Modell:")
    for m, (tk, cl) in sorted(bymodel.items(), key=lambda x: -x[1][0])[:12]:
        print(f"    {m:50s} {tk:>12,} tok {cl:>6} calls")

    sec("P5) OFFENE TRADES aller Strategien: Preis-Frische / Zombies")
    open_all = await db.auto_trades.find({"status": "open"},
        {"strategy_id": 1, "symbol": 1, "side": 1, "mode": 1, "opened_at": 1,
         "entry": 1, "leverage": 1, "data_collection": 1}).to_list(200)
    print(f"  Offene Trades gesamt: {len(open_all)}")
    for t in sorted(open_all, key=lambda x: str(x.get("opened_at"))):
        age_h = "?"
        try:
            dt = datetime.fromisoformat(str(t.get("opened_at")).replace("Z", "+00:00"))
            age_h = f"{(NOW - dt).total_seconds()/3600:.0f}h"
        except Exception:
            pass
        print(f"    {str(t.get('strategy_id'))[:22]:22s} {t.get('symbol'):10s} {t.get('side'):5s} "
              f"mode={t.get('mode'):5s} lev={t.get('leverage')} alter={age_h} "
              f"dc={1 if t.get('data_collection') else 0}")

    sec("P6) GATE-SHADOW-REPORT-Basis (28d)")
    cutoff28 = (NOW - timedelta(days=28)).isoformat()
    decs = await db.ai_decisions.find(
        {"gate_shadow.p_win": {"$exists": True}, "outcome": {"$in": ["win", "loss"]},
         "ts": {"$gte": cutoff28}},
        {"gate_shadow": 1, "outcome": 1, "symbol": 1}).to_list(20000)
    print(f"  Bewertete gate_shadow-Decisions 28d: {len(decs)}")
    if decs:
        wb = [d for d in decs if (d.get("gate_shadow") or {}).get("would_block")]
        wins = [d for d in decs if d["outcome"] == "win"]
        print(f"  would_block: {len(wb)} ({100*len(wb)/len(decs):.0f}%), reale Winrate: "
              f"{100*len(wins)/len(decs):.0f}%")
        blocked_losers = sum(1 for d in wb if d["outcome"] == "loss")
        blocked_winners = len(wb) - blocked_losers
        losses = len(decs) - len(wins)
        print(f"  Verlierer geblockt: {blocked_losers}/{losses} ({100*blocked_losers/max(1,losses):.0f}%), "
              f"Gewinner geblockt: {blocked_winners}/{max(1,len(wins))} ({100*blocked_winners/max(1,len(wins)):.0f}%)")

    sec("P7) use_heatmap/liquidation Verlauf: wer hat es geflippt? (settings + Proposals)")
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}) or {}
    print("  aktuell: use_heatmap_data =", cfg.get("use_heatmap_data"),
          "| use_liquidation_data =", cfg.get("use_liquidation_data"))
    flips = await db.ai_proposals.find(
        {"$or": [{"changes.use_heatmap_data": {"$exists": True}},
                 {"changes.use_liquidation_data": {"$exists": True}}]}).sort("ts", -1).to_list(20)
    for p in flips:
        print(f"    {str(p.get('ts'))[:16]} status={p.get('status')} "
              f"{json.dumps(p.get('changes'), ensure_ascii=False)[:100]}")
    audit = await db.audit_log.find().sort("ts", -1).to_list(15)
    print("  Audit-Log (letzte 15):")
    for a in audit:
        print(f"    {str(a.get('ts'))[:16]} {a.get('action')} user={a.get('user')} "
              f"{json.dumps({k: a.get(k) for k in ('scope', 'count', 'range') if a.get(k)}, ensure_ascii=False)[:80]}")

    c.close()

asyncio.run(main())
