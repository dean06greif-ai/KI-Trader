"""Regime-Nutzen auf dem Walk-Forward-Holdout (Analyse 03.11, Punkte 3 + 5).

Eine dynamische Strategie lohnt sich nur, wenn JEDES gehandelte Regime auf
unbekannten Daten genug Trades (>= MIN_TRADES) UND einen positiven
Erwartungswert zeigt. Die Spreizung (bestes − schwächstes Regime, Ø PnL/Trade)
wird als Nutzen-Kennzahl mitgeliefert. Zusätzlich Struktur-Hinweise zur
Regime-Taxonomie (9er-Modell / kurze Zeiteinheit flackern). Rein, ohne DB.
"""
from typing import Dict, List, Optional

MIN_TRADES = 30
FAST_TFS = ("1m", "3m", "5m", "15m", "30m", "1h")


def evaluate(per_regime: Optional[List[Dict]], min_trades: int = MIN_TRADES) -> Dict:
    rows = []
    for p in per_regime or []:
        m = p.get("metrics") or {}
        n = int(m.get("trades") or 0)
        if n <= 0:
            continue
        rows.append({"regime": p.get("regime"), "label": p.get("label") or f"#{p.get('regime')}",
                     "trades": n, "avg_pnl": round(float(m.get("avg_pnl") or 0), 4),
                     "enough": n >= min_trades})
    reasons = []
    if not rows:
        reasons.append("keine Holdout-Trades je Regime")
    thin = [r for r in rows if not r["enough"]]
    if thin:
        reasons.append(f"zu wenige Holdout-Trades (< {min_trades}) in: "
                       + ", ".join(f"{r['label']} ({r['trades']})" for r in thin))
    neg = [r for r in rows if r["avg_pnl"] <= 0]
    if neg:
        reasons.append("kein positiver Erwartungswert in: "
                       + ", ".join(f"{r['label']} (Ø {r['avg_pnl']:+.3f})" for r in neg))
    avgs = [r["avg_pnl"] for r in rows]
    spread = round(max(avgs) - min(avgs), 4) if len(avgs) >= 2 else None
    return {"ok": bool(rows) and not reasons, "min_trades": min_trades,
            "spread_avg_pnl": spread, "regimes": rows, "reasons": reasons}


def structure_warnings(doc: Dict) -> List[str]:
    model = doc.get("model") or {}
    mode = model.get("regime_mode")
    tf = str(model.get("timeframe") or doc.get("timeframe") or "")
    out = []
    if mode == 9:
        out.append("9-Regime-Modell: benachbarte Vola-Stufen wechseln oft – 5er-Modell "
                   "(Trend × Stärke) empfohlen, Vola besser als Größen-Faktor")
    if tf in FAST_TFS:
        out.append(f"Regime auf {tf} bestimmt – wechselt zu oft für eine Strategie-Bindung; "
                   "Struktur-Regime auf 4h/1d empfohlen (gehandelt wird weiter auf der kleineren Zeiteinheit)")
    return out
