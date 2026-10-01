"""Wächter-Schattentrades (Guard-Shadow) + autonome Kalibrierung.

Problem: der Fee-Wächter (und andere Einstiegs-Wächter) blockt Live-Trades
des KI-Traders – ob der Block richtig war, wusste bisher niemand, weil der
Trade nie existierte. Lösung: jeder geblockte LIVE-Einstieg wird stattdessen
als Paper-Datensammel-Trade („Schattentrade“) eröffnet, markiert mit
`guard_shadow` (welcher Wächter, welcher Grund, welche Schwelle). Beim Close
wird das Urteil gespeichert (Block richtig = Trade hätte verloren, Block falsch
= Trade hätte netto nach Fees gewonnen) und der Fee-Wächter kalibriert seine
Faktoren autonom innerhalb harter Leitplanken nach.

Bausteine (rein testbar, keine Seiteneffekte):
- `verdict(pnl)`, `plan_adjustment(stats, cfg, state)` – reine Logik
- `maybe_open_shadow(mgr, signal, candles, ...)` – Hook in bitunix_trade
- `on_trade_closed(db, trade)` – Hook in _after_close
- `calibrate(db)` – Autotune (nur Fee-Wächter, Rest = Statistik)
"""
import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

COLL_REVIEWS = "guard_shadow_reviews"
COLL_LOG = "guard_calibration_log"
STATE_ID = "guard_autotune_state"

# Nur Wächter, deren Block sich als Paper-Trade sinnvoll nachspielen lässt.
SHADOW_GUARDS = ("fee_guard", "trade_guard", "regime_gate", "safety_status")
GUARD_LABELS = {"fee_guard": "Fee-Wächter", "trade_guard": "Trade-Guard (Kill-Switch/Stacking)",
                "regime_gate": "Regime-Filter", "safety_status": "Sicherheitsstatus"}

DEFAULTS = {
    "guard_shadow_enabled": True,        # geblockte Live-Trades als Schattentrade nachspielen
    "guard_autotune_enabled": True,      # Fee-Wächter kalibriert sich autonom (Leitplanken)
    "guard_autotune_min_samples": 12,    # geschlossene Schattentrades je Wächter/Fenster
}
SHADOW_COOLDOWN_S = 15 * 60              # gleicher Coin+Seite+Wächter: max. 1 Schatten je 15 min
MAX_OPEN_SHADOWS = 8
REVIEW_WINDOW_DAYS = 14
ADJUST_MIN_INTERVAL_H = 12
STEP = 0.25
LOOSEN_WRONG_RATE = 0.60                 # >= 60 % der Blocks waren falsch -> lockern
TIGHTEN_WRONG_RATE = 0.35                # <= 35 % falsch -> Richtung Baseline zurück
BOUNDS = {"fee_guard_mult": (1.0, 6.0), "fee_guard_atr_mult": (0.5, 6.0)}
MAX_DEVIATION = 1.5                      # maximale Abweichung von der Baseline (± Faktor)
BREAKEVEN_EPS = 1e-6

_last_shadow_ts: Dict[str, float] = {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------- reine Logik ----------------
def verdict(realized_pnl: Optional[float]) -> str:
    """Block richtig, wenn der Schattentrade netto verlor; falsch, wenn er
    netto (inkl. Fees) gewann; neutral bei ±0."""
    try:
        pnl = float(realized_pnl or 0.0)
    except (TypeError, ValueError):
        pnl = 0.0
    if pnl > BREAKEVEN_EPS:
        return "block_wrong"
    if pnl < -BREAKEVEN_EPS:
        return "block_right"
    return "neutral"


def limiting_factor(reason: str) -> str:
    return "atr" if "ATR-Minimum" in str(reason or "") else "fee"


def aggregate(reviews: List[Dict]) -> Dict[str, Dict]:
    """Statistik je Wächter (und je Fee-Wächter-Teilgrund): n, richtig/falsch,
    Netto-PnL der Schattentrades, Falsch-Quote."""
    out: Dict[str, Dict] = {}
    for r in reviews:
        keys = [str(r.get("guard") or "")]
        if r.get("guard") == "fee_guard":
            keys.append(f"fee_guard:{r.get('limiting') or 'fee'}")
        for k in keys:
            s = out.setdefault(k, {"n": 0, "right": 0, "wrong": 0, "neutral": 0,
                                   "net_pnl": 0.0})
            s["n"] += 1
            v = r.get("verdict")
            if v == "block_right":
                s["right"] += 1
            elif v == "block_wrong":
                s["wrong"] += 1
            else:
                s["neutral"] += 1
            s["net_pnl"] = round(s["net_pnl"] + float(r.get("realized_pnl") or 0), 4)
    for s in out.values():
        judged = s["right"] + s["wrong"]
        s["wrong_rate"] = round(s["wrong"] / judged, 3) if judged else None
    return out


def _clamp(key: str, value: float, baseline: float) -> float:
    lo, hi = BOUNDS[key]
    lo = max(lo, baseline - MAX_DEVIATION)
    hi = min(hi, baseline + MAX_DEVIATION)
    return round(min(max(value, lo), hi), 2)


def plan_adjustment(stats: Dict[str, Dict], cfg: Dict, state: Dict,
                    min_samples: int) -> Optional[Dict]:
    """Entscheidet EINE Anpassung des Fee-Wächters (oder None).

    - Falsch-Quote >= 60 % und Netto-PnL der Schatten > 0 -> lockern (-0.25)
    - Falsch-Quote <= 35 % und Netto-PnL < 0 -> Richtung Baseline zurück (+0.25),
      nur wenn vorher gelockert wurde (kein blindes Verschärfen)
    Leitplanken: harte Bounds + max. ±1.5 um die Baseline."""
    baseline = state.get("baseline") or {}
    for limiting, key in (("fee", "fee_guard_mult"), ("atr", "fee_guard_atr_mult")):
        s = stats.get(f"fee_guard:{limiting}")
        if not s or s["n"] < min_samples or s.get("wrong_rate") is None:
            continue
        try:
            cur = float(cfg.get(key, 2.5) or 0)
        except (TypeError, ValueError):
            continue
        base = float(baseline.get(key, cur))
        if s["wrong_rate"] >= LOOSEN_WRONG_RATE and s["net_pnl"] > 0:
            new = _clamp(key, cur - STEP, base)
            direction = "loosen"
        elif s["wrong_rate"] <= TIGHTEN_WRONG_RATE and s["net_pnl"] < 0 and cur < base:
            new = _clamp(key, min(cur + STEP, base), base)
            direction = "tighten"
        else:
            continue
        if abs(new - cur) < 1e-9:
            continue
        return {"key": key, "from": cur, "to": new, "direction": direction,
                "limiting": limiting, "stats": dict(s)}
    return None


# ---------------- Hook: Block -> Schattentrade ----------------
def _shadow_allowed(ai_cfg: Dict, signal: Dict, guard: str) -> bool:
    if not ai_cfg.get("guard_shadow_enabled", DEFAULTS["guard_shadow_enabled"]):
        return False
    if guard not in SHADOW_GUARDS or signal.get("data_collection"):
        return False
    key = f"{signal.get('symbol')}:{signal.get('type')}:{guard}"
    last = _last_shadow_ts.get(key, 0.0)
    if time.time() - last < SHADOW_COOLDOWN_S:
        return False
    return True


async def maybe_open_shadow(mgr, signal: Dict, candles: List[Dict], guard: str,
                            reason: str, snapshot: Optional[Dict] = None,
                            blocked_mode: str = "live") -> Optional[Dict]:
    """Geblockten LIVE-Einstieg als Paper-Schattentrade nachspielen.
    Läuft durch denselben Einstiegspfad (Sammel-Welt), nur der blockende
    Wächter wird per `_shadow_bypass` übersprungen. Fail-safe: nie Exception."""
    try:
        if str(signal.get("strategy_id") or "") != "ai_trader":
            return None
        ai_cfg = await mgr.db.settings.find_one({"_id": "ai_trader_config"}) or {}
        if not _shadow_allowed(ai_cfg, signal, guard):
            return None
        n_open = await mgr.db.auto_trades.count_documents(
            {"status": "open", "guard_shadow.guard": {"$exists": True}})
        if n_open >= MAX_OPEN_SHADOWS:
            return None
        shadow = dict(signal)
        shadow["data_collection"] = True
        shadow["collection_reason"] = f"guard_shadow:{guard}"
        shadow["guard_shadow"] = {"guard": guard, "reason": str(reason)[:300],
                                  "snapshot": snapshot or {}, "blocked_mode": blocked_mode,
                                  "blocked_at": _now_iso()}
        shadow["_shadow_bypass"] = [guard]
        shadow.pop("_reject_reason", None)
        _last_shadow_ts[f"{signal.get('symbol')}:{signal.get('type')}:{guard}"] = time.time()
        trade = await mgr._on_signal_impl(shadow, candles)
        if trade:
            logger.info(f"Guard-Shadow: {signal.get('symbol')} {signal.get('type')} "
                        f"als Schattentrade nachgespielt ({guard})")
            signal["_shadow_trade_id"] = trade.get("id")
        return trade
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Guard-Shadow fehlgeschlagen: {e}")
        return None


# ---------------- Hook: Close -> Urteil -> Kalibrierung ----------------
async def on_trade_closed(db, t: Dict) -> Optional[Dict]:
    gs = t.get("guard_shadow")
    if not isinstance(gs, dict) or not gs.get("guard"):
        return None
    try:
        pnl = float(t.get("realized_pnl") or 0.0)
        cap = float(t.get("max_capital") or 0.0)
        row = {"id": str(uuid.uuid4()), "ts": _now_iso(), "trade_id": t.get("id"),
               "guard": gs["guard"], "limiting": limiting_factor(gs.get("reason")),
               "symbol": t.get("symbol"), "side": t.get("side"),
               "setup": t.get("ai_setup") or t.get("setup"),
               "realized_pnl": round(pnl, 6),
               "pnl_pct": round(pnl / cap * 100, 3) if cap else None,
               "result": t.get("result"), "verdict": verdict(pnl),
               "snapshot": gs.get("snapshot") or {}, "reason": gs.get("reason"),
               "consumed": False}
        await db.guard_shadow_reviews.insert_one(dict(row))
        await db.guard_shadow_reviews.delete_many(
            {"ts": {"$lt": (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()}})
        await calibrate(db)
        return row
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Guard-Shadow Review fehlgeschlagen: {e}")
        return None


async def _state(db) -> Dict:
    return await db.settings.find_one({"_id": STATE_ID}) or {"_id": STATE_ID}


async def calibrate(db) -> Optional[Dict]:
    """Autonome Nachjustierung des Fee-Wächters aus den Schatten-Urteilen.
    Nur unverbrauchte Urteile der letzten 14 Tage; max. 1 Schritt je 12 h."""
    from services.ai_engine import ai_engine
    cfg = ai_engine.config or {}
    if not cfg.get("guard_autotune_enabled", DEFAULTS["guard_autotune_enabled"]):
        return None
    state = await _state(db)
    last = state.get("last_adjust_ts")
    if last:
        try:
            age_h = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() / 3600
            if age_h < ADJUST_MIN_INTERVAL_H:
                return None
        except ValueError:
            pass
    if not state.get("baseline"):
        state["baseline"] = {k: float(cfg.get(k, 2.5) or 2.5) for k in BOUNDS}
        await db.settings.update_one({"_id": STATE_ID}, {"$set": {"baseline": state["baseline"]}},
                                     upsert=True)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=REVIEW_WINDOW_DAYS)).isoformat()
    rows = await db.guard_shadow_reviews.find(
        {"ts": {"$gte": cutoff}, "consumed": {"$ne": True}, "guard": "fee_guard"},
        {"_id": 0}).to_list(2000)
    min_samples = int(cfg.get("guard_autotune_min_samples", DEFAULTS["guard_autotune_min_samples"]) or 12)
    plan = plan_adjustment(aggregate(rows), cfg, state, min_samples)
    if not plan:
        return None
    await ai_engine.update_config({plan["key"]: plan["to"]})
    used_ids = [r["id"] for r in rows if r.get("limiting") == plan["limiting"]]
    await db.guard_shadow_reviews.update_many({"id": {"$in": used_ids}}, {"$set": {"consumed": True}})
    await db.settings.update_one({"_id": STATE_ID}, {"$set": {"last_adjust_ts": _now_iso()}},
                                 upsert=True)
    s = plan["stats"]
    text = (f"Fee-Wächter autonom {'gelockert' if plan['direction'] == 'loosen' else 'verschärft'}: "
            f"{plan['key']} {plan['from']:g}× → {plan['to']:g}× – Grundlage {s['n']} Schattentrades, "
            f"{round((s.get('wrong_rate') or 0) * 100)} % der Blocks waren falsch, "
            f"Netto {s['net_pnl']:+.2f} USDT")
    entry = {"id": str(uuid.uuid4()), "ts": _now_iso(), **plan, "text": text}
    await db.guard_calibration_log.insert_one(dict(entry))
    try:
        await db.ai_chat.insert_one({"id": str(uuid.uuid4()), "role": "governance",
                                     "text": text, "ts": _now_iso()})
    except Exception:  # noqa: BLE001
        pass
    logger.info(f"Guard-Autotune: {text}")
    entry.pop("_id", None)
    return entry


# ---------------- Statistik für die UI ----------------
async def stats(db, days: int = 14) -> Dict:
    days = max(1, min(90, int(days)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.guard_shadow_reviews.find({"ts": {"$gte": cutoff}}, {"_id": 0}) \
        .sort("ts", -1).to_list(2000)
    open_n = await db.auto_trades.count_documents(
        {"status": "open", "guard_shadow.guard": {"$exists": True}})
    log = await db.guard_calibration_log.find({}, {"_id": 0}).sort("ts", -1).to_list(10)
    state = await _state(db)
    agg = aggregate(rows)
    guards = []
    for key, s in sorted(agg.items()):
        if ":" in key:
            continue
        guards.append({"key": key, "label": GUARD_LABELS.get(key, key), **s})
    return {"days": days, "open_shadows": open_n, "closed": len(rows), "guards": guards,
            "fee_guard_detail": {k.split(":", 1)[1]: v for k, v in agg.items()
                                 if k.startswith("fee_guard:")},
            "recent": rows[:10], "adjustments": log,
            "baseline": state.get("baseline"), "last_adjust_ts": state.get("last_adjust_ts")}
