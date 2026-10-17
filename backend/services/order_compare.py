"""Market vs. Limit: Vergleich zweier Backtest-Läufe mit denselben Signalen (rein).

Empfehlung bewusst konservativ: der Backtest-Fill ("Kerze berührt das Limit")
ist optimistisch (keine Orderbuch-Warteschlange, keine Teil-Fills) – Limit wird
nur empfohlen, wenn es klar besser ist UND genug Limits wirklich füllen.
"""
from typing import Dict, List, Optional

FILL_MIN = 0.6          # Mindest-Fill-Quote (ohne Market-Fallback) für "Limit"
MARGIN_REL = 0.10       # Limit muss >= 10 % des Market-PnL besser sein ...
MARGIN_CAP = 0.01       # ... mindestens aber 1 % des Kapitals


def _sum(rows: List[Dict]) -> Dict:
    s = {k: 0 for k in ("trades", "wins", "losses", "limit_expired", "limit_filled", "limit_fallback")}
    s.update(pnl=0.0, fees=0.0, max_drawdown=0.0)
    for r in rows:
        for k in ("trades", "wins", "losses", "limit_expired", "limit_filled", "limit_fallback"):
            s[k] += int(r.get(k) or 0)
        for k in ("pnl", "fees", "max_drawdown"):
            s[k] += float(r.get(k) or 0)
    d = s["wins"] + s["losses"]
    s["win_rate"] = round(s["wins"] / d * 100, 1) if d else 0.0
    for k in ("pnl", "fees", "max_drawdown"):
        s[k] = round(s[k], 2)
    tried = s["limit_filled"] + s["limit_expired"] + s["limit_fallback"]
    s["fill_rate"] = round(s["limit_filled"] / tried, 3) if tried else None
    return s


def recommend(m: Dict, lim: Dict, capital: float) -> Dict:
    margin = max(abs(m["pnl"]) * MARGIN_REL, capital * MARGIN_CAP)
    fill = lim.get("fill_rate")
    diff = round(lim["pnl"] - m["pnl"], 2)
    if m["trades"] and not lim["trades"]:
        return {"choice": "market", "diff": diff, "why": "Limit wird nie gefüllt – Market"}
    if diff > margin and (fill is None or fill >= FILL_MIN):
        return {"choice": "limit", "diff": diff,
                "why": f"Limit klar besser (+{diff:.2f} USDT, Fill-Quote {round((fill or 0) * 100)} %)"}
    if diff > margin:
        return {"choice": "market", "diff": diff,
                "why": (f"Limit nur auf dem Papier besser – nur {round(fill * 100)} % der Limits gefüllt "
                        "(Backtest-Fill ist optimistisch) → Market")}
    if -diff > margin:
        return {"choice": "market", "diff": diff,
                "why": f"Market besser ({diff:.2f} USDT mit Limit) – verpasste/späte Einstiege kosten mehr als die Gebühr spart"}
    return {"choice": "market", "diff": diff,
            "why": "gleichwertig (Unterschied unter Sicherheitsabstand) → Market (Ausführung sicher)"}


def build(market_pairs: List[Dict], limit_pairs: List[Dict], capital: float,
          fallback: bool = False) -> Dict[str, Dict]:
    out: Dict[str, Dict] = {}
    sids = {r["strategy_id"] for r in market_pairs} | {r["strategy_id"] for r in limit_pairs}
    for sid in sorted(sids):
        mr = [r for r in market_pairs if r["strategy_id"] == sid]
        lr = [r for r in limit_pairs if r["strategy_id"] == sid]
        m, lim = _sum(mr), _sum(lr)
        name: Optional[str] = (mr or lr)[0].get("strategy_name")
        out[sid] = {"strategy_name": name, "market": m, "limit": lim, "limit_fallback_market": fallback,
                    **recommend(m, lim, capital)}
    return out
