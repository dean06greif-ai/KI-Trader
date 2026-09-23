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

from services import econ_event, event_assets, fomc_backtest

logger = logging.getLogger(__name__)

SYMBOLS = list(fomc_backtest.SYMBOLS)   # Krypto-Default (Abwärtskompatibilität)
ASSET_CLASS = "crypto"

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

_running: dict[str, bool] = {}   # je (Event, Anlageklasse) ein Lauf


def result_id(key: str, asset_class: str = "crypto") -> str:
    return f"{key}_backtest_result{event_assets.result_suffix(asset_class)}"


async def _event_candles(symbol: str, t0_ms: int, p: dict,
                         asset_class: str = "crypto") -> list[dict]:
    start = t0_ms - int(p["pre_range_hours"] * 3600 * 1000) - 600_000
    end = t0_ms + int(p["timeout_drift_min"] + 30) * 60_000
    return await event_assets.fetch_event_candles(asset_class, symbol, start, end)


async def run(db, key: str, years: float = 2.0,
              symbols: list[str] | None = None,
              params: dict | None = None, asset_class: str = "crypto") -> dict:
    ev = econ_event.get(key)
    if not ev:
        return {"status": "error", "detail": f"Unbekanntes Event '{key}'"}
    cls = event_assets.normalize(asset_class)
    run_key = f"{key}:{cls}"
    if _running.get(run_key):
        return {"status": "busy",
                "detail": f"{ev.label}-Backtest ({event_assets.LABELS[cls]}) läuft bereits"}
    _running[run_key] = True
    try:
        symbols = symbols or event_assets.symbols_for(cls)
        p = {**FIXED[key], **event_assets.class_overrides(cls), **(params or {})}
        years = event_assets.years_cap(cls, years)
        releases = ev.past_releases(years=years)
        all_trades: list[dict] = []
        per_event: dict[str, dict] = {}
        data_events = 0
        for dt in releases:
            evd = dt.strftime("%Y-%m-%d")
            t0 = int(dt.timestamp())
            ev_trades = []
            ev_has_data = False
            for sym in symbols:
                candles = await _event_candles(sym, t0 * 1000, p, cls)
                if len(candles) < 20:
                    continue
                ev_has_data = True
                for t in fomc_backtest.simulate_event(candles, t0, p=p):
                    t.update({"event": evd, "symbol": sym})
                    ev_trades.append(t)
                await asyncio.sleep(0.15)   # Daten-API-Rate schonen
            data_events += 1 if ev_has_data else 0
            all_trades += ev_trades
            per_event[evd] = {"trades": len(ev_trades),
                              "pnl": round(sum(t["pnl"] for t in ev_trades), 2)}
        events_sorted = [dt.strftime("%Y-%m-%d") for dt in releases]
        agg = fomc_backtest.aggregate(all_trades, events_sorted)
        ok, why = fomc_backtest.validated(agg)
        if data_events == 0 and releases:
            why = ("keine Kursdaten für diese Anlageklasse erreichbar"
                   + (" (IBKR-Gateway eingeloggt?)" if cls == event_assets.FOREX else ""))
        result = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "event": key, "label": ev.label,
            "years": years, "symbols": symbols, "asset_class": cls,
            "data_note": event_assets.HIST_NOTE.get(cls, ""),
            "events_with_data": data_events,
            "params_fixed": p, "ai_params": bool(params),
            "aggregate": agg, "per_event": per_event,
            "trades": sorted(all_trades, key=lambda t: t["entry_ts"]),
            "validated": ok, "validation_reason": why,
            "note": ("Feste Regeln ohne Parameter-Optimierung; Validierung verlangt "
                     "positives Gesamt- UND Out-of-Sample-PnL (Overfitting-Schutz)."),
        }
        await db.settings.update_one({"_id": result_id(key, cls)},
                                     {"$set": result}, upsert=True)
        await ev.set_validation(
            db, cls, ok, why,
            {"trades": agg["total"]["trades"], "pnl": agg["total"]["pnl"],
             "oos_pnl": agg["out_of_sample"]["pnl"]})
        logger.info(f"{ev.label}-Backtest [{cls}]: {len(all_trades)} Trades, "
                    f"validiert={ok} ({why})")
        return {"status": "ok", **result}
    finally:
        _running[run_key] = False


async def last_result(db, key: str, asset_class: str = "crypto") -> dict | None:
    doc = await db.settings.find_one({"_id": result_id(key, asset_class)})
    if doc:
        doc.pop("_id", None)
    return doc


def is_running(key: str, asset_class: str | None = None) -> bool:
    if asset_class:
        return bool(_running.get(f"{key}:{event_assets.normalize(asset_class)}"))
    return any(v for k, v in _running.items() if k.startswith(f"{key}:"))
