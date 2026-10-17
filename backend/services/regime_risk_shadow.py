"""Regime-Risiko im Shadow-Modus: „Wie viel Einsatz hätte der KI Trader je
Struktur-Regime genommen – und hätte das geholfen?“

Reine Beobachtung, KEINE Wirkung auf Orders/Größen/Prompt. Je geschlossenem
KI-Trade mit bekanntem Struktur-Regime (ab Freigabe-Stufe shadow in ai_rewards
erfasst) wird ein Einsatz-Faktor bestimmt – nur aus Trades, die VOR dem Entry
geschlossen waren (kein Lookahead). Die Bilanz vergleicht den echten PnL mit
dem skalierten PnL. Erst wenn diese Bilanz über viele Trades trägt, ist ein
echter Einsatz je Regime (Stufe „Wirksam“) begründet.
"""
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

MIN_TRADES = 15            # je Regime, bevor ein Faktor ≠ 1 vorgeschlagen wird
FACTORS = ((-0.25, 0.5), (0.0, 0.75), (0.25, 1.0))   # Ø-Reward-Grenze -> Faktor
BOOST = 1.25               # Ø-Reward ≥ +0.25 R
MAX_ROWS = 5000
VERDICT_MIN_SCALED = 20    # so viele skalierte Trades, bevor ein Urteil fällt


def factor_for(n: int, avg_reward: Optional[float], min_trades: int = MIN_TRADES) -> float:
    """Einsatz-Faktor aus Stichprobe + Ø-Reward des Regimes (rein, konservativ)."""
    if n < min_trades or avg_reward is None:
        return 1.0
    for limit, f in FACTORS:
        if avg_reward < limit:
            return f
    return BOOST


def simulate(trades: List[Dict], min_trades: int = MIN_TRADES) -> Dict:
    """trades: {regime, reward, pnl, opened_at, closed_at} (ISO). Walk-forward ohne
    Lookahead: Statistik je Regime nur aus Trades mit closed_at < opened_at."""
    rows = sorted(trades, key=lambda t: str(t.get("opened_at") or ""))
    closed = sorted(rows, key=lambda t: str(t.get("closed_at") or ""))
    stats: Dict[str, Dict] = {}
    ci = 0
    actual = shadow = 0.0
    scaled = 0
    per: Dict[str, Dict] = {}
    for t in rows:
        while ci < len(closed) and str(closed[ci].get("closed_at") or "") < str(t.get("opened_at") or ""):
            c = closed[ci]
            if c.get("regime"):
                s = stats.setdefault(c["regime"], {"n": 0, "sum": 0.0})
                s["n"] += 1
                s["sum"] += float(c.get("reward") or 0)
            ci += 1
        reg = t.get("regime")
        s = stats.get(reg) if reg else None
        f = factor_for(s["n"], s["sum"] / s["n"], min_trades) if s else 1.0
        pnl = float(t.get("pnl") or 0)
        actual += pnl
        shadow += pnl * f
        scaled += f != 1.0
        p = per.setdefault(reg or "unbekannt", {"regime": reg or "unbekannt", "trades": 0, "pnl": 0.0,
                                                "shadow_pnl": 0.0, "reward_sum": 0.0})
        p["trades"] += 1
        p["pnl"] += pnl
        p["shadow_pnl"] += pnl * f
        p["reward_sum"] += float(t.get("reward") or 0)
    out_rows = []
    for p in per.values():
        n = p["trades"]
        avg = p["reward_sum"] / n if n else None
        out_rows.append({"regime": p["regime"], "trades": n, "avg_reward": round(avg, 3) if avg is not None else None,
                         "pnl": round(p["pnl"], 2), "shadow_pnl": round(p["shadow_pnl"], 2),
                         "factor_now": factor_for(n, avg, min_trades) if p["regime"] != "unbekannt" else 1.0})
    out_rows.sort(key=lambda r: -(r["trades"]))
    delta = round(shadow - actual, 2)
    return {"trades": len(rows), "scaled_trades": scaled, "pnl": round(actual, 2),
            "shadow_pnl": round(shadow, 2), "delta": delta, "rows": out_rows,
            "verdict": verdict(scaled, delta)}


def verdict(scaled: int, delta: float) -> str:
    if scaled < VERDICT_MIN_SCALED:
        return "sammelt"
    return "würde helfen" if delta > 0 else "würde schaden" if delta < 0 else "neutral"


async def report(db, days: int = 90) -> Dict:
    """Shadow-Bilanz über ai_rewards (+ Entry-Zeit aus auto_trades)."""
    days = max(7, min(365, int(days)))
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rewards = await db.ai_rewards.find(
        {"ts": {"$gte": cutoff}}, {"_id": 0, "trade_id": 1, "structural_regime": 1, "score": 1,
                                   "pnl": 1, "ts": 1, "mode": 1}).to_list(MAX_ROWS)
    ids = [r["trade_id"] for r in rewards if r.get("trade_id")]
    opened = {t["id"]: t.get("opened_at") async for t in db.auto_trades.find(
        {"id": {"$in": ids}, "data_collection": {"$ne": True}}, {"_id": 0, "id": 1, "opened_at": 1})}
    trades = [{"regime": r.get("structural_regime") if r.get("structural_regime") not in (None, "", "unbekannt")
               else None, "reward": r.get("score"), "pnl": r.get("pnl"),
               "opened_at": opened[r["trade_id"]], "closed_at": r.get("ts")}
              for r in rewards if r.get("trade_id") in opened]
    res = simulate(trades)
    res.update({"days": days, "min_trades": MIN_TRADES,
                "factors": {"< -0.25 R": 0.5, "< 0 R": 0.75, "< +0.25 R": 1.0, "≥ +0.25 R": BOOST},
                "note": "Nur Beobachtung – Orders und Positionsgrößen bleiben unverändert."})
    return res
