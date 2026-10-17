"""Regime-Cockpit (PLAN_REGIME_COCKPIT B1/B2): Live-Sicht auf BEIDE Regime-Ebenen
eines Symbols plus Vorwärts-Kontrolle der Erkennung.

- Kurzfrist-Ebene: Market-Observer-Snapshots (`ai_market_snapshots.features.regime`)
- Struktur-Ebene:  freigegebenes Lab-Modell (`structural_regime`), Verlauf in der
  neuen Collection `structural_regime_history` (ein Eintrag je Wechsel + Heartbeat)
- Vorwärts-Trefferquote: „Was sagte das Label damals – wohin lief der Kurs danach?“
  (realisiert, kein Selbsttest gegen die eigenen Kerzen)

Reine Funktionen oben (unit-testbar), DB/IO unten. Nichts hier verändert
bestehende Collections oder das Trading-Verhalten.
"""
import bisect
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

HISTORY_COLL = "structural_regime_history"
HEARTBEAT_S = 6 * 3600
OBSERVER_HORIZON_MS = 4 * 3600 * 1000        # Kurzfrist-Label: 4 h voraus
STRUCTURAL_HORIZON_MS = 3 * 86400 * 1000     # Struktur-Label: 3 Tage voraus
MIN_POINTS_FOR_RATE = 5
OVERVIEW_TTL_S = 600

_overview_cache: Dict[str, Dict] = {}


# --------------------------------------------------------------------------
# Reine Bausteine
# --------------------------------------------------------------------------
def observer_direction(label: Optional[str]) -> Optional[str]:
    """Kurzfrist-Label -> Richtung. breakout = Richtung offen -> None."""
    s = str(label or "")
    if s.startswith("trend_up"):
        return "up"
    if s.startswith("trend_down"):
        return "down"
    if s.startswith("range") or s.startswith("drift"):
        return "side"
    return None


def ts_ms(value) -> Optional[int]:
    """ISO-String / datetime / Zahl -> Millisekunden (rein)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        return int(v if v > 1e11 else v * 1000)
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except ValueError:
        return None


def segments(points: List[Dict]) -> List[Dict]:
    """Aufeinanderfolgende Punkte gleicher Beschriftung -> Abschnitte
    [{label, direction, from_ts, to_ts, n}]. Erwartet zeitlich sortierte Punkte."""
    out: List[Dict] = []
    for p in points:
        if out and out[-1]["label"] == p.get("label"):
            out[-1]["to_ts"] = p["ts"]
            out[-1]["n"] += 1
            continue
        out.append({"label": p.get("label"), "direction": p.get("direction"),
                    "from_ts": p["ts"], "to_ts": p["ts"], "n": 1})
    return out


def price_at(prices: List[Tuple[int, float]], ts: int) -> Optional[float]:
    """Letzter bekannter Kurs <= ts (prices sortiert nach Zeit)."""
    if not prices:
        return None
    keys = [p[0] for p in prices]
    i = bisect.bisect_right(keys, ts) - 1
    return prices[i][1] if i >= 0 else None


def forward_hits(points: List[Dict], prices: List[Tuple[int, float]], horizon_ms: int,
                 flat_pct: Optional[float] = None) -> Dict:
    """Vorwärts-Trefferquote je Label.

    Treffer: up -> Kurs nach horizon höher; down -> tiefer;
    side -> |Bewegung| < flat_pct (Default: Median der |Horizont-Bewegungen| aller
    Punkte, also „unterdurchschnittliche Bewegung“). Punkte ohne Richtung oder
    ohne Zukunftskurs zählen nicht."""
    moves: List[Tuple[Dict, float]] = []
    for p in points:
        d = p.get("direction")
        if not d:
            continue
        p0 = price_at(prices, p["ts"])
        p1 = price_at(prices, p["ts"] + horizon_ms)
        if not p0 or p1 is None or prices[-1][0] < p["ts"] + horizon_ms:
            continue
        moves.append((p, (p1 - p0) / p0 * 100.0))
    if flat_pct is None:
        absm = sorted(abs(m) for _, m in moves)
        flat_pct = absm[len(absm) // 2] if absm else 0.0
    per: Dict[str, Dict] = {}
    for p, mv in moves:
        d = p["direction"]
        hit = (mv > 0) if d == "up" else (mv < 0) if d == "down" else (abs(mv) < flat_pct)
        row = per.setdefault(str(p.get("label")), {"label": p.get("label"), "direction": d,
                                                    "n": 0, "hits": 0})
        row["n"] += 1
        row["hits"] += 1 if hit else 0
    rows = []
    for row in per.values():
        row["hit_pct"] = round(row["hits"] / row["n"] * 100.0, 1) if row["n"] else None
        row["reliable"] = row["n"] >= MIN_POINTS_FOR_RATE
        rows.append(row)
    rows.sort(key=lambda r: -r["n"])
    n = sum(r["n"] for r in rows)
    hits = sum(r["hits"] for r in rows)
    return {"horizon_ms": horizon_ms, "flat_pct": round(flat_pct, 3), "n": n,
            "hit_pct": round(hits / n * 100.0, 1) if n else None,
            "reliable": n >= MIN_POINTS_FOR_RATE, "per_label": rows}


def agreement(observer: List[Dict], structural: List[Dict]) -> Optional[Dict]:
    """Anteil der Observer-Punkte, deren Richtung zur zeitgleich gültigen
    Struktur-Richtung passt (side vs. Trend zählt als Widerspruch)."""
    if not observer or not structural:
        return None
    s_sorted = sorted(structural, key=lambda x: x["ts"])
    keys = [s["ts"] for s in s_sorted]
    n = same = 0
    for p in observer:
        if not p.get("direction"):
            continue
        i = bisect.bisect_right(keys, p["ts"]) - 1
        if i < 0 or not s_sorted[i].get("direction"):
            continue
        n += 1
        same += 1 if s_sorted[i]["direction"] == p["direction"] else 0
    return {"n": n, "agree_pct": round(same / n * 100.0, 1) if n else None}


def history_changed(prev: Optional[Dict], ctx: Dict, now_ms: int) -> bool:
    """Neuer Verlaufseintrag nötig? Bei Wechsel von Regime/Richtung/Zustand/
    Stufe – oder als Heartbeat spätestens alle HEARTBEAT_S."""
    if not prev:
        return True
    for k in ("regime_id", "direction", "state", "stage"):
        if prev.get(k) != ctx.get(k):
            return True
    return (now_ms - int(prev.get("ts") or 0)) >= HEARTBEAT_S * 1000


def history_entry(symbol: str, ctx: Dict, now_ms: int) -> Dict:
    return {"symbol": symbol, "ts": now_ms,
            "at": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            "regime_id": ctx.get("regime_id"), "direction": ctx.get("direction"),
            "label": ctx.get("label"), "phase": ctx.get("phase"), "state": ctx.get("state"),
            "stage": ctx.get("stage"), "confidence": ctx.get("confidence"),
            "aid": ctx.get("aid"), "model_fingerprint": ctx.get("model_fingerprint")}


def trades_to_markers(trades: List[Dict]) -> List[Dict]:
    out = []
    for t in trades:
        pnl = t.get("realized_pnl", t.get("pnl"))
        out.append({"id": t.get("id"), "side": t.get("side"),
                    "opened_ts": ts_ms(t.get("opened_at")), "closed_ts": ts_ms(t.get("closed_at")),
                    "entry": t.get("entry"), "exit": t.get("exit_price"),
                    "pnl": round(float(pnl), 2) if pnl is not None else None,
                    "result": t.get("result") or ("open" if t.get("status") == "open" else None),
                    "regime": ((t.get("entry_market_snapshot") or {}).get("features") or {}).get("regime"),
                    "structural": ((t.get("entry_market_snapshot") or {}).get("structural") or {}).get("phase")})
    return out


# --------------------------------------------------------------------------
# DB / IO
# --------------------------------------------------------------------------
async def record_structural(db, symbol: str, ctx: Dict) -> bool:
    """Additiver Hook aus structural_regime.resolve(): Verlauf schreiben.
    Fehler werden geschluckt – der Resolver darf nie daran scheitern."""
    if db is None or not ctx or ctx.get("state") == "unknown":
        return False
    try:
        now_ms = int(time.time() * 1000)
        prev = await db[HISTORY_COLL].find_one({"symbol": symbol}, sort=[("ts", -1)])
        if not history_changed(prev, ctx, now_ms):
            return False
        await db[HISTORY_COLL].insert_one(history_entry(symbol, ctx, now_ms))
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug(f"regime_cockpit history {symbol}: {e}")
        return False


async def _prices(symbol: str, days: int) -> List[Tuple[int, float]]:
    import aiohttp
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    async with aiohttp.ClientSession() as session:
        raw = await fetch_history(session, symbol, days)
    candles = aggregate_candles(raw, "1h", drop_partial=True)
    del raw
    return [(int(c["timestamp"]), float(c["close"])) for c in candles]


async def _observer_points(db, symbol: str, since_iso: str) -> List[Dict]:
    rows = await db.ai_market_snapshots.find(
        {"symbol": symbol, "ts": {"$gte": since_iso}},
        {"_id": 0, "ts": 1, "features.regime": 1, "features.market_closed": 1}).sort("ts", 1).to_list(5000)
    out = []
    for r in rows:
        f = r.get("features") or {}
        if f.get("market_closed"):
            continue
        t = ts_ms(r.get("ts"))
        if t is None:
            continue
        out.append({"ts": t, "label": f.get("regime"), "direction": observer_direction(f.get("regime"))})
    return out


async def _structural_points(db, symbol: str, since_ms: int) -> List[Dict]:
    rows = await db[HISTORY_COLL].find({"symbol": symbol, "ts": {"$gte": since_ms}},
                                       {"_id": 0}).sort("ts", 1).to_list(2000)
    return [{"ts": int(r["ts"]), "label": r.get("label"), "direction": r.get("direction"),
             "state": r.get("state"), "stage": r.get("stage"), "confidence": r.get("confidence")}
            for r in rows]


async def _trades(db, symbol: str, since_iso: str) -> List[Dict]:
    rows = await db.auto_trades.find(
        {"symbol": symbol, "strategy_id": "ai_trader", "opened_at": {"$gte": since_iso}},
        {"_id": 0, "id": 1, "side": 1, "opened_at": 1, "closed_at": 1, "entry": 1, "exit_price": 1,
         "realized_pnl": 1, "pnl": 1, "result": 1, "status": 1,
         "entry_market_snapshot.features.regime": 1, "entry_market_snapshot.structural.phase": 1}
    ).sort("opened_at", 1).to_list(500)
    return trades_to_markers(rows)


async def assemble(db, symbol: str, days: int = 14) -> Dict:
    """Komplettes Cockpit für ein Symbol (nur lesend)."""
    from services import structural_regime
    days = int(min(max(days, 3), 60))
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since_iso = since_dt.isoformat()
    since_ms = int(since_dt.timestamp() * 1000)
    prices = await _prices(symbol, days + 1)
    observer = await _observer_points(db, symbol, since_iso)
    structural = await _structural_points(db, symbol, since_ms)
    stage = await structural_regime.stage_of(symbol)
    current_struct = await structural_regime.resolve(symbol) if stage != "none" else None
    trades = await _trades(db, symbol, since_iso)
    obs_hits = forward_hits(observer, prices, OBSERVER_HORIZON_MS)
    str_hits = forward_hits(structural, prices, STRUCTURAL_HORIZON_MS) if structural else None
    return {"symbol": symbol, "days": days, "generated_at": datetime.now(timezone.utc).isoformat(),
            "prices": prices,
            "observer": {"points": observer, "segments": segments(observer), "hits": obs_hits,
                         "horizon_hours": OBSERVER_HORIZON_MS // 3600000,
                         "current": observer[-1] if observer else None},
            "structural": {"stage": stage, "current": current_struct, "points": structural,
                           "segments": segments(structural), "hits": str_hits,
                           "horizon_days": STRUCTURAL_HORIZON_MS // 86400000},
            "trades": trades,
            "agreement": agreement(observer, structural)}


def summary_of(cockpit: Dict) -> Dict:
    """Kompakte Kennzahlen (für Übersicht + KI-Prompt)."""
    obs = cockpit.get("observer") or {}
    st = cockpit.get("structural") or {}
    tr = cockpit.get("trades") or []
    closed = [t for t in tr if t.get("pnl") is not None and t.get("result") in ("win", "loss")]
    return {"symbol": cockpit.get("symbol"), "days": cockpit.get("days"),
            "observer_hit_pct": (obs.get("hits") or {}).get("hit_pct"),
            "observer_n": (obs.get("hits") or {}).get("n"),
            "observer_reliable": (obs.get("hits") or {}).get("reliable"),
            # Trefferquote je Regime-Label (für Snapshot/ML-Gate: schwache Labels erkennen)
            "observer_per_label": {str(r.get("label")): {"hit_pct": r.get("hit_pct"), "n": r.get("n"),
                                                          "reliable": bool(r.get("reliable"))}
                                   for r in ((obs.get("hits") or {}).get("per_label") or [])
                                   if r.get("label") is not None},
            "observer_current": (obs.get("current") or {}).get("label"),
            "structural_stage": st.get("stage"),
            "structural_hit_pct": (st.get("hits") or {}).get("hit_pct") if st.get("hits") else None,
            "structural_current": ((st.get("current") or {}).get("phase")
                                   if (st.get("current") or {}).get("state") == "ok" else None),
            "agreement_pct": (cockpit.get("agreement") or {}).get("agree_pct"),
            "trades": len(closed), "wins": sum(1 for t in closed if t["result"] == "win"),
            "pnl": round(sum(t["pnl"] for t in closed), 2)}


async def overview(db, symbols: List[str], days: int = 14) -> List[Dict]:
    """Übersicht je Symbol, 10 min gecacht (die Kursabfrage ist der teure Teil)."""
    out = []
    for s in symbols:
        key = f"{s}:{days}"
        ent = _overview_cache.get(key)
        if ent and time.time() - ent["_at"] < OVERVIEW_TTL_S:
            out.append(ent["row"])
            continue
        try:
            row = summary_of(await assemble(db, s, days))
        except Exception as e:  # noqa: BLE001
            row = {"symbol": s, "days": days, "error": str(e)[:120]}
        _overview_cache[key] = {"_at": time.time(), "row": row}
        out.append(row)
    return out


_refresh_task = None


def overview_cached(db, symbols: List[str], days: int = 14) -> List[Dict]:
    """Nicht blockierend (für den KI-Prompt): liefert nur, was im Cache liegt
    (bis 3×TTL alt) und stößt für fehlende/abgelaufene Symbole EINE
    Hintergrund-Auffrischung an. Die Analyse wartet nie auf Kursdownloads."""
    global _refresh_task
    import asyncio
    rows, missing = [], []
    for s in symbols:
        ent = _overview_cache.get(f"{s}:{days}")
        if ent and time.time() - ent["_at"] < 3 * OVERVIEW_TTL_S:
            rows.append(ent["row"])
        if not ent or time.time() - ent["_at"] >= OVERVIEW_TTL_S:
            missing.append(s)
    if missing and (_refresh_task is None or _refresh_task.done()):
        try:
            _refresh_task = asyncio.get_running_loop().create_task(overview(db, missing, days))
        except RuntimeError:
            pass
    return rows
