"""Ausreißer-/Robustheits-Filter für dynamische Strategien und die Regime-Erkennung
(rein & testbar).

Gleiche Grundidee wie services/optimizer_outliers.py (Ausreißer-Assets im
Optimizer), übertragen auf Regime:

  * Ausreißer-Assets je Regime (optimizer_outliers.detect auf den Regime-Trades)
  * Ausreißer-Dominanz: Gewinn existiert nur wegen der TOP_N besten Trades
  * zu wenige Trades für eine belastbare Aussage
  * Training/Walk-Forward-Widerspruch: Walk-Forward positiv, Training klar negativ
    (typischer Zufallstreffer – darf in der Suche nicht als "validiert" zählen)
  * Regime-Erkennung: Assets, auf denen die Erkennung deutlich schlechter trifft
    als auf den übrigen (robuster z-Wert wie im Optimizer)

Alle Funktionen sind additiv: fehlende Felder (z.B. alte gespeicherte Kandidaten
ohne `pnl_ex_top2`) führen nie zu einer Abwertung.
"""
from statistics import median
from typing import Dict, Iterable, List, Optional

from services import optimizer_outliers

TOP_N = 2
MIN_TRADES = 8          # ab so vielen Trades ist Dominanz/Ausreißer-Analyse belastbar
Z_MAX = 2.0
MIN_GAP_PCT = 10.0       # Erkennung: mind. so viele %-Punkte unter dem Median
MIN_SYMBOLS_DETECTION = 4
HARD_FLAGS = ("train_negative", "train_outlier_dominated", "test_outlier_dominated")

FLAG_TEXT = {
    "train_negative": "Walk-Forward positiv, aber Training negativ – Zufallstreffer",
    "train_outlier_dominated": "Training-Gewinn hängt an den 2 besten Trades",
    "test_outlier_dominated": "Walk-Forward-Gewinn hängt an den 2 besten Trades",
    "outlier_dominated": "Gewinn hängt an den 2 besten Trades",
    "outlier_assets": "einzelne Assets fallen klar aus dem Raster",
    "few_trades": "zu wenige Trades für eine belastbare Aussage",
}


def dominance(pnls: Iterable[float], top_n: int = TOP_N) -> Dict:
    """Wie stark hängt das Ergebnis an den besten Trades? (rein)"""
    vals = sorted((float(p or 0) for p in pnls), reverse=True)
    total = sum(vals)
    top = sum(v for v in vals[:top_n] if v > 0)
    gross = sum(v for v in vals if v > 0)
    ex = total - top
    return {"pnl_ex_top": round(ex, 2),
            "top_share_pct": round(top / gross * 100, 1) if gross > 0 else None,
            "dominated": len(vals) >= MIN_TRADES and total > 0 and ex <= 0}


def _dominated(m: Optional[Dict]) -> bool:
    if not m or m.get("pnl_ex_top2") is None:
        return False
    return (int(m.get("trades") or 0) >= MIN_TRADES and float(m.get("pnl") or 0) > 0
            and float(m["pnl_ex_top2"]) <= 0)


def candidate_flags(entry: Optional[Dict], min_trades: int = 0) -> List[str]:
    """Robustheits-Flags eines Regime-Kandidaten (Format regime_opt.top5 / assign)."""
    if not entry:
        return []
    m = entry.get("metrics") or {}
    v = entry.get("validation") or {}
    flags = []
    if entry.get("validation_passed") and m and float(m.get("pnl") or 0) < 0 \
            and int(m.get("trades") or 0) >= max(min_trades, 1):
        flags.append("train_negative")
    if _dominated(m):
        flags.append("train_outlier_dominated")
    if _dominated(v):
        flags.append("test_outlier_dominated")
    if min_trades and m and int(m.get("trades") or 0) < min_trades:
        flags.append("few_trades")
    return flags


def robust_validated(entry: Optional[Dict], min_trades: int = 0) -> bool:
    """Walk-Forward bestanden UND kein harter Robustheits-Verstoß."""
    if not entry or not entry.get("validation_passed"):
        return False
    return not any(f in HARD_FLAGS for f in candidate_flags(entry, min_trades))


def flag_texts(flags: Iterable[str]) -> List[str]:
    return [FLAG_TEXT.get(f, f) for f in flags]


def regime_report(rows: List[Dict], min_trades: int = MIN_TRADES) -> Dict:
    """Ausreißer-Analyse der Trades EINES Regimes (Ergebnis-Backtest)."""
    per: Dict[str, Dict] = {}
    for r in rows:
        sym = r.get("symbol") or "?"
        d = per.setdefault(sym, {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
        p = float(r.get("pnl") or 0)
        d["pnl"] += p
        d["trades"] += 1
        d["wins"] += 1 if p > 1e-6 else 0
        d["losses"] += 1 if p < -1e-6 else 0
    per_symbol = {}
    for s, d in per.items():
        dec = d["wins"] + d["losses"]
        per_symbol[s] = {"pnl": round(d["pnl"], 2), "trades": d["trades"],
                         "win_rate": round(d["wins"] / dec * 100, 1) if dec else 0.0}
    ol = optimizer_outliers.detect(per_symbol)
    dom = dominance([r.get("pnl") for r in rows])
    flags = []
    if len(rows) < min_trades:
        flags.append("few_trades")
    if dom["dominated"]:
        flags.append("outlier_dominated")
    if ol["outliers"]:
        flags.append("outlier_assets")
    return {"per_symbol": per_symbol, "outlier_assets": ol["outliers"],
            "outlier_reasons": ol["reasons"],
            "pnl_without_outliers": (round(sum(per_symbol[s]["pnl"] for s in ol["kept"]), 2)
                                     if ol["outliers"] else None),
            "dominance": dom, "flags": flags, "notes": flag_texts(flags)}


def adjust_recommendation(rec: Dict, report: Optional[Dict]) -> Dict:
    """'handeln' wird zu 'Vorsicht', wenn der Gewinn nur an Ausreißer-Trades hängt;
    Ausreißer-Assets werden als Hinweis angehängt (rein)."""
    if not report or not rec:
        return rec
    rec = dict(rec)
    if rec.get("action") == "trade" and "outlier_dominated" in report["flags"]:
        rec["action"] = "caution"
        rec["text"] = (f"nur mit Vorsicht – Gewinn hängt an den {TOP_N} besten Trades "
                       f"(ohne sie PnL {report['dominance']['pnl_ex_top']:.2f})")
    if report.get("outlier_assets") and rec.get("action") in ("trade", "caution"):
        names = ", ".join(s.replace("USDT", "") for s in report["outlier_assets"])
        rec["text"] = f"{rec.get('text')} · Ausreißer-Assets: {names}"
    return rec


def detection_outliers(rows: List[Dict]) -> Optional[Dict]:
    """Regime-Erkennung: Assets, auf denen die Erkennung klar schlechter trifft
    als auf den übrigen (rows = regime_quality._symbol_row). None = nichts auffällig."""
    keys = ("reference_holdout_f1_pct", "reference_holdout_pct", "holdout_direction_pct")
    key = next((k for k in keys if rows and all(r.get(k) is not None for r in rows)), None)
    if key is None or len(rows) < MIN_SYMBOLS_DETECTION:
        return None
    vals = {r["symbol"]: float(r[key]) for r in rows}
    max_out = max(1, int(len(vals) / 3))
    out, reasons = [], {}
    for sym in sorted(vals, key=vals.get):
        if len(out) >= max_out:
            break
        others = [v for s, v in vals.items() if s != sym and s not in out]
        med = median(others)
        mad = median([abs(v - med) for v in others]) * 1.4826
        z = (vals[sym] - med) / max(mad, 2.5)
        if z > -Z_MAX or med - vals[sym] < MIN_GAP_PCT:
            break
        out.append(sym)
        reasons[sym] = f"{vals[sym]:.1f}% vs. Median der übrigen {med:.1f}% (robuster z-Wert {z:.1f})"
    if not out:
        return None
    kept = [v for s, v in vals.items() if s not in out]
    return {"metric": key, "symbols": out, "reasons": reasons,
            "median_without": round(median(kept), 1), "median_all": round(median(vals.values()), 1)}
