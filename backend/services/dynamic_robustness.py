"""Robustheits-Checks für fertige dynamische Strategien (Werkbank-Ergebnis).

Gleiche Prüfungen wie im klassischen Optimizer (services.robustness), aber auf
der ZUSAMMENGESETZTEN dynamischen Strategie über den vollen Zeitraum – dort sind
sie aussagekräftig (Regimewechsel, alle Trades in echter Reihenfolge). Je Regime
werden sie nur als Info gezeigt (Abschnitte oft zu kurz für eigene Urteile).

Rein rechnerisch auf dem Ergebnis-Backtest (Trades = breakdown.points): KEINE
neue Simulation, KEIN Kerzen-Laden auf dem Server – egal ob Cloud oder Worker.
- Drawdown-Filter: max DD in % vom PnL (robustness.dd_check)
- Monte-Carlo:     Trade-Reihenfolge mischen -> DD-Verteilung (robustness.monte_carlo)
- Kosten-Stress:   PnL mit Gebühren x Faktor (aus den tatsächlichen Trade-Gebühren)
- Konstanz:        PnL je Zeitabschnitt gleichmäßig? (robustness.evaluate_chunks)
"""
import math
from datetime import datetime
from typing import Dict, List, Optional

from services import robustness

ROBUST_KEYS = ("dd_filter", "monte_carlo", "stress_test", "constancy")


def parse(body: Optional[Dict]) -> Optional[Dict]:
    """Konfiguration aus {dd_filter, monte_carlo, stress_test, constancy}; None = aus."""
    body = body or {}
    cfg = robustness.parse_config({k: body.get(k) for k in ROBUST_KEYS})
    if not (cfg["dd_enabled"] or cfg["mc_enabled"] or cfg["st_enabled"] or cfg["ct_enabled"]):
        return None
    return cfg


def _ts_ms(iso) -> Optional[float]:
    try:
        return datetime.fromisoformat(str(iso)).timestamp() * 1000
    except (TypeError, ValueError):
        return None


def chunk_pnls(points: List[Dict], chunk_days: int) -> List[float]:
    stamps = [(t, float(p.get("pnl") or 0)) for p in points
              if (t := _ts_ms(p.get("t"))) is not None]
    if not stamps:
        return []
    start, end = min(s for s, _ in stamps), max(s for s, _ in stamps)
    size = chunk_days * 86_400_000
    out = [0.0] * max(1, math.ceil((end - start + 1) / size))
    for ts, pnl in stamps:
        out[min(int((ts - start) // size), len(out) - 1)] += pnl
    return out


def stress(metrics: Dict, mult: float) -> Dict:
    pnl, fees = float(metrics.get("pnl") or 0), float(metrics.get("fees") or 0)
    s_pnl = round(pnl - fees * (mult - 1.0), 2)
    return {"cost_multiplier": mult, "pnl": s_pnl, "fees": round(fees * mult, 2), "passed": s_pnl > 0}


def _per_regime(breakdown: Dict, cfg: Dict) -> List[Dict]:
    out = []
    pts = breakdown.get("points") or []
    for r in breakdown.get("regimes") or []:
        m = r.get("metrics")
        if not r.get("traded") or not m:
            continue
        row = {"regime": r.get("regime"), "label": r.get("label"), "trades": m.get("trades")}
        if cfg["dd_enabled"]:
            row["dd_pass"], row["dd_ratio_pct"] = robustness.dd_check(m, cfg["dd_max_pct"])
        if cfg["mc_enabled"]:
            mc = robustness.monte_carlo([p.get("pnl") or 0 for p in pts if p.get("regime") == r.get("regime")],
                                        cfg["mc_runs"], cfg["mc_max_dd_pct"])
            row["mc_dd_p95"], row["mc_pass"] = mc["dd_p95"], mc["passed"]
        if cfg["st_enabled"]:
            st = stress(m, cfg["st_mult"])
            row["stress_pnl"], row["stress_pass"] = st["pnl"], st["passed"]
        out.append(row)
    return out


def evaluate(breakdown: Optional[Dict], cfg: Optional[Dict]) -> Optional[Dict]:
    """Checks auf dem Ergebnis-Backtest (rein). None, wenn aus oder kein Ergebnis."""
    if not cfg or not breakdown or breakdown.get("error"):
        return None
    total = breakdown.get("total") or {}
    pts = breakdown.get("points") or []
    checks: List[Dict] = []
    res: Dict = {}
    if cfg["dd_enabled"]:
        ok, ratio = robustness.dd_check(total, cfg["dd_max_pct"])
        res["drawdown"] = {"max_dd_pct": cfg["dd_max_pct"], "ratio_pct": ratio, "passed": ok}
        checks.append({"key": "dd", "label": "Drawdown-Filter", "passed": ok,
                       "detail": f"DD {ratio if ratio is not None else '–'} % vom PnL (max {cfg['dd_max_pct']:g} %)"})
    if cfg["mc_enabled"]:
        mc = robustness.monte_carlo([p.get("pnl") or 0 for p in pts], cfg["mc_runs"], cfg["mc_max_dd_pct"])
        res["monte_carlo"] = mc
        checks.append({"key": "mc", "label": "Monte-Carlo", "passed": mc["passed"],
                       "detail": f"{mc['runs']} Läufe · DD p95 {mc['dd_p95'] if mc['dd_p95'] is not None else '–'} "
                                 f"({mc['dd_p95_pct'] if mc['dd_p95_pct'] is not None else '–'} % vom PnL, max {cfg['mc_max_dd_pct']:g} %)"})
    if cfg["st_enabled"]:
        st = stress(total, cfg["st_mult"])
        res["stress"] = st
        checks.append({"key": "stress", "label": f"Kosten-Stresstest ×{cfg['st_mult']:g}", "passed": st["passed"],
                       "detail": f"PnL {st['pnl']} bei Gebühren {st['fees']}"})
    if cfg["ct_enabled"]:
        ct = robustness.evaluate_chunks(chunk_pnls(pts, cfg["ct_chunk_days"]), cfg["ct_max_dev_pct"])
        ct["chunk_days"] = cfg["ct_chunk_days"]
        res["constancy"] = ct
        checks.append({"key": "ct", "label": f"Konstanz ({cfg['ct_chunk_days']} Tage)", "passed": ct["passed"],
                       "detail": f"{ct['chunks']} Abschnitte · {ct['profitable_chunks_pct']} % profitabel · "
                                 f"Streuung {ct['deviation_pct'] if ct['deviation_pct'] is not None else '–'} % (max {cfg['ct_max_dev_pct']:g} %)"})
    res.update({"checks": checks, "passed": all(c["passed"] for c in checks),
                "per_regime": _per_regime(breakdown, cfg), "trades": len(pts)})
    return res
