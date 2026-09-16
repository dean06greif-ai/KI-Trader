"""FOMC-Event-Backtest: vergangene Zinsentscheide (letzte ~2 Jahre) auf
5m-Bitunix-Kerzen nachhandeln – feste, ex-ante definierte Regeln.

OVERFITTING-SCHUTZ (bewusst, vom Trader gefordert):
  * FIXED enthält ALLE Parameter – es findet KEINE Optimierung auf den (wenigen,
    ~16) Events statt. Ein Lauf testet genau EINE Regel-Menge.
  * Chronologischer Split: die letzten OOS_SHARE der Events sind Out-of-Sample.
    Validierung verlangt positives PnL im Gesamt UND im OOS-Teil.
  * Wilson-Konfidenzintervall der Winrate wird mit ausgewiesen (Ehrlichkeit
    bei kleiner Stichprobe, wie setup_lifecycle).

Regeln je Event & Symbol (max. 2 Trades -> mehrere Trades im Fenster):
  A) Whipsaw-FADE   T..T+30min: 5m-Extrem > Pre-Range ± 0.35×Range-Höhe, Kerze
     schließt zurück in der Range -> Gegenposition; SL hinter Spike-Extrem
     (+0.1×H Puffer), TP Range-Mitte; Timeout T+150min (Exit zum Close).
  B) DRIFT          T+30..T+120min: erster 5m-SCHLUSS jenseits Pre-Range um
     ≥ 0.25×H -> in Bewegungsrichtung; SL Range-Mitte, TP Entry ± 1.0×H;
     Timeout T+210min.
Pre-Range: 5m-Kerzen der 3h vor der Entscheidung.
"""
import asyncio
import logging
from datetime import datetime, timezone

from services import fomc_event
from services import setup_lifecycle as lifecycle
from services.bitunix_client import fetch_klines_range

logger = logging.getLogger(__name__)

RESULT_ID = "fomc_backtest_result"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
ASSET_CLASS = "crypto"          # Bitunix liefert nur Krypto-Historie
NOTIONAL = 300.0                # USDT Nominal je Trade (nur für PnL-Skalierung)
FEE_RT = 0.0012                 # Roundtrip Taker-Fees ~0.12 %

FIXED: dict[str, float] = {     # ALLE Parameter fest – keine Optimierung!
    "pre_range_hours": 3.0,
    "fade_window_min": 30, "fade_spike_mult": 0.35, "fade_sl_buffer_mult": 0.10,
    "drift_start_min": 30, "drift_window_min": 120, "drift_break_mult": 0.25,
    "drift_tp_mult": 1.0,
    "timeout_fade_min": 150, "timeout_drift_min": 210,
    "min_range_pct": 0.15,      # zu enge Pre-Range (< 0.15 % vom Preis) = kein Trade
}
# Validierung (fest): genug Trades, Gesamt- UND OOS-PnL positiv
MIN_TRADES = 10
MIN_OOS_TRADES = 4
OOS_SHARE = 1.0 / 3.0

_running = False


# ---------------------------------------------------------------------------
# Simulation (rein & testbar)
# ---------------------------------------------------------------------------
def _exit_trade(candles: list[dict], start_i: int, side: str, entry: float,
                sl: float, tp: float, timeout_ts: int) -> dict:
    """Konservativ: trifft eine Kerze SL und TP, zählt der SL zuerst."""
    d = 1 if side == "LONG" else -1
    for c in candles[start_i:]:
        if c["time"] > timeout_ts:
            break
        hit_sl = c["low"] <= sl if d == 1 else c["high"] >= sl
        hit_tp = c["high"] >= tp if d == 1 else c["low"] <= tp
        if hit_sl:
            return {"exit": sl, "result": "sl"}
        if hit_tp:
            return {"exit": tp, "result": "tp"}
    last = None
    for c in candles[start_i:]:
        if c["time"] > timeout_ts:
            break
        last = c
    return {"exit": (last or candles[min(start_i, len(candles) - 1)])["close"],
            "result": "timeout"}


def _pnl(side: str, entry: float, exit_px: float) -> float:
    d = 1 if side == "LONG" else -1
    return round(d * (exit_px - entry) / entry * NOTIONAL - NOTIONAL * FEE_RT, 2)


def simulate_event(candles: list[dict], t0: int, p: dict | None = None) -> list[dict]:
    """Trades eines Events auf 5m-Kerzen (time in Sekunden, aufsteigend)."""
    p = p or FIXED
    pre = [c for c in candles if t0 - p["pre_range_hours"] * 3600 <= c["time"] < t0]
    if len(pre) < 12:
        return []
    rh = max(c["high"] for c in pre)
    rl = min(c["low"] for c in pre)
    h = rh - rl
    mid = (rh + rl) / 2
    px = pre[-1]["close"]
    if h <= 0 or (h / px * 100) < p["min_range_pct"]:
        return []
    trades: list[dict] = []

    # A) Whipsaw-Fade
    fade_end = t0 + p["fade_window_min"] * 60
    for i, c in enumerate(candles):
        if not (t0 <= c["time"] < fade_end):
            continue
        if c["high"] > rh + p["fade_spike_mult"] * h and c["close"] < rh:
            side, entry = "SHORT", c["close"]
            sl = c["high"] + p["fade_sl_buffer_mult"] * h
            ex = _exit_trade(candles, i + 1, side, entry, sl, mid,
                             t0 + p["timeout_fade_min"] * 60)
        elif c["low"] < rl - p["fade_spike_mult"] * h and c["close"] > rl:
            side, entry = "LONG", c["close"]
            sl = c["low"] - p["fade_sl_buffer_mult"] * h
            ex = _exit_trade(candles, i + 1, side, entry, sl, mid,
                             t0 + p["timeout_fade_min"] * 60)
        else:
            continue
        trades.append({"rule": "fade", "side": side, "entry": entry, "sl": sl,
                       "tp": mid, "exit": ex["exit"], "result": ex["result"],
                       "entry_ts": c["time"], "pnl": _pnl(side, entry, ex["exit"])})
        break

    # B) Drift nach Pressekonferenz
    d_start = t0 + p["drift_start_min"] * 60
    d_end = t0 + (p["drift_start_min"] + p["drift_window_min"]) * 60
    for i, c in enumerate(candles):
        if not (d_start <= c["time"] < d_end):
            continue
        if c["close"] > rh + p["drift_break_mult"] * h:
            side, entry, sl = "LONG", c["close"], mid
            tp = entry + p["drift_tp_mult"] * h
        elif c["close"] < rl - p["drift_break_mult"] * h:
            side, entry, sl = "SHORT", c["close"], mid
            tp = entry - p["drift_tp_mult"] * h
        else:
            continue
        ex = _exit_trade(candles, i + 1, side, entry, sl, tp,
                         t0 + p["timeout_drift_min"] * 60)
        trades.append({"rule": "drift", "side": side, "entry": entry, "sl": sl,
                       "tp": tp, "exit": ex["exit"], "result": ex["result"],
                       "entry_ts": c["time"], "pnl": _pnl(side, entry, ex["exit"])})
        break
    return trades


def _bucket(trades: list[dict]) -> dict:
    n = len(trades)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    pnl = round(sum(t["pnl"] for t in trades), 2)
    lo, hi = lifecycle.wilson_interval(wins, n)
    return {"trades": n, "wins": wins, "winrate": round(wins / n * 100) if n else 0,
            "pnl": pnl, "wilson_wr_95": [lo, hi]}


def aggregate(all_trades: list[dict], events_sorted: list[str]) -> dict:
    """Gesamt/IS/OOS-Statistik; OOS = letzte OOS_SHARE der Events (chronologisch)."""
    n_oos = max(1, round(len(events_sorted) * OOS_SHARE))
    oos_events = set(events_sorted[-n_oos:])
    is_t = [t for t in all_trades if t["event"] not in oos_events]
    oos_t = [t for t in all_trades if t["event"] in oos_events]
    return {"total": _bucket(all_trades), "in_sample": _bucket(is_t),
            "out_of_sample": _bucket(oos_t), "oos_events": sorted(oos_events),
            "events_tested": len(events_sorted)}


def validated(agg: dict) -> (bool, str):
    tot, oos = agg["total"], agg["out_of_sample"]
    if tot["trades"] < MIN_TRADES:
        return False, f"erst {tot['trades']}/{MIN_TRADES} Backtest-Trades"
    if oos["trades"] < MIN_OOS_TRADES:
        return False, f"erst {oos['trades']}/{MIN_OOS_TRADES} Out-of-Sample-Trades"
    if tot["pnl"] <= 0:
        return False, f"Gesamt-PnL {tot['pnl']:+.2f} USDT nicht positiv"
    if oos["pnl"] <= 0:
        return False, (f"Out-of-Sample-PnL {oos['pnl']:+.2f} USDT nicht positiv "
                       "(Overfitting-Schutz)")
    return True, (f"{tot['trades']}T, WR {tot['winrate']}% "
                  f"(95% CI {tot['wilson_wr_95'][0]}-{tot['wilson_wr_95'][1]}%), "
                  f"PnL {tot['pnl']:+.2f} · OOS {oos['trades']}T {oos['pnl']:+.2f}")


# ---------------------------------------------------------------------------
# Lauf (DB-Anbindung dünn)
# ---------------------------------------------------------------------------
async def _event_candles(symbol: str, t0_ms: int, p: dict | None = None) -> list[dict]:
    p = p or FIXED
    start = t0_ms - int(p["pre_range_hours"] * 3600 * 1000) - 600_000
    end = t0_ms + int(p["timeout_drift_min"] + 30) * 60_000
    return await fetch_klines_range(symbol, "5m", start_ms=start, end_ms=end, limit=200)


async def run(db, years: float = 2.0, symbols: list[str] | None = None,
              params: dict | None = None) -> dict:
    global _running
    if _running:
        return {"status": "busy", "detail": "FOMC-Backtest läuft bereits"}
    _running = True
    try:
        symbols = symbols or SYMBOLS
        p = {**FIXED, **(params or {})}
        decisions = fomc_event.past_decisions(years=years)
        all_trades: list[dict] = []
        per_event: dict[str, dict] = {}
        for dt in decisions:
            ev = dt.strftime("%Y-%m-%d")
            t0 = int(dt.timestamp())
            ev_trades = []
            for sym in symbols:
                candles = await _event_candles(sym, t0 * 1000, p)
                if len(candles) < 20:
                    continue
                for t in simulate_event(candles, t0, p=p):
                    t.update({"event": ev, "symbol": sym})
                    ev_trades.append(t)
                await asyncio.sleep(0.15)   # Bitunix-Rate schonen
            all_trades += ev_trades
            per_event[ev] = {"trades": len(ev_trades),
                             "pnl": round(sum(t["pnl"] for t in ev_trades), 2)}
        events_sorted = [dt.strftime("%Y-%m-%d") for dt in decisions]
        agg = aggregate(all_trades, events_sorted)
        ok, why = validated(agg)
        result = {
            "run_at": datetime.now(timezone.utc).isoformat(),
            "years": years, "symbols": symbols, "asset_class": ASSET_CLASS,
            "params_fixed": p, "ai_params": bool(params),
            "aggregate": agg, "per_event": per_event,
            "trades": sorted(all_trades, key=lambda t: t["entry_ts"]),
            "validated": ok, "validation_reason": why,
            "note": ("Feste Regeln ohne Parameter-Optimierung; Validierung verlangt "
                     "positives Gesamt- UND Out-of-Sample-PnL (Overfitting-Schutz)."),
        }
        await db.settings.update_one({"_id": RESULT_ID}, {"$set": result}, upsert=True)
        await fomc_event.set_validation(
            db, ASSET_CLASS, ok, why,
            {"trades": agg["total"]["trades"], "pnl": agg["total"]["pnl"],
             "oos_pnl": agg["out_of_sample"]["pnl"]})
        logger.info(f"FOMC-Backtest: {len(all_trades)} Trades, validiert={ok} ({why})")
        return {"status": "ok", **result}
    finally:
        _running = False


async def last_result(db) -> dict | None:
    doc = await db.settings.find_one({"_id": RESULT_ID})
    if doc:
        doc.pop("_id", None)
    return doc


def is_running() -> bool:
    return _running
