"""CPI-/NFP-Event-Backtest: vergangene Datenveröffentlichungen (~2 Jahre) auf
5m-Bitunix-Kerzen nachhandeln – exakt das Muster von services/fomc_backtest.py.

OVERFITTING-SCHUTZ (identisch zum FOMC-Backtest):
  * FIXED enthält ALLE Parameter je Event – KEINE Optimierung auf den Events.
  * Chronologischer Split: letzte OOS_SHARE der Events sind Out-of-Sample;
    Validierung verlangt positives PnL im Gesamt UND im OOS-Teil.
  * Wilson-Konfidenzintervall der Winrate wird mit ausgewiesen.

Die Simulation (Whipsaw-Fade + Drift) wird 1:1 aus fomc_backtest wiederverwendet
(simulate_event/aggregate/validated sind rein) – nur die Fenster sind an
Datenveröffentlichungen angepasst (Reaktion schneller als beim Zinsentscheid,
keine Pressekonferenz): Fade in den ersten 20 min, Drift ab min 15.
"""
import asyncio
import logging
from datetime import datetime, timezone

from services import econ_event, fomc_backtest
from services.bitunix_client import fetch_klines_range

logger = logging.getLogger(__name__)

SYMBOLS = list(fomc_backtest.SYMBOLS)
ASSET_CLASS = "crypto"          # Bitunix liefert nur Krypto-Historie

# ALLE Parameter fest je Event – ex-ante definiert, keine Optimierung!
_DATA_RELEASE_FIXED: dict[str, float] = {
    "pre_range_hours": 3.0,
    "fade_window_min": 20, "fade_spike_mult": 0.35, "fade_sl_buffer_mult": 0.10,
    "drift_start_min": 15, "drift_window_min": 90, "drift_break_mult": 0.25,
    "drift_tp_mult": 1.0,
    "timeout_fade_min": 120, "timeout_drift_min": 150,
    "min_range_pct": 0.15,
}
FIXED: dict[str, dict[str, float]] = {
    "cpi": dict(_DATA_RELEASE_FIXED),
    "nfp": dict(_DATA_RELEASE_FIXED),
    "ppi": dict(_DATA_RELEASE_FIXED),
    "pce": dict(_DATA_RELEASE_FIXED),
}

_running: dict[str, bool] = {key: False for key in econ_event.EVENTS}


def result_id(key: str) -> str:
    return f"{key}_backtest_result"


async def _event_candles(symbol: str, t0_ms: int, p: dict) -> list[dict]:
    start = t0_ms - int(p["pre_range_hours"] * 3600 * 1000) - 600_000
    end = t0_ms + int(p["timeout_drift_min"] + 30) * 60_000
    return await fetch_klines_range(symbol, "5m", start_ms=start, end_ms=end, limit=200)


async def run(db, key: str, years: float = 2.0,
              symbols: list[str] | None = None,
              params: dict | None = None) -> dict:
    ev = econ_event.get(key)
    if not ev:
        return {"status": "error", "detail": f"Unbekanntes Event '{key}'"}
    if _running.get(key):
        return {"status": "busy", "detail": f"{ev.label}-Backtest läuft bereits"}
    _running[key] = True
    try:
        symbols = symbols or SYMBOLS
        p = {**FIXED[key], **(params or {})}
        releases = ev.past_releases(years=years)
        all_trades: list[dict] = []
        per_event: dict[str, dict] = {}
        for dt in releases:
            evd = dt.strftime("%Y-%m-%d")
            t0 = int(dt.timestamp())
            ev_trades = []
            for sym in symbols:
                candles = await _event_candles(sym, t0 * 1000, p)
                if len(candles) < 20:
                    continue
                for t in fomc_backtest.simulate_event(candles, t0, p=p):
                    t.update({"event": evd, "symbol": sym})
                    ev_trades.append(t)
                await asyncio.sleep(0.15)   # Bitunix-Rate schonen
            all_trades += ev_trades
            per_event[evd] = {"trades": len(ev_trades),
                              "pnl": round(sum(t["pnl"] for t in ev_trades), 2)}
        events_sorted = [dt.strftime("%Y-%m-%d") for dt in releases]
        agg = fomc_backtest.aggregate(all_trades, events_sorted)
        ok, why = fomc_backtest.validated(agg)
        result = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "event": key, "label": ev.label,
            "years": years, "symbols": symbols, "asset_class": ASSET_CLASS,
            "params_fixed": p, "ai_params": bool(params),
            "aggregate": agg, "per_event": per_event,
            "trades": sorted(all_trades, key=lambda t: t["entry_ts"]),
            "validated": ok, "validation_reason": why,
            "note": ("Feste Regeln ohne Parameter-Optimierung; Validierung verlangt "
                     "positives Gesamt- UND Out-of-Sample-PnL (Overfitting-Schutz)."),
        }
        await db.settings.update_one({"_id": result_id(key)}, {"$set": result}, upsert=True)
        await ev.set_validation(
            db, ASSET_CLASS, ok, why,
            {"trades": agg["total"]["trades"], "pnl": agg["total"]["pnl"],
             "oos_pnl": agg["out_of_sample"]["pnl"]})
        logger.info(f"{ev.label}-Backtest: {len(all_trades)} Trades, validiert={ok} ({why})")
        return {"status": "ok", **result}
    finally:
        _running[key] = False


async def last_result(db, key: str) -> dict | None:
    doc = await db.settings.find_one({"_id": result_id(key)})
    if doc:
        doc.pop("_id", None)
    return doc


def is_running(key: str) -> bool:
    return bool(_running.get(key))
