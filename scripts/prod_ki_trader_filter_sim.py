"""Read-only Kontrafaktische Filter-Simulation auf geschlossenen KI-Trades (nur find).

Aufruf: PROD_MONGO_URL=... python scripts/prod_ki_trader_filter_sim.py [since_iso]
Achtung: In-Sample – zeigt Richtungen, keine Garantie.
"""
import asyncio
import os
import sys
from collections import Counter

from motor.motor_asyncio import AsyncIOMotorClient


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def sl_pct(t):
    e = f(t.get("entry"))
    return abs(e - f(t.get("initial_sl"))) / e * 100 if e else 0


def bias(t):
    return ((t.get("entry_market_snapshot") or {}).get("features") or {}).get("daily_bias")


def with_bias(t):
    b = bias(t)
    return (b == "up" and t.get("side") == "LONG") or (b == "down" and t.get("side") == "SHORT")


def r_of(t):
    return f(t.get("realized_pnl")) / f(t.get("risk_usdt"))


def line(name, rows):
    rs = [t for t in rows if f(t.get("risk_usdt")) > 0]
    if not rs:
        print(f"  {name:55s} n=0")
        return
    risk = sum(f(t["risk_usdt"]) for t in rs)
    pnl = sum(f(t.get("realized_pnl")) for t in rs)
    fee = sum(f(t.get("fees_paid")) for t in rs)
    print(f"  {name:55s} n={len(rs):4d} net={pnl / risk:+.3f}R gross={(pnl + fee) / risk:+.3f}R "
          f"fee={fee / risk:.3f}R PnL={pnl:+8.1f}")


def refee(t, fee_mult):
    """Netto-R, wenn die Gebühren um fee_mult skaliert wären (z.B. Maker statt Taker)."""
    return (f(t.get("realized_pnl")) + f(t.get("fees_paid")) * (1 - fee_mult)) / f(t.get("risk_usdt"))


async def main():
    url = os.environ["PROD_MONGO_URL"]
    since = sys.argv[1] if len(sys.argv) > 1 else "2026-01-01"
    db = AsyncIOMotorClient(url, serverSelectionTimeoutMS=20000)["crypto_scanner"]
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": since}},
        {"_id": 0, "events": 0, "paper_exec": 0}).to_list(20000)
    rows = [t for t in rows if f(t.get("risk_usdt")) > 0]
    print("Trades", len(rows), "seit", since)
    print("Asset-Klassen:", Counter(str(t.get("asset_class") or t.get("symbol", "")[-4:]) for t in rows).most_common(8))
    print("Fee% Entry:", Counter(round(f(t.get("entry_fee_percent")), 3) for t in rows).most_common(6))
    bad = {"pullback", "trend_follow", "vwap_reclaim", "squeeze_breakout", "divergence"}
    crypto = [t for t in rows if str(t.get("symbol", "")).endswith("USDT")]
    print("\nFilter (in-sample):")
    line("alle", rows)
    line("nur Krypto", crypto)
    line("nicht Krypto (Rohstoffe/FX/Index)", [t for t in rows if t not in crypto])
    line("SL >= 0.7%", [t for t in rows if sl_pct(t) >= 0.7])
    line("SL >= 1.0%", [t for t in rows if sl_pct(t) >= 1.0])
    line("Gebühr <= 10% des Risikos", [t for t in rows if f(t.get("fees_paid")) / f(t["risk_usdt"]) <= 0.10])
    line("mit Tages-Bias", [t for t in rows if with_bias(t)])
    line("gegen/ohne Tages-Bias", [t for t in rows if not with_bias(t)])
    line("ohne Bias-Daten", [t for t in rows if bias(t) is None])
    line("ohne die 5 negativen Setups", [t for t in rows if t.get("setup") not in bad])
    line("mit Bias + SL>=0.7%", [t for t in rows if with_bias(t) and sl_pct(t) >= 0.7])
    line("mit Bias oder flat-Short, ohne neg. Setups",
         [t for t in rows if t.get("setup") not in bad and (with_bias(t))])
    # Halbe Stichprobe: Filter auf 1. Hälfte gefunden, auf 2. Hälfte prüfen
    rows.sort(key=lambda t: t["opened_at"])
    h = len(rows) // 2
    a, b = rows[:h], rows[h:]
    print("\nOut-of-sample-Check (1. Hälfte vs 2. Hälfte):")
    for name, fn in [("alle", lambda t: True), ("mit Bias", with_bias), ("SL>=0.7%", lambda t: sl_pct(t) >= 0.7),
                     ("Gebühr<=10%", lambda t: f(t.get("fees_paid")) / f(t["risk_usdt"]) <= 0.10),
                     ("mit Bias + SL>=0.7%", lambda t: with_bias(t) and sl_pct(t) >= 0.7)]:
        line(f"H1 {name}", [t for t in a if fn(t)])
        line(f"H2 {name}", [t for t in b if fn(t)])
    print("\nGebühren-Szenarien (gleiche Trades, nur Kosten anders):")
    for m, lbl in [(1.0, "wie gehabt (meist Taker)"), (0.5, "halbe Gebühren (Maker-Einstieg)"), (0.0, "ohne Gebühren")]:
        rs = [refee(t, m) for t in rows]
        print(f"  {lbl:40s} Ø {sum(rs) / len(rs):+.3f}R")
    # KI-Kosten
    tok = await db.ai_token_usage.find({}, {"_id": 0}).sort([("_id", -1)]).to_list(3)
    print("\nai_token_usage Beispiel:", tok[:2])
    cost = 0.0
    async for d in db.ai_token_usage.find({}, {"_id": 0}):
        cost += f(d.get("cost_usd") or d.get("cost"))
    print("ai_token_usage Summe cost:", round(cost, 2))


asyncio.run(main())
