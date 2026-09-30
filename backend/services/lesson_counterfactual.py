"""HOLD-Gegenprobe (PLAN_LEKTIONS_BILANZ, Baustein B).

Eine Lektion, die einen Trade VERHINDERT hat (HOLD mit `would_be.blocked_by`),
hat nur dann einen Wert, wenn der verhinderte Trade ein Verlierer geworden
wäre. Deshalb wird jede blockierte HOLD-Entscheidung nach Ablauf des Horizonts
mit echten 1m-Kerzen nachsimuliert (SL/TP aus `would_be`, konservativ: berührt
eine Kerze beide Level, zählt der SL) und NETTO (Fees + Spread + Slippage)
abgerechnet. Ergebnis -> Collection `ai_lesson_cf`.

Struktur wie `trade_postmortem.py`: reine Funktionen oben, dünner DB-Wrapper
unten. Fail-open: Fehler landen im Log, nie im Trading-Pfad.
"""
import asyncio
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from services import trade_postmortem as pm

logger = logging.getLogger(__name__)

HORIZON_MIN = {"scalp": 240, "swing": 1440}
BATCH = 12
LOOKBACK_DAYS = 30
MAX_AGE_DAYS = 7           # wie Postmortem: ältere 1m-Historie nicht zuverlässig
TICK_SEC = 900
DEFAULT_SL_PCT = 0.6
DEFAULT_TP1_PCT = 0.9


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def horizon_minutes(horizon: Optional[str]) -> int:
    return HORIZON_MIN.get(str(horizon or "scalp").lower(), HORIZON_MIN["scalp"])


def cost_pct(symbol: str, fee_pct: Optional[float] = None) -> float:
    """Roundtrip-Kosten in % (2× Fee + Spread + Slippage), rein aus den
    Fallback-Konstanten des Paper-Kostenmodells – kein Netz."""
    from services import paper_execution as pe
    from services.policy_lab import DEFAULT_SETTINGS
    tier = pe._tier(symbol)
    fee = float(DEFAULT_SETTINGS.get("fee_pct", 0.06) if fee_pct is None else fee_pct)
    return round(2 * fee + pe.FALLBACK_SPREAD_PCT.get(tier, 0.06)
                 + pe.SLIPPAGE_PCT.get(tier, 0.05), 4)


def frame_from_decision(dec: Dict) -> Optional[Dict]:
    """Hypothetischer Trade aus einer blockierten HOLD-Entscheidung. Ohne
    `would_be` -> None (keine Spekulation über die Richtung)."""
    wb = (dec or {}).get("would_be")
    if not isinstance(wb, dict) or str(dec.get("action", "")).upper() != "HOLD":
        return None
    side = str(wb.get("action") or "").upper()
    entry = pm._f(dec.get("price"))
    start = pm._parse_ms(dec.get("ts"))
    if side not in ("LONG", "SHORT") or entry <= 0 or not start:
        return None
    sl_pct = pm._f(wb.get("sl_pct"), DEFAULT_SL_PCT) or DEFAULT_SL_PCT
    tp_pct = pm._f(wb.get("tp1_pct"), DEFAULT_TP1_PCT) or DEFAULT_TP1_PCT
    sign = 1 if side == "LONG" else -1
    sl = entry * (1 - sign * sl_pct / 100)
    tp = entry * (1 + sign * tp_pct / 100)
    risk = abs(entry - sl)
    if risk <= 0:
        return None
    horizon = str(dec.get("horizon") or "scalp")
    return {"side": side, "entry": entry, "sl": sl, "tp": tp, "risk": risk,
            "start_ms": start, "horizon": horizon,
            "end_ms": start + horizon_minutes(horizon) * 60000,
            "blocked_by": list(wb.get("blocked_by") or []),
            "applied_lessons": list(dec.get("applied_lessons") or [])}


def review_hold(dec: Dict, candles: List[Dict], cost: Optional[float] = None) -> Dict:
    """Simulation EINER blockierten HOLD-Entscheidung (rein)."""
    fr = frame_from_decision(dec)
    base = {"decision_id": dec.get("id"), "symbol": dec.get("symbol"), "ts": dec.get("ts"),
            "horizon": (fr or {}).get("horizon") or dec.get("horizon") or "scalp",
            "blocked_by": (fr or {}).get("blocked_by") or [],
            "applied_lessons": (fr or {}).get("applied_lessons") or [],
            "policy_version": dec.get("policy_version"), "reviewed_at": _now_iso()}
    if not fr:
        return {**base, "status": "no_frame", "exit_reason": "no_data"}
    path = pm.slice_candles(candles, fr["start_ms"], fr["end_ms"])
    if len(path) < 2:
        return {**base, "action": fr["side"], "entry": fr["entry"], "sl": fr["sl"],
                "tp1": fr["tp"], "status": "no_data", "exit_reason": "no_data"}
    sim = pm.simulate_exit(path, fr["side"], fr["sl"], fr["tp"])
    exit_p = sim["exit"] if sim["exit"] else pm._f(path[-1].get("close"))
    gross_r = pm.r_multiple(fr["side"], fr["entry"], exit_p, fr["risk"])
    c = cost_pct(str(dec.get("symbol") or "")) if cost is None else float(cost)
    sign = 1 if fr["side"] == "LONG" else -1
    gross_pct = sign * (exit_p - fr["entry"]) / fr["entry"] * 100
    net_pct = round(gross_pct - c, 4)
    risk_pct = fr["risk"] / fr["entry"] * 100
    net_r = round(net_pct / risk_pct, 3) if risk_pct > 0 else 0.0
    mfe, mae = pm.excursions(path, fr["side"], fr["entry"], fr["risk"])
    return {**base, "action": fr["side"], "entry": fr["entry"], "sl": fr["sl"], "tp1": fr["tp"],
            "exit": exit_p, "exit_reason": sim["reason"], "r_gross": gross_r, "r": net_r,
            "pnl_pct_net": net_pct, "cost_pct": c, "mfe_r": mfe, "mae_r": mae,
            "lookahead_min": horizon_minutes(fr["horizon"]), "candles": len(path),
            "status": "done"}


def aggregate(reviews: List[Dict]) -> Dict[str, Dict]:
    """Je blockierender Lektion: verhinderte Trades und was daraus geworden wäre.
    `avoided_loss_r` (positiv = gut für die Lektion), `missed_gain_r` (positiv =
    verpasster Gewinn, schlecht für die Lektion), `net_r` = avoided - missed."""
    out: Dict[str, Dict] = {}
    for r in reviews:
        if r.get("status") != "done":
            for lid in r.get("blocked_by") or []:
                row = out.setdefault(lid, _empty())
                row["open"] += 1
            continue
        rv = float(r.get("r") or 0)
        for lid in r.get("blocked_by") or []:
            row = out.setdefault(lid, _empty())
            row["n"] += 1
            row["sum_r"] -= rv          # verhindert: Vorzeichen dreht sich
            if r.get("exit_reason") == "open":
                row["open"] += 1
            if rv < 0:
                row["would_loss"] += 1
                row["avoided_loss_r"] += -rv
            elif rv > 0:
                row["would_win"] += 1
                row["missed_gain_r"] += rv
    for row in out.values():
        row["net_r"] = round(row["avoided_loss_r"] - row["missed_gain_r"], 3)
        for k in ("sum_r", "avoided_loss_r", "missed_gain_r"):
            row[k] = round(row[k], 3)
    return out


def _empty() -> Dict:
    return {"n": 0, "would_win": 0, "would_loss": 0, "open": 0, "sum_r": 0.0,
            "avoided_loss_r": 0.0, "missed_gain_r": 0.0, "net_r": 0.0}


def is_due(dec: Dict, now_ms: Optional[int] = None) -> bool:
    fr = frame_from_decision(dec)
    if not fr:
        return False
    return (now_ms if now_ms is not None else int(time.time() * 1000)) >= fr["end_ms"]


# --------------------------------------------------------------------------
# DB-Anbindung (dünn)
# --------------------------------------------------------------------------
class CounterfactualService:
    def __init__(self):
        self.db = None
        self._next_due = 0.0
        self.last_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self.running = False

    def setup(self, db):
        self.db = db

    async def _pending(self, limit: int = BATCH) -> List[Dict]:
        """HOLDs mit `would_be`, Horizont abgelaufen, noch ohne Gegenprobe."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
        rows = await self.db.ai_decisions.find(
            {"action": "HOLD", "would_be": {"$exists": True}, "ts": {"$gte": cutoff},
             "lesson_cf": {"$exists": False}}, {"_id": 0}).sort("ts", 1).limit(limit * 3).to_list(limit * 3)
        now_ms = int(time.time() * 1000)
        out = []
        for d in rows:
            if is_due(d, now_ms):
                out.append(d)
            if len(out) >= limit:
                break
        return out

    async def _mark(self, decision_id, status: str):
        if decision_id:
            await self.db.ai_decisions.update_one({"id": decision_id}, {"$set": {"lesson_cf": status}})

    async def run_pending(self, limit: int = BATCH) -> Dict:
        if self.db is None or self.running:
            return {"status": "busy"}
        self.running = True
        done, skipped = 0, 0
        try:
            from services import candles as _c
            for d in await self._pending(limit):
                candles = await _c.recent_1m(d["symbol"], pm._parse_ms(d.get("ts")) or 0, MAX_AGE_DAYS)
                rev = review_hold(d, candles)
                await self.db.ai_lesson_cf.update_one({"decision_id": rev["decision_id"]},
                                                      {"$set": rev}, upsert=True)
                await self._mark(d.get("id"), rev["status"])
                done += rev["status"] == "done"
                skipped += rev["status"] != "done"
            self.last_run = _now_iso()
            self.last_error = None
            if done or skipped:
                logger.info(f"Lektions-Gegenprobe: {done} HOLDs simuliert, {skipped} ohne Daten")
            return {"status": "ok", "done": done, "skipped": skipped}
        except Exception as e:  # noqa: BLE001
            self.last_error = str(e)[:300]
            logger.error(f"Lektions-Gegenprobe fehlgeschlagen: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self.running = False

    async def reviews(self, days: int = LOOKBACK_DAYS) -> List[Dict]:
        if self.db is None:
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return await self.db.ai_lesson_cf.find({"ts": {"$gte": cutoff}}, {"_id": 0}) \
            .sort("ts", -1).to_list(3000)

    async def pending_count(self) -> int:
        if self.db is None:
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)).isoformat()
        return await self.db.ai_decisions.count_documents(
            {"action": "HOLD", "would_be": {"$exists": True}, "ts": {"$gte": cutoff},
             "lesson_cf": {"$exists": False}})

    async def tick(self):
        if self.db is None or time.time() < self._next_due:
            return
        self._next_due = time.time() + TICK_SEC
        await self.run_pending()

    async def run_loop(self):
        from core.config import local_engine_disabled
        await asyncio.sleep(150)
        while True:
            try:
                if not local_engine_disabled():
                    await self.tick()
            except Exception as e:  # noqa: BLE001
                logger.error(f"Lektions-Gegenprobe-Loop: {e}")
            await asyncio.sleep(60)


counterfactual = CounterfactualService()
