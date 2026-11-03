"""Optimizer-gleiche Ergebnis-Karte für fertige dynamische Strategien (rein).

Gleicher Umfang wie eine Top-Ergebnis-Karte der Parameter-Optimierung
(Optimizer.js „TOP 5“): Empfehlung (Ampel), WF-Score + Übereinstimmung
Training↔Test, DD/PnL, Monte-Carlo, Kosten-Stress, Konstanz, Checks mit
Begründung, Training- und Test-Kennzahlen, PnL je Regime und je Coin,
Ausreißer-Variante (ohne Ausreißer-Coins) – berechnet NUR aus den Trades des
Ergebnis-Backtests (voller Zeitraum) und des Walk-Forward-Holdouts. Keine neue
Simulation, kein Kerzen-Laden (Cloud und lokaler Worker identisch).

Schwellen: wie im Optimizer (services/robustness.parse_config) – vom Nutzer
gesetzte Werte (p["robustness"]) gelten, sonst die Optimizer-Standards.
"""
from typing import Dict, List, Optional

from services import dynamic_robustness, optimizer_outliers, regime_outliers, robustness
from services.dynamic_strategy import metrics_from_rows

MC_RUNS = 500
STRESS_MULT = 1.5
CONSTANCY_DAYS = 15
MIN_TRADES = 30          # weniger Trades im Gesamtzeitraum: Aussage nicht belastbar
BREADTH_MIN_PCT = 50.0   # Anteil profitabler Coins (wie optimizer_outliers.recommendation)


def thresholds(body: Optional[Dict]) -> Dict:
    """Optimizer-Schwellen; vom Nutzer gesetzte Werte überschreiben (rein)."""
    body = body or {}
    cfg = robustness.parse_config({k: body.get(k) for k in dynamic_robustness.ROBUST_KEYS})
    ct = body.get("constancy") or {}
    st = body.get("stress_test") or {}
    mc = body.get("monte_carlo") or {}
    return {"dd_max_pct": cfg["dd_max_pct"],
            "mc_runs": cfg["mc_runs"] if mc.get("runs") else MC_RUNS,
            "mc_max_dd_pct": cfg["mc_max_dd_pct"],
            "st_mult": cfg["st_mult"] if st.get("cost_multiplier") else STRESS_MULT,
            "ct_chunk_days": cfg["ct_chunk_days"] if ct.get("chunk_days") else CONSTANCY_DAYS,
            "ct_max_dev_pct": cfg["ct_max_dev_pct"]}


def _rows(points: List[Dict]) -> List[Dict]:
    return [{"closed": p.get("t"), "pnl": p.get("pnl") or 0, "symbol": p.get("symbol"),
             "regime": p.get("regime")} for p in points or [] if p.get("t")]


def _days(points: List[Dict]) -> float:
    ts = [dynamic_robustness._ts_ms(p.get("t")) for p in points or []]
    ts = [t for t in ts if t is not None]
    return max((max(ts) - min(ts)) / 86_400_000, 1.0) if len(ts) > 1 else 1.0


def outlier_variant(rows: List[Dict], test_rows: List[Dict], capital: float) -> Optional[Dict]:
    """Ergebnis ohne Ausreißer-Coins (optimizer_outliers.detect) – exakt, da jeder
    Coin unabhängig simuliert wird (Trades einfach weglassen)."""
    per = regime_outliers.per_symbol_metrics(rows)
    ol = optimizer_outliers.detect(per)
    if not ol["outliers"]:
        return None
    ex = set(ol["outliers"])
    kept_rows = [r for r in rows if r.get("symbol") not in ex]
    m = metrics_from_rows(kept_rows, capital)
    tm = metrics_from_rows([r for r in test_rows if r.get("symbol") not in ex], capital) if test_rows else None
    return {"excluded": ol["outliers"], "reasons": ol["reasons"],
            "kept": sorted(s for s in per if s not in ex), "metrics": m, "test_metrics": tm,
            "pnl_gain": round(m["pnl"] - sum(v["pnl"] for v in per.values()), 2)}


def _check(cid: str, label: str, passed, detail: str) -> Dict:
    return {"id": cid, "label": label, "enabled": True, "passed": passed, "detail": detail}


def build(backtest: Optional[Dict], wf: Optional[Dict], body: Optional[Dict] = None) -> Optional[Dict]:
    """Ergebnis-Karte (rein). None ohne Ergebnis-Backtest."""
    if not backtest or backtest.get("error") or not backtest.get("total"):
        return None
    th = thresholds(body)
    capital = float((backtest.get("config") or {}).get("max_capital") or 100)
    total = backtest["total"]
    rows = _rows(backtest.get("points"))
    test_rows = _rows((wf or {}).get("points"))
    # Training = Ergebnis-Backtest VOR dem Holdout-Beginn (sonst ganzer Zeitraum)
    hold_from = min((r["closed"] for r in test_rows), default=None)
    train_rows = [r for r in rows if hold_from is None or str(r["closed"]) < str(hold_from)]
    train_m = metrics_from_rows(train_rows, capital) if hold_from else total
    test_m = (wf or {}).get("dynamic_test") or (metrics_from_rows(test_rows, capital) if test_rows else None)
    card: Dict = {"metrics": total, "train_metrics": train_m, "test_metrics": test_m,
                  "baseline": (wf or {}).get("best_single"), "thresholds": th}
    checks: List[Dict] = []
    fails: List[str] = []
    if test_m:
        card["wf"] = robustness.walk_forward_eval(train_m, test_m, _days(train_rows), _days(test_rows))
        ok = float(test_m.get("pnl") or 0) > 0
        checks.append(_check("wf", "Walk-Forward (Holdout)", ok,
                             f"Test-PnL {test_m.get('pnl')} über {test_m.get('trades')} Trades · "
                             f"Übereinstimmung {card['wf']['consistency_pct']} %"))
        if not ok:
            fails.append("Auf unbekannten Daten (Holdout) im Minus")
    dd_ok, ratio = robustness.dd_check(total, th["dd_max_pct"])
    card["dd_pass"], card["dd_ratio_pct"] = dd_ok, ratio
    checks.append(_check("dd", "Drawdown-Filter", dd_ok,
                         f"DD {ratio if ratio is not None else '–'} % vom PnL (max {th['dd_max_pct']:g} %)"))
    mc = robustness.monte_carlo([r["pnl"] for r in rows], th["mc_runs"], th["mc_max_dd_pct"])
    card["monte_carlo"] = mc
    checks.append(_check("mc", "Monte-Carlo", mc["passed"],
                         f"{mc['runs']} gemischte Läufe · DD p95 {mc['dd_p95'] if mc['dd_p95'] is not None else '–'}"
                         f" ({mc['dd_p95_pct'] if mc['dd_p95_pct'] is not None else '–'} % vom PnL, max {th['mc_max_dd_pct']:g} %)"))
    if test_rows:
        card["monte_carlo_test"] = robustness.monte_carlo([r["pnl"] for r in test_rows], th["mc_runs"],
                                                          th["mc_max_dd_pct"])
    st = dynamic_robustness.stress(total, th["st_mult"])
    st.update({"trades": total.get("trades"), "win_rate": total.get("win_rate")})
    card["stress"] = st
    checks.append(_check("stress", f"Kosten-Stress ×{th['st_mult']:g}", st["passed"],
                         f"PnL {st['pnl']} bei Gebühren {st['fees']}"))
    ct = robustness.evaluate_chunks(dynamic_robustness.chunk_pnls(backtest.get("points") or [],
                                                                  th["ct_chunk_days"]), th["ct_max_dev_pct"])
    ct["chunk_days"] = th["ct_chunk_days"]
    card["constancy"] = ct
    checks.append(_check("ct", f"Konstanz ({th['ct_chunk_days']} Tage)", ct["passed"],
                         f"{ct['chunks']} Abschnitte · {ct['profitable_chunks_pct']} % profitabel · Streuung "
                         f"{ct['deviation_pct'] if ct['deviation_pct'] is not None else '–'} % (max {th['ct_max_dev_pct']:g} %)"))
    per_sym = regime_outliers.per_symbol_metrics(rows)
    card["per_symbol"] = per_sym
    card["positive_symbols_pct"] = round(sum(1 for v in per_sym.values() if v["pnl"] > 0)
                                         / len(per_sym) * 100, 1) if per_sym else None
    if card["positive_symbols_pct"] is not None:
        b_ok = card["positive_symbols_pct"] >= BREADTH_MIN_PCT
        checks.append(_check("breadth", "Breite über Coins", b_ok,
                             f"{card['positive_symbols_pct']} % der Coins profitabel (mind. {BREADTH_MIN_PCT:g} %)"))
    n = int(total.get("trades") or 0)
    checks.append(_check("trades", "Genug Trades", n >= MIN_TRADES,
                         f"{n} Trades im Gesamtzeitraum (mind. {MIN_TRADES} für eine belastbare Aussage)"))
    if n < MIN_TRADES:
        fails.append(f"Nur {n} Trades – Ergebnis statistisch dünn (Overfitting-Gefahr)")
    verdict = (wf or {}).get("verdict") or {}
    if "dynamic_better" in verdict:
        checks.append(_check("vs_single", "Besser als beste Einzel-Strategie", bool(verdict["dynamic_better"]),
                             verdict.get("recommendation") or ""))
    card["per_regime"] = [{"regime": r.get("regime"), "label": r.get("label"),
                           "pnl": (r.get("metrics") or {}).get("pnl"),
                           "trades": (r.get("metrics") or {}).get("trades")}
                          for r in backtest.get("regimes") or [] if r.get("traded") and r.get("metrics")]
    card["outlier_variant"] = outlier_variant(rows, test_rows, capital)
    hard = [c for c in checks if c["id"] in ("wf", "dd", "mc", "trades")]
    card["passed"] = all(c["passed"] for c in hard)
    fails += [f"{c['label']}: {c['detail']}" for c in checks
              if c["passed"] is False and c["id"] not in ("wf", "trades")]
    card["checks"], card["fail_reasons"] = checks, fails
    ok_n = sum(1 for c in checks if c["passed"])
    card["rank_reason"] = (f"{ok_n}/{len(checks)} Checks bestanden"
                           + (f" · WF-Score {card['wf']['wf_score']}" if card.get("wf") else "")
                           + f" · {n} Trades · PnL {total.get('pnl')}")
    card["recommendation"] = optimizer_outliers.recommendation(
        {"metrics": total, "test_metrics": test_m, "positive_symbols_pct": card["positive_symbols_pct"],
         "passed": card["passed"], "outlier_variant": card["outlier_variant"]})
    return card
