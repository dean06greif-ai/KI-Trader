"""Dynamische Strategien im Kerzen-Backtester.

Eine gespeicherte dynamische Strategie (Dokument in `dynamic_strategies`) wird
wie im Live-Betrieb simuliert: das Regime wird je Kerze rein rückblickend
bestimmt (regime.classify_series, kein Lookahead), jeder zusammenhängende
Regime-Abschnitt läuft mit der Sub-Strategie und den Trade-Parametern seines
Regimes (services.strategy_plan – dieselbe Auflösung wie live), Trades werden
chronologisch zusammengeführt.

Ergebnis pro dynamischer Strategie: Backtester-kompatible per_pair-Zeilen
(je Coin) + eine Aufschlüsselung je Regime mit Empfehlung („handeln“,
„nicht handeln“, „besser mit Strategie X“) und Equity-Punkten mit Regime-Tag.

Nutzt ausschließlich bestehende Bausteine (dynamic_strategy.eval_dynamic,
regime_lab.fetch_histories, strategy_plan.resolve_symbol_plan) – der klassische
Backtest-Pfad bleibt unverändert.
"""
import json
import logging
from typing import Callable, Dict, List, Optional, Tuple

from services import dynamic_strategy as dyn
from services import regime as rg
from services import regime_lab as lab
from services import strategy_plan

logger = logging.getLogger(__name__)

MIN_TRADES_FOR_VERDICT = 5
# Alternative muss deutlich besser sein, sonst keine Wechsel-Empfehlung
ALT_MIN_IMPROVEMENT = 0.15


def _plan_for_regime(doc: Dict, rid: int, registry, mk_strategy) -> Dict:
    """Sub-Strategie, Trade-Overrides und Strategie-Parameter eines Regimes
    (rein, gleiche Auflösung wie live)."""
    plan = strategy_plan.resolve_symbol_plan(doc, "_", {"regime": rid})
    sub = registry.get(plan["strategy_id"] or "")
    if plan["unmapped"] or sub is None or getattr(sub, "IS_DYNAMIC", False):
        return {"traded": False, "strategy": None, "strategy_id": None,
                "strategy_name": None, "overrides": {}, "params": {}}
    sub_def = ((doc.get("sub_strategies") or {}).get(str(rid)) or {}).get("definition")
    if plan.get("rules_override") and sub_def:
        strat = mk_strategy(sub_def)
        name = "Eigene Regeln (Discovery)"
    else:
        strat, name = sub, getattr(sub, "STRATEGY_NAME", plan["strategy_id"])
    return {"traded": True, "strategy": strat, "strategy_id": plan["strategy_id"],
            "strategy_name": name, "overrides": plan["overrides"] or {},
            "params": plan["params"] or {}}


def _variant_key(plan: Dict) -> str:
    sid = getattr(plan["strategy"], "STRATEGY_ID", plan["strategy_id"])
    return json.dumps({"s": sid, "o": plan["overrides"], "p": plan["params"]},
                      sort_keys=True, default=str)


def _pair_row(did: str, name: str, sym: str, tf: str, n_candles: int,
              rows: List[Dict], capital: float) -> Dict:
    """per_pair-Zeile im Format des klassischen Backtesters (aus Trade-Liste)."""
    m = dyn.metrics_from_rows(rows, capital)
    durs = [float(r["duration_min"]) for r in rows if r.get("duration_min") is not None]
    return {"strategy_id": did, "strategy_name": name, "symbol": sym,
            "timeframe": tf, "candles": n_candles, "dynamic": True,
            **{k: m[k] for k in ("trades", "wins", "losses", "breakevens", "win_rate",
                                 "pnl", "pnl_pct", "fees", "avg_pnl", "max_drawdown",
                                 "max_drawdown_pct")},
            "secured": sum(1 for r in rows if r.get("profit_secured")),
            "liquidations": sum(1 for r in rows if r.get("liquidated")),
            "avg_duration_min": round(sum(durs) / len(durs), 1) if durs else 0.0,
            "long_trades": sum(1 for r in rows if r.get("side") == "LONG"),
            "short_trades": sum(1 for r in rows if r.get("side") == "SHORT")}


def _equity_points(rows: List[Dict], labels: Dict[int, str]) -> List[Dict]:
    rows = sorted([r for r in rows if r.get("closed")], key=lambda r: r["closed"])
    eq = peak = 0.0
    out = []
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        eq += pnl
        peak = max(peak, eq)
        out.append({"t": r["closed"], "equity": round(eq, 4), "peak": round(peak, 4),
                    "drawdown": round(peak - eq, 4), "pnl": round(pnl, 4),
                    "symbol": r.get("symbol"), "side": r.get("side"),
                    "result": r.get("result"), "regime": r.get("regime"),
                    "regime_label": labels.get(r.get("regime")),
                    "liquidated": bool(r.get("liquidated"))})
    return out


def recommend(entry: Dict, alternatives: List[Dict]) -> Dict:
    """Empfehlung für EIN Regime (rein): handeln / nicht handeln / Strategie
    wechseln. `alternatives`: [{strategy_name, regime_label, metrics}]."""
    if not entry.get("traded"):
        return {"action": "untraded", "text": "wird nicht gehandelt (keine Strategie zugeordnet)"}
    m = entry.get("metrics") or {}
    trades, pnl = int(m.get("trades") or 0), float(m.get("pnl") or 0)
    best_alt = None
    for a in alternatives:
        am = a.get("metrics") or {}
        if int(am.get("trades") or 0) < MIN_TRADES_FOR_VERDICT:
            continue
        if best_alt is None or float(am.get("pnl") or 0) > float(best_alt["metrics"].get("pnl") or 0):
            best_alt = a
    if trades < MIN_TRADES_FOR_VERDICT:
        if best_alt and float(best_alt["metrics"].get("pnl") or 0) > 0:
            return {"action": "switch", "text": f"zu wenig Trades ({trades}) – "
                    f"„{best_alt['strategy_name']}“ (aus {best_alt['regime_label']}) "
                    f"handelt hier profitabel", "alternative": best_alt}
        return {"action": "unclear", "text": f"zu wenig Trades ({trades}) – keine Aussage möglich"}
    alt_pnl = float(best_alt["metrics"].get("pnl") or 0) if best_alt else None
    if alt_pnl is not None and alt_pnl > 0 and alt_pnl > max(pnl, 0) * (1 + ALT_MIN_IMPROVEMENT) \
            and alt_pnl - pnl > 1e-6:
        return {"action": "switch", "alternative": best_alt,
                "text": f"besser mit „{best_alt['strategy_name']}“ (aus {best_alt['regime_label']}): "
                        f"PnL {alt_pnl:.2f} statt {pnl:.2f}"}
    if pnl < 0:
        return {"action": "skip", "text": f"nicht handeln – Verlust {pnl:.2f} über {trades} Trades"}
    if float(m.get("max_drawdown") or 0) > max(pnl, 1e-9) * 1.5 and trades >= MIN_TRADES_FOR_VERDICT:
        return {"action": "caution", "text": f"nur mit Vorsicht – Drawdown {m.get('max_drawdown')} "
                                            f"übersteigt den Gewinn {pnl:.2f} deutlich"}
    return {"action": "trade", "text": f"handeln – PnL {pnl:.2f} bei {m.get('win_rate')}% Winrate"}


async def simulate_dynamic(doc: Dict, symbols: List[str], days: int, cfg: Dict,
                           settings: Dict, registry, job: Dict,
                           cancelled: Callable[[], bool], timeframe: Optional[str] = None,
                           start_ms: Optional[int] = None, end_ms: Optional[int] = None,
                           with_alternatives: bool = True,
                           progress: Tuple[int, int] = (0, 100)) -> Dict:
    """Eine dynamische Strategie auf `symbols` über `days` Tage simulieren."""
    from services.optimizer import _mk_strategy
    did, name = doc["id"], doc.get("name") or doc["id"]
    tf = timeframe or doc.get("timeframe") or "1h"
    model = doc.get("model") or {}
    if not model.get("regimes"):
        raise RuntimeError(f"{name}: kein Regime-Modell gespeichert")
    s_cfg = doc.get("settings") or {}
    conf_min = float(s_cfg.get("confidence_min") or 70) / 100.0
    min_hold = float(s_cfg.get("min_hold_days") or 2)
    labels_of = {int(r["id"]): r.get("label") or f"#{int(r['id']) + 1}"
                 for r in model.get("regimes") or []}
    capital = float(cfg.get("max_capital", 100.0)) or 100.0
    p0, p1 = progress

    plans = {rid: _plan_for_regime(doc, rid, registry, _mk_strategy) for rid in labels_of}
    strategies_by_regime = {rid: p["strategy"] for rid, p in plans.items() if p["traded"]}
    configs = {rid: p["overrides"] for rid, p in plans.items() if p["traded"]}
    settings_by_regime = {rid: dyn.with_strategy_params(settings, p["strategy_id"], p["params"])
                          for rid, p in plans.items() if p["traded"] and p["params"]}
    if not strategies_by_regime:
        raise RuntimeError(f"{name}: kein Regime hat eine handelbare Strategie")
    fallback = next(iter(strategies_by_regime.values()))

    job["phase"] = f"{name}: lade Kerzen ({tf})"
    histories = await lab.fetch_histories(symbols, int(days), tf, job,
                                          progress_span=(p0, p0 + (p1 - p0) * 0.2))
    if start_ms or end_ms:
        histories = {s: [c for c in cs if (not start_ms or c["timestamp"] >= start_ms)
                         and (not end_ms or c["timestamp"] <= end_ms)]
                     for s, cs in histories.items()}
    histories = {s: cs for s, cs in histories.items() if len(cs) > 100}
    if not histories:
        raise RuntimeError(f"{name}: zu wenig Kerzen für {', '.join(symbols)}")

    job["phase"] = f"{name}: Regime je Kerze bestimmen (rückblickend)"
    labels_map = {sym: rg.classify_series(model, cs, tf, conf_min, min_hold)
                  for sym, cs in histories.items()}
    all_segments = dyn.build_segments(histories, labels_map)
    bars_by_regime: Dict[int, int] = {}
    for segs in all_segments.values():
        for s in segs:
            bars_by_regime[s["regime"]] = bars_by_regime.get(s["regime"], 0) + s["n_bars"]
    total_bars = sum(bars_by_regime.values()) or 1
    traded_segments = {sym: [s for s in segs if s["regime"] in strategies_by_regime]
                       for sym, segs in all_segments.items()}
    switches = sum(max(len(ss) - 1, 0) for ss in all_segments.values())

    job["phase"] = f"{name}: dynamische Simulation ({switches} Regimewechsel)"
    job["progress"] = round(p0 + (p1 - p0) * 0.3)
    total_m, rows = await dyn.eval_dynamic(fallback, traded_segments, configs, cfg, settings,
                                           cancelled, strategies_by_regime,
                                           settings_by_regime or None)
    job["progress"] = round(p0 + (p1 - p0) * 0.6)

    # Alternativen: jede andere Regime-Strategie (dedupliziert) auf den
    # Abschnitten dieses Regimes -> Basis der Wechsel-Empfehlung
    alternatives: Dict[int, List[Dict]] = {rid: [] for rid in labels_of}
    if with_alternatives:
        variants: Dict[str, Tuple[int, Dict]] = {}
        for rid, p in plans.items():
            if p["traded"]:
                variants.setdefault(_variant_key(p), (rid, p))
        todo = [(rid, vk, src) for rid in labels_of if bars_by_regime.get(rid)
                for vk, src in variants.items() if src[0] != rid and _variant_key(plans[rid]) != vk]
        for i, (rid, vk, (src_rid, src_plan)) in enumerate(todo):
            if cancelled():
                raise dyn.JobCancelled()
            job["phase"] = f"{name}: Alternative für „{labels_of[rid]}“ ({i + 1}/{len(todo)})"
            job["progress"] = round(p0 + (p1 - p0) * (0.6 + 0.35 * i / max(len(todo), 1)))
            segs_r = {sym: [s for s in segs if s["regime"] == rid]
                      for sym, segs in all_segments.items()}
            alt_settings = dyn.with_strategy_params(settings, src_plan["strategy_id"], src_plan["params"])
            m, _ = await dyn.eval_dynamic(src_plan["strategy"], segs_r, {rid: src_plan["overrides"]},
                                          cfg, alt_settings, cancelled)
            alternatives[rid].append({"strategy_id": src_plan["strategy_id"],
                                      "strategy_name": src_plan["strategy_name"],
                                      "regime": src_rid, "regime_label": labels_of[src_rid],
                                      "metrics": m})

    by_regime: Dict[int, List[Dict]] = {}
    for r in rows:
        by_regime.setdefault(r.get("regime"), []).append(r)
    regimes_out = []
    for rid in sorted(labels_of):
        p = plans[rid]
        entry = {"regime": rid, "label": labels_of[rid], "traded": p["traded"],
                 "strategy_id": p["strategy_id"], "strategy_name": p["strategy_name"],
                 "config": p["overrides"], "share_pct": round(bars_by_regime.get(rid, 0) / total_bars * 100, 1),
                 "bars": bars_by_regime.get(rid, 0),
                 "metrics": dyn.metrics_from_rows(by_regime.get(rid, []), capital) if p["traded"] else None,
                 "alternatives": sorted(alternatives.get(rid) or [],
                                        key=lambda a: -float((a["metrics"] or {}).get("pnl") or 0))}
        entry["recommendation"] = recommend(entry, entry["alternatives"])
        regimes_out.append(entry)

    per_pair = []
    rows_by_sym: Dict[str, List[Dict]] = {}
    for r in rows:
        rows_by_sym.setdefault(r.get("symbol"), []).append(r)
    for sym, cs in histories.items():
        per_pair.append(_pair_row(did, name, sym, tf, len(cs), rows_by_sym.get(sym, []), capital))
    export_trades = [{"strategy_id": did, "strategy_name": name, "timeframe": tf,
                      "regime_label": labels_of.get(r.get("regime")), **r} for r in rows]
    job["progress"] = round(p1)
    return {"per_pair": per_pair, "export_trades": export_trades, "timeframe": tf,
            "breakdown": {"dynamic_id": did, "name": name, "timeframe": tf,
                          "symbols": list(histories.keys()), "switches": switches,
                          "total": total_m, "regimes": regimes_out,
                          "points": _equity_points(rows, labels_of)[:8000],
                          "untraded_regimes": [labels_of[r] for r, p in plans.items() if not p["traded"]],
                          "skip_recommended": [e["label"] for e in regimes_out
                                               if e["recommendation"]["action"] in ("skip", "switch")]}}
