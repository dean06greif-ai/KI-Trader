"""Robustheits-Tests im Backtester – dieselben Prüfungen wie im Strategie-Optimizer
(services/robustness.py), aber rein aus den Trades des Backtests (keine erneute
Simulation, kein Kerzen-Nachladen):

  * Walk-Forward: Zeitraum in Trainings-/Testteil (single) bzw. N Fenster
    (rolling/anchored) teilen -> läuft die Strategie in beiden Teilen ähnlich?
  * Drawdown-Filter, Konstanz-Test, Monte-Carlo (wie im Optimizer)
  * Kosten-Stresstest: PnL − (Faktor−1) × Gebühren je Trade (exakt für die
    proportionale Gebühr des Simulators)
  * Marktphasen (Bull/Bär/Seitwärts) gesamt und je Asset, Multi-Coin-Check,
    Ausreißer-Assets (services/optimizer_outliers) und Ausreißer-Dominanz
    (services/regime_outliers)
  * Parameter-Stabilität braucht neue Simulationen -> nur im Optimizer

Ergebnis je Strategie im Format eines Optimizer-Top-Ergebnisses (checks,
fail_reasons, recommendation), damit die UI dieselben Bausteine nutzt.
"""
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import optimizer_outliers, regime_outliers, robustness
from services.dynamic_strategy import metrics_from_rows

PHASES = ("bull", "bear", "sideways")


def _ts(iso: Optional[str]) -> Optional[float]:
    try:
        return datetime.fromisoformat(iso).timestamp() * 1000 if iso else None
    except ValueError:
        return None


def _rows(trades: List[Dict]) -> List[Dict]:
    out = []
    for t in trades:
        ts = _ts(t.get("closed"))
        if ts is not None:
            out.append({**t, "_ts": ts})
    out.sort(key=lambda r: r["_ts"])
    return out


def _between(rows: List[Dict], a: float, b: float) -> List[Dict]:
    return [r for r in rows if a <= r["_ts"] < b]


def _walk_forward(rows, robust, start, end, capital) -> Dict:
    day = 86400000.0
    span = max(end - start, day)
    if robust["wf_mode"] == "single":
        cut = start + span * robust["train_pct"] / 100.0
        tr = metrics_from_rows(_between(rows, start, cut), capital)
        te = metrics_from_rows(_between(rows, cut, end + 1), capital)
        return {"test_metrics": te, "train_metrics": tr,
                "wf": robustness.walk_forward_eval(tr, te, (cut - start) / day, (end - cut) / day)}
    n = robust["wf_windows"]
    part = span / (n + 1)
    wins = []
    for w in range(n):
        a = start + (0 if robust["wf_mode"] == "anchored" else w * part)
        b = start + (w + 1) * part
        tr = metrics_from_rows(_between(rows, a, b), capital)
        te = metrics_from_rows(_between(rows, b, b + part + (1 if w == n - 1 else 0)), capital)
        ev = robustness.walk_forward_eval(tr, te, (b - a) / day, part / day)
        wins.append({"window": w + 1, "train_metrics": tr, "test_metrics": te,
                     "range": {"train_from": datetime.fromtimestamp(a / 1000, timezone.utc).date().isoformat(),
                               "test_to": datetime.fromtimestamp((b + part) / 1000, timezone.utc).date().isoformat()},
                     **ev})
    return {"wf_windows": wins, "wf": robustness.aggregate_rolling(wins),
            "test_metrics": robustness.combine_test_metrics([w["test_metrics"] for w in wins])}


def _phases(rows: List[Dict]) -> Optional[Dict]:
    if not any(r.get("market_phase") for r in rows):
        return None
    agg = {k: {"pnl": 0.0, "trades": 0, "wins": 0} for k in PHASES}
    for r in rows:
        ph = r.get("market_phase")
        if ph in agg:
            p = float(r.get("pnl") or 0)
            agg[ph]["pnl"] += p
            agg[ph]["trades"] += 1
            agg[ph]["wins"] += 1 if p > 1e-6 else 0
    for v in agg.values():
        wins = v.pop("wins")
        v["pnl"] = round(v["pnl"], 2)
        v["win_rate"] = round(wins / v["trades"] * 100, 1) if v["trades"] else 0.0
    return agg


def _chunks(rows, start, end, chunk_days) -> List[float]:
    ms = chunk_days * 86400000
    n = max(1, math.ceil((end - start + 1) / ms))
    out = [0.0] * n
    for r in rows:
        out[min(max(int((r["_ts"] - start) // ms), 0), n - 1)] += float(r.get("pnl") or 0)
    return out


def evaluate_strategy(trades: List[Dict], robust: Dict, capital: float,
                      start: float, end: float) -> Dict:
    rows = _rows(trades)
    m = metrics_from_rows(rows, capital)
    entry: Dict = {"metrics": m, "trades": len(rows)}
    passed = True
    if robust["wf_enabled"]:
        entry.update(_walk_forward(rows, robust, start, end, capital))
    if robust["dd_enabled"]:
        ok, ratio = robustness.dd_check(m, robust["dd_max_pct"])
        entry["dd_pass"], entry["dd_ratio_pct"] = ok, ratio
        if entry.get("test_metrics") is not None:
            ok_t, ratio_t = robustness.dd_check(entry["test_metrics"], robust["dd_max_pct"])
            entry["wf"]["test_dd_ratio_pct"] = ratio_t
            entry["dd_pass"] = ok and ok_t
        passed = passed and entry["dd_pass"]
    if robust["ct_enabled"]:
        entry["constancy"] = robustness.evaluate_chunks(
            _chunks(rows, start, end, robust["ct_chunk_days"]), robust["ct_max_dev_pct"])
        passed = passed and entry["constancy"]["passed"]
    if robust["st_enabled"]:
        extra = robust["st_mult"] - 1.0
        st_pnl = round(sum(float(r.get("pnl") or 0) - extra * float(r.get("fees") or 0) for r in rows), 2)
        entry["stress"] = {"cost_multiplier": robust["st_mult"], "pnl": st_pnl,
                           "trades": len(rows), "win_rate": m.get("win_rate"), "passed": st_pnl > 0}
        passed = passed and entry["stress"]["passed"]
    if robust["mc_enabled"]:
        entry["monte_carlo"] = robustness.monte_carlo(
            [float(r.get("pnl") or 0) for r in rows], robust["mc_runs"], robust["mc_max_dd_pct"])
        passed = passed and entry["monte_carlo"]["passed"]
    if robust["rg_enabled"]:
        entry["regimes"] = _phases(rows)
    # Multi-Coin-Check + Ausreißer-Assets (Assets sind im Backtest unabhängig ->
    # "ohne Ausreißer" ist exakt die Summe der übrigen Assets)
    by_sym: Dict[str, List[Dict]] = {}
    for r in rows:
        by_sym.setdefault(r.get("symbol") or "?", []).append(r)
    if len(by_sym) > 1:
        per = {}
        for sym, rs in sorted(by_sym.items()):
            sm = metrics_from_rows(rs, capital)
            per[sym] = {k: sm.get(k) for k in ("pnl", "trades", "win_rate", "max_drawdown", "fees")}
            if robust["rg_enabled"]:
                per[sym]["regimes"] = _phases(rs)
            if entry.get("test_metrics") is not None and robust["wf_mode"] == "single":
                cut = start + max(end - start, 86400000.0) * robust["train_pct"] / 100.0
                per[sym]["test"] = metrics_from_rows(_between(rs, cut, end + 1), capital)
        entry["per_symbol"] = per
        entry["positive_symbols_pct"] = round(
            sum(1 for v in per.values() if (v["pnl"] or 0) > 0) / len(per) * 100, 1)
        ol = optimizer_outliers.detect(per)
        if ol["outliers"]:
            kept = [r for r in rows if r.get("symbol") in set(ol["kept"])]
            km = metrics_from_rows(kept, capital)
            entry["outlier_variant"] = {"excluded": ol["outliers"], "reasons": ol["reasons"],
                                        "kept": ol["kept"], "metrics": km, "test_metrics": None,
                                        "pnl_gain": round(km["pnl"] - m["pnl"], 2)}
    dom = regime_outliers.dominance([r.get("pnl") for r in rows])
    entry["dominance"] = dom
    entry["passed"] = passed
    entry["checks"] = robustness.build_checks_summary(entry, {**robust, "sb_enabled": False})
    if dom["dominated"]:
        entry["checks"].append({"id": "dominance", "label": "Ausreißer-Dominanz", "enabled": True,
                                "passed": False, "value": dom["pnl_ex_top"], "is_filter": False,
                                "detail": f"Ohne die 2 besten Trades PnL {dom['pnl_ex_top']:.2f} – "
                                          f"Gewinn hängt an Einzeltreffern (nur Info)."})
    entry["fail_reasons"] = robustness.fail_reasons(entry["checks"])
    entry["recommendation"] = optimizer_outliers.recommendation(entry)
    return entry


def evaluate(export_trades: List[Dict], body: Dict, capital: float, start_ms: float,
             end_ms: float, truncated: bool = False) -> Dict:
    """{strategy_id: Bericht} + Konfiguration der aktiven Tests."""
    robust = robustness.parse_config(body or {})
    by_sid: Dict[str, List[Dict]] = {}
    names: Dict[str, str] = {}
    for t in export_trades or []:
        sid = t.get("strategy_id")
        by_sid.setdefault(sid, []).append(t)
        names[sid] = t.get("strategy_name") or sid
    out = {}
    for sid, ts in by_sid.items():
        out[sid] = {"strategy_name": names[sid],
                    **evaluate_strategy(ts, robust, capital, float(start_ms), float(end_ms))}
    return {"config": {k: v for k, v in robust.items() if k != "any"}, "per_strategy": out,
            "truncated": truncated,
            "note": "Rein aus den Backtest-Trades berechnet (keine Neu-Simulation). "
                    "Parameter-Stabilität gibt es nur im Optimizer."}
