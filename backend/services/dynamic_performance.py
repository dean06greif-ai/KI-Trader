"""Ergebnis je Regime für gehandelte dynamische Strategien (Live/Paper) neben
den Walk-Forward-Zahlen der zugrunde liegenden Regime-Analyse.

Quelle Live: geschlossene `auto_trades` der dynamischen Strategie – jeder Trade
trägt das Regime seiner Entstehung in `trade.dynamic.regime` (bitunix_trade).
Quelle Walk-Forward: `regime_analyses.walkforward[scope_key].per_regime`.
"""
from typing import Dict, List, Optional

MIN_TRADES_COMPARE = 5   # erst ab so vielen Live-Trades wird bewertet


def _metrics(rows: List[Dict]) -> Dict:
    pnl = [float(r.get("realized_pnl") or 0) for r in rows]
    wins = sum(1 for r, p in zip(rows, pnl)
               if r.get("result") == "win" or (r.get("result") is None and p > 0))
    n = len(rows)
    return {"trades": n, "wins": wins, "win_rate": round(wins / n * 100, 1) if n else 0.0,
            "pnl": round(sum(pnl), 2), "avg_pnl": round(sum(pnl) / n, 4) if n else 0.0,
            "fees": round(sum(float(r.get("fees_paid") or 0) for r in rows), 2)}


def _verdict(live: Dict, wf: Optional[Dict]) -> Dict:
    if live["trades"] < MIN_TRADES_COMPARE:
        return {"status": "few", "text": f"zu wenig Live-Trades ({live['trades']}) für eine Aussage"}
    if not wf or not int(wf.get("trades") or 0):
        return {"status": "no_wf", "text": "kein Walk-Forward-Vergleich vorhanden"}
    wf_avg = float(wf.get("pnl") or 0) / max(int(wf.get("trades") or 0), 1)
    if live["pnl"] < 0 <= wf_avg:
        return {"status": "worse", "text": "live negativ, obwohl der Walk-Forward positiv war – Regime beobachten/abschalten"}
    if live["pnl"] < 0:
        return {"status": "negative", "text": "live und im Walk-Forward negativ – nicht handeln"}
    if live["avg_pnl"] < wf_avg * 0.5:
        return {"status": "weaker", "text": "positiv, aber deutlich schwächer als im Walk-Forward"}
    return {"status": "ok", "text": "entspricht dem Walk-Forward oder besser"}


def aggregate(trades: List[Dict], regimes: List[Dict], wf_per_regime: Optional[List[Dict]]) -> Dict:
    """Live-Kennzahlen je Regime + Walk-Forward je Regime + Bewertung (rein)."""
    by_rid: Dict[Optional[int], List[Dict]] = {}
    for t in trades:
        rid = (t.get("dynamic") or {}).get("regime")
        by_rid.setdefault(int(rid) if rid is not None else None, []).append(t)
    wf_of = {int(r["regime"]): (r.get("metrics") or {}) for r in (wf_per_regime or [])
             if r.get("regime") is not None}
    out = []
    for r in regimes:
        rid = int(r["id"])
        live = _metrics(by_rid.get(rid, []))
        wf = wf_of.get(rid)
        out.append({"regime": rid, "label": r.get("label"), "traded": r.get("traded", True),
                    "strategy_name": r.get("strategy_name"), "live": live,
                    "walkforward": ({k: wf.get(k) for k in ("trades", "win_rate", "pnl")} if wf else None),
                    "verdict": _verdict(live, wf)})
    unknown = by_rid.get(None) or []
    return {"regimes": out, "total": _metrics(trades),
            "unknown_regime_trades": len(unknown), "has_walkforward": bool(wf_of)}


async def regime_performance(db, doc: Dict, regimes: List[Dict], mode: str = "all") -> Dict:
    q: Dict = {"strategy_id": doc["id"], "status": "closed"}
    if mode in ("live", "paper"):
        q["mode"] = mode
    trades = await db.auto_trades.find(
        q, {"_id": 0, "dynamic": 1, "realized_pnl": 1, "result": 1, "fees_paid": 1}).to_list(20000)
    s = doc.get("settings") or {}
    wf = None
    if s.get("analysis_id"):
        a = await db.regime_analyses.find_one({"id": s["analysis_id"]}, {"walkforward": 1})
        wf = ((a or {}).get("walkforward") or {}).get(s.get("scope_key") or "combined") or {}
    res = aggregate(trades, regimes, (wf or {}).get("per_regime"))
    res.update({"id": doc["id"], "mode": mode,
                "walkforward_created_at": (wf or {}).get("created_at"),
                "skipped_regimes": s.get("skipped_regimes") or []})
    return res
