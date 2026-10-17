"""Nachanalyse (Post-Trade-Review) des KI-Traders – "Was wäre gewesen, wenn …?"

Nach dem Schließen eines Trades (bzw. Verfall einer Key-Level-Limit-Order) wird
mit den echten 1m-Kerzen geprüft, wie sich der Kurs DANACH entwickelt hat und wie
Varianten desselben Setups gelaufen wären:

  * TP-Varianten  – Final-TP näher/weiter (×0.75 … ×2.0 des Risikos)
  * SL-Varianten  – Stop enger/weiter (×0.75 … ×1.5 des Risikos)
  * Runner        – kein festes Ziel, Position bis Fensterende laufen lassen
  * Limit-Orders  – nicht gefüllt: wäre ein Market-Entry aufgegangen (verpasste
                    Bewegung) oder hat die Order einen Verlust vermieden?

Ergebnis je Trade -> Collection `trade_reviews` (bzw. `limit_reviews`).

OVERFITTING-SCHUTZ (mehrstufig, alle Regeln rein & testbar):
  1. Mindeststichprobe: Aussagen je Setup erst ab MIN_SAMPLE Trades.
  2. Median statt Mittelwert: einzelne Ausreißer-Trades kippen keine Empfehlung.
  3. Konsistenz: eine Variante gilt nur als besser, wenn sie bei ≥ MIN_CONSISTENCY
     der Trades besser war – nicht nur im Schnitt.
  4. Split-Half (Walk-Forward): ältere UND jüngere Hälfte der Trades müssen die
     Verbesserung unabhängig zeigen (Zufallsfunde fallen hier durch).
  5. Kosten-Schwelle: Verbesserungen unter MIN_GAIN_R (R-Vielfache) gelten als
     Rauschen/Fees und werden ignoriert.
  6. Schrittweite: Empfehlungen sind auf ±TUNE_MAX_STEP (setup_lifecycle) je
     Parameter gedeckelt – große Sprünge sind ausgeschlossen.
  7. Backtest-Gegenprobe: das Setup-Backtest-Ergebnis (IS/OOS) wird als
     Bestätigungsstufe mitgeliefert; ohne Bestätigung bleibt es ein Hinweis.
  8. Zeitliche Begrenzung: nur Trades der letzten LOOKBACK_DAYS zählen.
"""
import asyncio
import logging
import statistics
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

TP_VARIANTS = (0.75, 1.25, 1.5, 2.0)
SL_VARIANTS = (0.75, 1.25, 1.5)
MIN_SAMPLE = 8
MIN_CONSISTENCY = 0.60
MIN_GAIN_R = 0.15
LOOKBACK_DAYS = 30
MAX_AGE_DAYS = 7           # ältere Trades: keine 1m-Historie mehr zuverlässig -> überspringen
LOOKAHEAD_MIN = 30
LOOKAHEAD_MAX = 24 * 60
LOOKAHEAD_FACTOR = 2.0     # Nachlauf-Fenster = 2× Trade-Dauer (geclampt)
TICK_SEC = 600
BATCH = 12


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ms(ts) -> Optional[int]:
    if ts is None:
        return None
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except (TypeError, ValueError):
        return None


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# Reine Bausteine
# --------------------------------------------------------------------------
def lookahead_minutes(opened_ms: int, closed_ms: int) -> int:
    """Nachlauf-Fenster nach dem Close: 2× Trade-Dauer, mind. 30 min, max. 24 h."""
    dur_min = max(0.0, (closed_ms - opened_ms) / 60000.0)
    return int(max(LOOKAHEAD_MIN, min(LOOKAHEAD_MAX, dur_min * LOOKAHEAD_FACTOR)))


def r_multiple(side: str, entry: float, price: float, risk: float) -> float:
    if risk <= 0 or entry <= 0:
        return 0.0
    move = (price - entry) if str(side).upper() == "LONG" else (entry - price)
    return round(move / risk, 3)


def slice_candles(candles: List[Dict], start_ms: int, end_ms: int) -> List[Dict]:
    return [c for c in candles if start_ms <= int(c.get("timestamp") or 0) <= end_ms]


def simulate_exit(candles: List[Dict], side: str, sl: Optional[float],
                  tp: Optional[float]) -> Dict:
    """Pfad-Simulation: erster Treffer von SL/TP (konservativ: berührt eine Kerze
    beide Level, zählt der SL). Kein Treffer -> Exit zum letzten Close ('open')."""
    long = str(side).upper() == "LONG"
    for c in candles:
        hi, lo = _f(c.get("high")), _f(c.get("low"))
        if hi <= 0 or lo <= 0:
            continue
        sl_hit = sl is not None and ((lo <= sl) if long else (hi >= sl))
        tp_hit = tp is not None and ((hi >= tp) if long else (lo <= tp))
        if sl_hit:
            return {"exit": float(sl), "reason": "sl", "ts": int(c.get("timestamp") or 0)}
        if tp_hit:
            return {"exit": float(tp), "reason": "tp", "ts": int(c.get("timestamp") or 0)}
    if candles:
        return {"exit": _f(candles[-1].get("close")), "reason": "open",
                "ts": int(candles[-1].get("timestamp") or 0)}
    return {"exit": None, "reason": "no_data", "ts": None}


def excursions(candles: List[Dict], side: str, ref: float, risk: float) -> Tuple[float, float]:
    """(MFE, MAE) in R relativ zu `ref` über den Kerzen-Ausschnitt."""
    if not candles or risk <= 0:
        return 0.0, 0.0
    long = str(side).upper() == "LONG"
    highs = [_f(c.get("high")) for c in candles if _f(c.get("high")) > 0]
    lows = [_f(c.get("low")) for c in candles if _f(c.get("low")) > 0]
    if not highs or not lows:
        return 0.0, 0.0
    best = max(highs) if long else min(lows)
    worst = min(lows) if long else max(highs)
    return r_multiple(side, ref, best, risk), -r_multiple(side, ref, worst, risk)


def trade_frame(t: Dict) -> Optional[Dict]:
    """Kernzahlen eines Trades (Entry, Risiko, Level, Zeiten) – None wenn unvollständig."""
    entry = _f(t.get("entry"))
    sl0 = _f(t.get("initial_sl") or t.get("sl"))
    tpf = _f(t.get("tpf"))
    opened = _parse_ms(t.get("opened_at"))
    closed = _parse_ms(t.get("closed_at"))
    if entry <= 0 or sl0 <= 0 or tpf <= 0 or not opened or not closed or closed < opened:
        return None
    risk = _f(t.get("risk")) or abs(entry - sl0)
    if risk <= 0:
        return None
    side = str(t.get("side") or "").upper()
    if side not in ("LONG", "SHORT"):
        return None
    sign = 1 if side == "LONG" else -1
    return {"entry": entry, "sl": sl0, "tpf": tpf, "risk": risk, "side": side,
            "sign": sign, "opened_ms": opened, "closed_ms": closed,
            "tp_ratio": round(abs(tpf - entry) / risk, 3),
            "exit": _f(t.get("exit_price")) or None}


def review_trade(t: Dict, candles: List[Dict]) -> Optional[Dict]:
    """Nachanalyse EINES geschlossenen Trades (rein). None = keine Datenbasis."""
    fr = trade_frame(t)
    if not fr:
        return None
    la_min = lookahead_minutes(fr["opened_ms"], fr["closed_ms"])
    end_ms = fr["closed_ms"] + la_min * 60000
    path = slice_candles(candles, fr["opened_ms"], end_ms)
    after = slice_candles(candles, fr["closed_ms"], end_ms)
    if len(path) < 2:
        return None
    entry, risk, side, sign = fr["entry"], fr["risk"], fr["side"], fr["sign"]
    exit_p = fr["exit"] or _f(after[0].get("close") if after else path[-1].get("close"))
    base_r = r_multiple(side, entry, exit_p, risk)

    variants: Dict[str, Dict] = {}
    for fct in TP_VARIANTS:
        tp = entry + sign * risk * fr["tp_ratio"] * fct
        sim = simulate_exit(path, side, fr["sl"], tp)
        variants[f"tp_x{fct:g}"] = {"kind": "tp", "factor": fct, "reason": sim["reason"],
                                    "r": r_multiple(side, entry, sim["exit"], risk)
                                    if sim["exit"] else base_r}
    for fct in SL_VARIANTS:
        sl = entry - sign * risk * fct
        sim = simulate_exit(path, side, sl, fr["tpf"])
        variants[f"sl_x{fct:g}"] = {"kind": "sl", "factor": fct, "reason": sim["reason"],
                                    "r": r_multiple(side, entry, sim["exit"], risk)
                                    if sim["exit"] else base_r}
    run = simulate_exit(path, side, fr["sl"], None)
    variants["runner"] = {"kind": "runner", "factor": None, "reason": run["reason"],
                          "r": r_multiple(side, entry, run["exit"], risk) if run["exit"] else base_r}
    for v in variants.values():
        v["delta_r"] = round(v["r"] - base_r, 3)

    mfe_after, mae_after = excursions(after, side, exit_p, risk)
    result = str(t.get("result") or "")
    if result == "win" and mfe_after >= 1.0:
        verdict, verdict_text = "early_exit", f"zu früh raus: nach dem Exit noch +{mfe_after:.1f}R Bewegung"
    elif result == "loss" and mfe_after >= 1.0:
        verdict, verdict_text = "stopped_before_move", f"ausgestoppt, danach lief der Kurs +{mfe_after:.1f}R in Trade-Richtung (SL zu eng?)"
    elif result == "win" and mae_after >= 1.0:
        verdict, verdict_text = "good_exit", f"guter Exit: Kurs drehte danach {mae_after:.1f}R gegen den Trade"
    elif result == "loss" and mae_after >= 1.0:
        verdict, verdict_text = "right_stop", "Stop war richtig: Kurs lief danach weiter gegen den Trade"
    else:
        verdict, verdict_text = "neutral", "kein klares Nachlauf-Signal"
    return {
        "trade_id": t.get("id"), "symbol": t.get("symbol"), "side": side,
        "setup": t.get("setup") or "unbekannt", "mode": t.get("mode"),
        "horizon": t.get("horizon") or "scalp", "timeframe": t.get("timeframe"),
        "data_collection": bool(t.get("data_collection")),
        "limit_entry": bool(t.get("limit_entry")),
        "result": result, "realized_pnl": round(_f(t.get("realized_pnl")), 4),
        "entry": entry, "exit": exit_p, "risk": round(risk, 8), "tp_ratio": fr["tp_ratio"],
        "base_r": base_r, "variants": variants,
        "after": {"minutes": la_min, "mfe_r": mfe_after, "mae_r": mae_after, "candles": len(after)},
        "verdict": verdict, "verdict_text": verdict_text,
        "opened_at": t.get("opened_at"), "closed_at": t.get("closed_at"),
        "reviewed_at": _now_iso(),
    }


def review_limit_order(row: Dict, candles: List[Dict]) -> Optional[Dict]:
    """Nachanalyse einer NICHT gefüllten Key-Level-Limit-Order (rein)."""
    sig = row.get("signal") or {}
    side = str(row.get("side") or sig.get("type") or "").upper()
    limit = _f(row.get("limit_price"))
    ref = _f(row.get("ref_price")) or _f(sig.get("entry_price"))
    sl = _f(sig.get("stop_loss"))
    tp = _f(sig.get("take_profit_full") or sig.get("take_profit_1"))
    start = _parse_ms(row.get("created_at"))
    end = _parse_ms(row.get("closed_at") or row.get("expires_at"))
    if side not in ("LONG", "SHORT") or limit <= 0 or ref <= 0 or sl <= 0 or tp <= 0 \
            or not start or not end:
        return None
    sign = 1 if side == "LONG" else -1
    risk = abs(limit - sl)
    if risk <= 0:
        return None
    window_end = end + max(LOOKAHEAD_MIN, int(row.get("valid_min") or 60)) * 60000
    path = slice_candles(candles, start, window_end)
    if len(path) < 2:
        return None
    # Wäre ein sofortiger Market-Entry (zum Referenzkurs) aufgegangen? Gleiche
    # SL/TP-Level, Risiko auf den Limit-Abstand normiert.
    sim = simulate_exit(path, side, sl, tp)
    market_r = r_multiple(side, ref, sim["exit"], risk) if sim["exit"] else 0.0
    lows = [_f(c.get("low")) for c in path]
    highs = [_f(c.get("high")) for c in path]
    closest = min(lows) if side == "LONG" else max(highs)
    miss_pct = round(abs(closest - limit) / limit * 100, 3)
    mfe, _ = excursions(path, side, ref, risk)
    if market_r >= 1.0:
        verdict, text = "missed_move", f"verpasste Bewegung: Market-Entry hätte +{market_r:.1f}R gebracht, Limit um {miss_pct}% verfehlt"
    elif market_r <= -0.5:
        verdict, text = "avoided_loss", f"Limit hat Verlust vermieden: Market-Entry wäre bei {market_r:.1f}R ausgestoppt"
    else:
        verdict, text = "neutral", "kein klares Bild (Bewegung blieb klein)"
    return {"order_id": row.get("id"), "symbol": row.get("symbol"), "side": side,
            "setup": row.get("setup") or "unbekannt", "status": row.get("status"),
            "limit_price": limit, "ref_price": ref, "dist_pct": _f(row.get("dist_pct")),
            "miss_pct": miss_pct, "market_r": market_r, "mfe_r": mfe,
            "market_exit_reason": sim["reason"], "verdict": verdict, "verdict_text": text,
            "created_at": row.get("created_at"), "reviewed_at": _now_iso()}


# --------------------------------------------------------------------------
# Aggregation mit Overfitting-Schutz (rein)
# --------------------------------------------------------------------------
def _median(vals: List[float]) -> float:
    return round(statistics.median(vals), 3) if vals else 0.0


def variant_verdict(deltas: List[float]) -> Dict:
    """Bewertet eine Variante über viele Trades: Median-Δ, Konsistenz,
    Split-Half. `robust` nur, wenn ALLE Schutzregeln erfüllt sind."""
    n = len(deltas)
    if n == 0:
        return {"n": 0, "median_delta_r": 0.0, "consistency": 0.0,
                "split_agree": False, "robust": False, "reason": "keine Daten"}
    med = _median(deltas)
    improved = sum(1 for d in deltas if d > 1e-9)
    worsened = sum(1 for d in deltas if d < -1e-9)
    consistency = round(improved / n, 3)
    half = n // 2
    older, newer = deltas[:half], deltas[half:]
    split_agree = bool(half >= 2 and _median(older) >= MIN_GAIN_R / 2
                       and _median(newer) >= MIN_GAIN_R / 2)
    reasons = []
    if n < MIN_SAMPLE:
        reasons.append(f"nur {n}/{MIN_SAMPLE} Trades")
    if med < MIN_GAIN_R:
        reasons.append(f"Median-Gewinn {med:+.2f}R unter Schwelle {MIN_GAIN_R}R")
    if consistency < MIN_CONSISTENCY:
        reasons.append(f"nur {int(consistency * 100)}% der Trades besser")
    if not split_agree:
        reasons.append("ältere/jüngere Hälfte uneinig")
    return {"n": n, "median_delta_r": med, "consistency": consistency,
            "improved": improved, "worsened": worsened, "split_agree": split_agree,
            "robust": not reasons, "reason": "; ".join(reasons) or "alle Schutzregeln erfüllt"}


def recommended_step(kind: str, factor: Optional[float]) -> Optional[float]:
    """Empfohlene Anpassung, auf ±TUNE_MAX_STEP gedeckelt (Anti-Overfitting)."""
    if factor is None:
        return None
    from services.setup_lifecycle import TUNE_MAX_STEP
    step = max(-TUNE_MAX_STEP, min(TUNE_MAX_STEP, factor - 1.0))
    return round(1.0 + step, 3)


def aggregate(reviews: List[Dict], backtest_state: Optional[Dict] = None) -> List[Dict]:
    """Setup-Übersicht: je Setup die beste Variante + Nachlauf-Statistik.
    `reviews` chronologisch (alt -> neu) – nötig für den Split-Half-Test."""
    by_setup: Dict[str, List[Dict]] = {}
    for r in reviews:
        by_setup.setdefault(str(r.get("setup") or "unbekannt"), []).append(r)
    out = []
    for sid, rows in sorted(by_setup.items()):
        rows = sorted(rows, key=lambda r: str(r.get("closed_at") or ""))
        n = len(rows)
        variants: Dict[str, Dict] = {}
        keys = set()
        for r in rows:
            keys.update((r.get("variants") or {}).keys())
        for k in sorted(keys):
            deltas = [_f((r.get("variants") or {}).get(k, {}).get("delta_r"))
                      for r in rows if k in (r.get("variants") or {})]
            sample = next((r["variants"][k] for r in rows if k in (r.get("variants") or {})), {})
            v = variant_verdict(deltas)
            v.update({"kind": sample.get("kind"), "factor": sample.get("factor"),
                      "step": recommended_step(sample.get("kind"), sample.get("factor"))})
            variants[k] = v
        robust = [(k, v) for k, v in variants.items() if v["robust"]]
        best = max(robust, key=lambda kv: kv[1]["median_delta_r"], default=None)
        verdicts: Dict[str, int] = {}
        for r in rows:
            verdicts[str(r.get("verdict"))] = verdicts.get(str(r.get("verdict")), 0) + 1
        bt = (backtest_state or {}).get(sid) or {}
        bt_ok = str(bt.get("status") or "") in ("passed", "tuned", "live")
        finding = None
        if best:
            k, v = best
            label = {"tp": "Final-TP", "sl": "Stop-Loss", "runner": "Runner (kein festes Ziel)"}[v["kind"]]
            if v["kind"] == "runner":
                finding = (f"{label} hätte im Median {v['median_delta_r']:+.2f}R mehr gebracht "
                           f"({int(v['consistency'] * 100)}% der {v['n']} Trades besser)")
            else:
                finding = (f"{label} ×{v['step']:g} (statt ×{v['factor']:g} – Schritt gedeckelt) hätte im "
                           f"Median {v['median_delta_r']:+.2f}R mehr gebracht "
                           f"({int(v['consistency'] * 100)}% der {v['n']} Trades besser)")
            finding += (" – Backtest bestätigt" if bt_ok
                        else " – vom Setup-Backtest NICHT bestätigt, nur Hinweis")
        out.append({
            "setup": sid, "trades": n, "median_base_r": _median([_f(r.get("base_r")) for r in rows]),
            "median_after_mfe_r": _median([_f((r.get("after") or {}).get("mfe_r")) for r in rows]),
            "verdicts": verdicts, "variants": variants,
            "best_variant": best[0] if best else None,
            "finding": finding, "backtest_confirmed": bt_ok,
            "backtest_status": bt.get("status"),
            "enough_data": n >= MIN_SAMPLE,
        })
    return out


def aggregate_limits(reviews: List[Dict]) -> Dict:
    n = len(reviews)
    counts: Dict[str, int] = {}
    for r in reviews:
        counts[str(r.get("verdict"))] = counts.get(str(r.get("verdict")), 0) + 1
    missed = counts.get("missed_move", 0)
    avoided = counts.get("avoided_loss", 0)
    note = None
    if n >= MIN_SAMPLE:
        if missed / n >= MIN_CONSISTENCY:
            note = (f"{missed}/{n} verfallene Limit-Orders verpassten eine Bewegung ≥1R – Limits "
                    f"liegen tendenziell zu weit vom Kurs (Median-Verfehlung "
                    f"{_median([_f(r.get('miss_pct')) for r in reviews]):.2f}%)")
        elif avoided / n >= MIN_CONSISTENCY:
            note = f"{avoided}/{n} verfallene Limit-Orders haben einen Verlust vermieden – Geduld zahlt sich aus"
    return {"n": n, "verdicts": counts, "note": note, "enough_data": n >= MIN_SAMPLE,
            "median_miss_pct": _median([_f(r.get("miss_pct")) for r in reviews])}


def context_lines(setups: List[Dict], limits: Dict) -> List[str]:
    """Prompt-Zeilen für den KI-Trader: NUR robuste Befunde als Hinweis."""
    lines = []
    for s in setups:
        if s.get("finding"):
            lines.append(f"- {s['setup']}: {s['finding']}")
        elif int(s.get("trades") or 0) >= MIN_SAMPLE:
            ve = s.get("verdicts") or {}
            early = ve.get("early_exit", 0)
            if early / max(1, s["trades"]) >= MIN_CONSISTENCY:
                lines.append(f"- {s['setup']}: {early}/{s['trades']} Trades zu früh beendet "
                             f"(Nachlauf-MFE Median {s['median_after_mfe_r']:+.1f}R) – keine Variante "
                             "robust genug für eine Parameter-Empfehlung")
    if limits.get("note"):
        lines.append(f"- Limit-Orders: {limits['note']}")
    if not lines:
        return []
    return (["NACHANALYSE (Was-wäre-wenn nach Trade-Close, Anti-Overfitting: Median, ≥"
             f"{int(MIN_CONSISTENCY * 100)}% Konsistenz, Split-Half, ≥{MIN_SAMPLE} Trades). Hinweise – "
             f"keine Regeln. Pro Setup höchstens EINEN Parameter anpassen, max. ±"
             "20 % je Schritt, und die Änderung als neue Profil-Version festhalten:"]
            + lines)


def proposals_from_setups(setups: List[Dict]) -> Dict[str, Dict]:
    """Robuste Befunde -> Versionsvorschlag je Setup (rein): genau EIN Parameter.
    TP-Variante -> tp_ratio × step, SL-Variante -> sl_pct × step; Runner-Befunde
    ergeben keinen Parameter (dafür gibt es den Runner-Modus)."""
    out: Dict[str, Dict] = {}
    for s in setups:
        k = s.get("best_variant")
        v = (s.get("variants") or {}).get(k) if k else None
        if not v or not v.get("robust") or v.get("kind") not in ("tp", "sl") or not v.get("step"):
            continue
        out[str(s["setup"])] = {
            "param": "tp_ratio" if v["kind"] == "tp" else "sl_pct",
            "factor": float(v["step"]),
            "note": f"{'TP' if v['kind'] == 'tp' else 'SL'} ×{v['step']:g}, Median {v['median_delta_r']:+.2f}R "
                    f"bei {int(v['consistency'] * 100)}% von {v['n']} Trades"
                    + ("" if s.get("backtest_confirmed") else " (ohne Backtest-Bestätigung)"),
            "backtest_confirmed": bool(s.get("backtest_confirmed")),
        }
    return out


# --------------------------------------------------------------------------
# DB-Anbindung (dünn)
# --------------------------------------------------------------------------
class PostmortemService:
    def __init__(self):
        self.db = None
        self._next_due = 0.0
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self.running = False

    def setup(self, db):
        self.db = db

    async def _candles(self, symbol: str, start_ms: int) -> List[Dict]:
        from services import candles as _c
        return await _c.recent_1m(symbol, start_ms, MAX_AGE_DAYS)

    async def _pending_trades(self, limit: int) -> List[Dict]:
        now = datetime.now(timezone.utc)
        cutoff = (now - timedelta(days=MAX_AGE_DAYS)).isoformat()
        rows = await self.db.auto_trades.find(
            {"status": "closed", "strategy_id": "ai_trader", "closed_at": {"$gte": cutoff},
             "postmortem": {"$exists": False}}, {"_id": 0}).sort("closed_at", 1).limit(limit * 3).to_list(limit * 3)
        ready = []
        now_ms = int(now.timestamp() * 1000)
        for t in rows:
            fr = trade_frame(t)
            if not fr:
                await self._mark(t.get("id"), "no_frame")
                continue
            if now_ms >= fr["closed_ms"] + lookahead_minutes(fr["opened_ms"], fr["closed_ms"]) * 60000:
                ready.append(t)
            if len(ready) >= limit:
                break
        return ready

    async def _mark(self, trade_id, status: str):
        if trade_id:
            await self.db.auto_trades.update_one({"id": trade_id}, {"$set": {"postmortem": status}})

    async def run_pending(self, limit: int = BATCH) -> Dict:
        if self.db is None or self.running:
            return {"status": "busy"}
        self.running = True
        done, skipped, limits_done = 0, 0, 0
        try:
            for t in await self._pending_trades(limit):
                candles = await self._candles(t["symbol"], _parse_ms(t.get("opened_at")) or 0)
                rev = review_trade(t, candles)
                if not rev:
                    skipped += 1
                    await self._mark(t.get("id"), "no_data")
                    continue
                await self.db.trade_reviews.update_one({"trade_id": rev["trade_id"]},
                                                       {"$set": rev}, upsert=True)
                await self._mark(t.get("id"), "done")
                done += 1
            cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
            lrows = await self.db.ai_limit_orders.find(
                {"status": {"$in": ["expired", "cancelled"]}, "created_at": {"$gte": cutoff},
                 "postmortem": {"$exists": False}}, {"_id": 0}).sort("created_at", 1).limit(limit).to_list(limit)
            for row in lrows:
                end = _parse_ms(row.get("closed_at") or row.get("expires_at")) or 0
                if time.time() * 1000 < end + max(LOOKAHEAD_MIN, int(row.get("valid_min") or 60)) * 60000:
                    continue
                candles = await self._candles(row["symbol"], _parse_ms(row.get("created_at")) or 0)
                rev = review_limit_order(row, candles)
                status = "done" if rev else "no_data"
                if rev:
                    await self.db.limit_reviews.update_one({"order_id": rev["order_id"]},
                                                           {"$set": rev}, upsert=True)
                    limits_done += 1
                await self.db.ai_limit_orders.update_one({"id": row["id"]},
                                                         {"$set": {"postmortem": status}})
            self.last_run = _now_iso()
            self.last_error = None
            if done or limits_done:
                logger.info(f"Nachanalyse: {done} Trades, {limits_done} Limit-Orders ausgewertet, "
                            f"{skipped} ohne Daten")
            return {"status": "ok", "trades": done, "limits": limits_done, "skipped": skipped}
        except Exception as e:
            self.last_error = str(e)[:300]
            logger.error(f"Nachanalyse fehlgeschlagen: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self.running = False

    async def _backtest_state(self) -> Dict:
        try:
            doc = await self.db.settings.find_one({"_id": "setup_backtest_state"}) or {}
            merged: Dict[str, Dict] = {}
            for cls_state in (doc.get("classes") or {}).values():
                for sid, entry in (cls_state or {}).items():
                    if isinstance(entry, dict) and (sid not in merged
                                                    or entry.get("status") in ("passed", "tuned", "live")):
                        merged[sid] = entry
            return merged
        except Exception:
            return {}

    async def summary(self, days: int = LOOKBACK_DAYS, mode: Optional[str] = None) -> Dict:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        match: Dict = {"closed_at": {"$gte": cutoff}}
        if mode in ("live", "paper"):
            match["mode"] = mode
        reviews = await self.db.trade_reviews.find(match, {"_id": 0}).sort("closed_at", 1).to_list(2000)
        lreviews = await self.db.limit_reviews.find(
            {"created_at": {"$gte": cutoff}}, {"_id": 0}).sort("created_at", 1).to_list(1000)
        setups = aggregate(reviews, await self._backtest_state())
        return {"days": days, "mode": mode or "all", "reviews": len(reviews),
                "setups": setups, "limits": aggregate_limits(lreviews),
                "recent": list(reversed(reviews))[:40],
                "recent_limits": list(reversed(lreviews))[:20],
                "rules": {"min_sample": MIN_SAMPLE, "min_consistency": MIN_CONSISTENCY,
                          "min_gain_r": MIN_GAIN_R, "lookback_days": LOOKBACK_DAYS,
                          "tp_variants": list(TP_VARIANTS), "sl_variants": list(SL_VARIANTS)},
                "last_run": self.last_run, "last_error": self.last_error}

    async def context_text(self) -> str:
        if self.db is None:
            return ""
        try:
            s = await self.summary()
            lines = context_lines(s["setups"], s["limits"])
            return "\n".join(lines) if lines else ""
        except Exception as e:
            logger.debug(f"Nachanalyse-Kontext: {e}")
            return ""

    async def proposals(self) -> Dict[str, Dict]:
        """Versionsvorschläge für setup_lifecycle (nur Backtest-bestätigte Befunde)."""
        if self.db is None:
            return {}
        s = await self.summary()
        return {k: v for k, v in proposals_from_setups(s["setups"]).items() if v["backtest_confirmed"]}

    async def tick(self):
        if self.db is None or time.time() < self._next_due:
            return
        self._next_due = time.time() + TICK_SEC
        await self.run_pending()

    async def run_loop(self):
        await asyncio.sleep(90)
        while True:
            try:
                await self.tick()
            except Exception as e:
                logger.error(f"Nachanalyse-Loop: {e}")
            await asyncio.sleep(60)


postmortem = PostmortemService()
