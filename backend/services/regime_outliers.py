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
    "few_test_trades": "zu wenige Trades im Walk-Forward (Out-of-Sample) – Ergebnis nicht belastbar",
    "overfit_gap": "Walk-Forward deutlich schwächer als Training – vermutlich überangepasst",
}
# Overfitting-Lücke: Ø-Gewinn je Trade im Walk-Forward < OVERFIT_KEEP × Training
OVERFIT_KEEP = 0.25
# Coin-Edge je Regime: ab so vielen Trades eines Assets gilt "kein Edge" als belegt
COIN_MIN_TRADES = 3


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
    if min_trades and v and int(v.get("trades") or 0) < test_min_trades(min_trades):
        flags.append("few_test_trades")
    if overfit_gap(m, v, min_trades):
        flags.append("overfit_gap")
    return flags


def test_min_trades(min_trades: int) -> int:
    """Mindest-Trades im Walk-Forward – identisch zu regime_opt/discover (40 %, mind. 3)."""
    return max(int((min_trades or 0) * 0.4), 3)


def overfit_gap(m: Optional[Dict], v: Optional[Dict], min_trades: int = 0) -> bool:
    """Training stark, Walk-Forward je Trade nur noch ein Bruchteil (rein).
    Ohne belastbaren Walk-Forward (zu wenige Trades) keine Aussage."""
    if not m or not v:
        return False
    mt, vt = int(m.get("trades") or 0), int(v.get("trades") or 0)
    if mt < max(min_trades, 1) or vt < test_min_trades(min_trades):
        return False
    m_avg = float(m.get("pnl") or 0) / mt
    v_avg = float(v.get("pnl") or 0) / vt
    return m_avg > 0 and v_avg < m_avg * OVERFIT_KEEP


def trade_sufficient(entry: Optional[Dict], min_trades: int) -> bool:
    """Genug Trades im Training UND (falls vorhanden) im Walk-Forward (rein)."""
    if not entry:
        return False
    m, v = entry.get("metrics") or {}, entry.get("validation")
    if int(m.get("trades") or 0) < max(min_trades, 1):
        return False
    return v is None or int((v or {}).get("trades") or 0) >= test_min_trades(min_trades)


def coin_edge_report(train_ps: Optional[Dict[str, Dict]], test_ps: Optional[Dict[str, Dict]] = None,
                     min_trades: int = COIN_MIN_TRADES) -> Optional[Dict]:
    """Coins eines Regime-Kandidaten: Ausreißer (optimizer_outliers.detect, wie im
    Optimizer) + Coins ohne Edge (Training ODER Walk-Forward belegt <= 0).
    per_symbol-Format: {sym: {pnl, trades, win_rate}}. None ohne Daten (rein)."""
    train_ps = train_ps or {}
    test_ps = test_ps or {}
    syms = sorted(set(train_ps) | set(test_ps))
    if not syms:
        return None
    rows, no_edge, edge = [], [], []
    for s in syms:
        tr, te = train_ps.get(s) or {}, test_ps.get(s) or {}
        n_tr, n_te = int(tr.get("trades") or 0), int(te.get("trades") or 0)
        p_tr, p_te = float(tr.get("pnl") or 0), float(te.get("pnl") or 0)
        bad = (n_tr >= min_trades and p_tr <= 0) or (n_te >= min_trades and p_te <= 0)
        good = n_tr + n_te >= min_trades and p_tr > 0 and (n_te == 0 or p_te > 0)
        rows.append({"symbol": s, "train_pnl": round(p_tr, 2), "train_trades": n_tr,
                     "test_pnl": round(p_te, 2) if te else None, "test_trades": n_te if te else None,
                     "edge": "none" if bad else ("yes" if good else "unclear")})
        (no_edge if bad else edge if good else []).append(s)
    ol = optimizer_outliers.detect({s: {"pnl": (train_ps.get(s) or {}).get("pnl", 0) +
                                        (test_ps.get(s) or {}).get("pnl", 0),
                                        "trades": int((train_ps.get(s) or {}).get("trades") or 0) +
                                        int((test_ps.get(s) or {}).get("trades") or 0),
                                        "win_rate": (train_ps.get(s) or {}).get("win_rate", 0)}
                                    for s in syms})
    return {"symbols": rows, "no_edge": no_edge, "edge": edge,
            "outlier_assets": ol["outliers"], "outlier_reasons": ol["reasons"]}


def per_symbol_metrics(rows: List[Dict]) -> Dict[str, Dict]:
    """{sym: {pnl, trades, win_rate}} aus Trade-Zeilen mit 'symbol' (rein)."""
    per: Dict[str, Dict] = {}
    for r in rows:
        d = per.setdefault(r.get("symbol") or "?", {"pnl": 0.0, "trades": 0, "wins": 0, "losses": 0})
        p = float(r.get("pnl") or 0)
        d["pnl"] += p
        d["trades"] += 1
        d["wins"] += 1 if p > 1e-6 else 0
        d["losses"] += 1 if p < -1e-6 else 0
    out = {}
    for s, d in per.items():
        dec = d["wins"] + d["losses"]
        out[s] = {"pnl": round(d["pnl"], 2), "trades": d["trades"],
                  "win_rate": round(d["wins"] / dec * 100, 1) if dec else 0.0}
    return out


def robust_validated(entry: Optional[Dict], min_trades: int = 0) -> bool:
    """Walk-Forward bestanden UND kein harter Robustheits-Verstoß."""
    if not entry or not entry.get("validation_passed"):
        return False
    return not any(f in HARD_FLAGS for f in candidate_flags(entry, min_trades))


def flag_texts(flags: Iterable[str]) -> List[str]:
    return [FLAG_TEXT.get(f, f) for f in flags]


def regime_report(rows: List[Dict], min_trades: int = MIN_TRADES) -> Dict:
    """Ausreißer-Analyse der Trades EINES Regimes (Ergebnis-Backtest)."""
    per_symbol = per_symbol_metrics(rows)
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
