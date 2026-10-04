"""Live-Check Regime-Brücke (nur lesend): echte Lab-Analyse + echte 1h-Kerzen -> Struktur-Kontext."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))


async def main():
    from motor.motor_asyncio import AsyncIOMotorClient
    import aiohttp
    from services import regime_lab as lab, regime_release as rr, structural_regime as sr
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles

    db = AsyncIOMotorClient(os.environ["MONGO_URL"])[os.environ["DB_NAME"]]
    docs = await db.regime_analyses.find({"timeframe": "1h"}, {"chart": 0, "chart_emas": 0}).to_list(10)
    doc = next((d for d in docs if lab.model_for(d, "combined")), None)
    assert doc, "keine 1h-Analyse mit Modell"
    print("Analyse:", doc["id"], doc.get("name"), doc.get("symbols"), "scope:", doc.get("scope"))
    model = lab.model_for(doc, "combined")
    sym = doc["symbols"][0]
    async with aiohttp.ClientSession() as s:
        raw = await fetch_history(s, sym, sr.DETECT_DAYS)
    candles = aggregate_candles(raw, "1h", drop_partial=True)
    print("Kerzen 1h:", len(candles), "Lücken ok:", sr.candle_gap_ok(candles, 3600))
    kept = rr.kept_regime_ids(doc, "combined")
    ctx = sr.context_from_model(model, candles, "1h", doc["id"], "shadow", kept, sym)
    for k in ("state", "direction", "phase", "label", "confidence", "since_days", "kept", "market_closed", "model_fingerprint"):
        print(f"  {k}: {ctx.get(k)}")
    print("Prompt-Zeile:", sr.prompt_line(ctx))
    assert not any(w in sr.prompt_line(ctx) for w in sr.KURZFRIST_WORDS)
    print("Artefakt:", sr.artifact_of({"stage": "shadow", "model_fingerprint": ctx["model_fingerprint"]}, doc["id"]))
    ok, reasons, ev = rr.validate_release(doc, "shadow", {}, await rr.jobs_for(db, doc))
    print("Nachweis-Gate shadow:", ok, reasons)
    print("Frische Gold (Wochenende?):", sr.freshness_ttl_sec("GOLD", "1h"))
    print("Frische BTC:", sr.freshness_ttl_sec("BTCUSDT", "1h"))
    print("LIVE-CHECK OK")


asyncio.run(main())
