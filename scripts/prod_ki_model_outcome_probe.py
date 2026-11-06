"""Read-only: KI-Trade-Ergebnis je Analyst-Modell (Zuordnung über das am
Eröffnungstag dominante Analyst-Modell aus ai_token_usage; Entscheidungen
speichern kein Modell). Aufruf: python scripts/prod_ki_model_outcome_probe.py
"""
import asyncio
import os
import re
from collections import defaultdict

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv("/app/backend/.env")


def f(v):
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


async def main():
    raw = os.environ["PROD_MONGO_URL"]
    url = re.search(r"mongodb(?:\+srv)?://[^\s\"']+", raw).group(0)
    db = AsyncIOMotorClient(url)["crypto_scanner"]
    day_model = {}
    by_day = defaultdict(dict)
    async for d in db.ai_token_usage.find({"role": "analyst"}, {"date": 1, "model": 1, "calls": 1}):
        by_day[d["date"]][d.get("model")] = by_day[d["date"]].get(d.get("model"), 0) + int(d.get("calls") or 0)
    for day, ms in by_day.items():
        day_model[day] = max(ms, key=ms.get)
    print("Tage mit Analyst-Modell:", len(day_model), "| Modelle:", sorted(set(day_model.values()), key=str))
    dec_model = {d["id"]: d.get("model") async for d in db.ai_decisions.find(
        {"model": {"$ne": None}}, {"_id": 0, "id": 1, "model": 1}) if d.get("id")}
    print("Entscheidungen mit Modell:", len(dec_model))
    agg = defaultdict(lambda: [0, 0, 0.0, 0.0, 0, 0.0])
    proj = {"opened_at": 1, "realized_pnl": 1, "risk_usdt": 1, "entry": 1, "initial_sl": 1, "sl": 1,
            "qty": 1, "mode": 1, "decision_id": 1, "data_collection": 1, "signal_source": 1, "fees_paid": 1}
    async for t in db.auto_trades.find({"strategy_id": "ai_trader", "status": "closed"}, proj):
        day = str(t.get("opened_at") or "")[:10]
        m = dec_model.get(t.get("decision_id")) or ("~" + str(day_model.get(day, "?")))
        risk = f(t.get("risk_usdt")) or abs(f(t.get("entry")) - (f(t.get("initial_sl")) or f(t.get("sl")))) * f(t.get("qty"))
        p = f(t.get("realized_pnl"))
        src = t.get("signal_source") or "-"
        for k in (m, f"{m} | {src}"):
            a = agg[k]
            a[0] += 1
            a[1] += p > 0
            a[2] += p
            a[5] += f(t.get("fees_paid"))
            if risk:
                a[3] += p / risk
                a[4] += 1
    for k in sorted(agg, key=lambda x: (x.split(" | ")[0], -agg[x][0])):
        n, w, p, rs, rn, fe = agg[k]
        print(f"{k:62s} n={n:5d} WR={100*w/n:5.1f}% PnL={p:10.2f} Fees={fe:8.2f} ØR={(rs/rn if rn else 0):+.2f}")


asyncio.run(main())
