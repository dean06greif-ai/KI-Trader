"""Copilot-Kontext für dynamische Strategien und die Dynamik-Werkbank (nur lesend).

Vorher sah der Strategie-Copilot dynamische Strategien nur als eine Zeile in der
Strategie-Übersicht (Name + Paper/Live-PnL) und in der Werkbank alte, nicht
mehr genutzte Einstellungen. Jetzt bekommt er je Strategie den vollständigen
Aufbau (Regime -> Strategie/Parameter/Trade-Werte, nicht gehandelte Regime,
Wechsel-Verhalten, Release-Status, Walk-Forward-Urteil, aktuelles Live-Regime,
Live/Paper-Ergebnis je Regime) und den Stand/das Ergebnis des Werkbank-Laufs
(Runden, Regime-Suche, Walk-Forward, Ergebnis-Backtest, Zusatz-Tests).
"""
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

MAX_STRATEGIES = 6        # so viele dynamische Strategien kommen ausführlich in den Kontext
MAX_FOCUS_PERF = 3        # Live/Paper je Regime (DB-Abfrage) nur für die fokussierten
BLOCK_LIMIT = 5200        # Zeichen-Budget des gesamten Blocks
JOB_LIMIT = 2600


def _j(obj, limit: int) -> str:
    try:
        s = json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        s = str(obj)
    return s if len(s) <= limit else s[:limit] + "…"


def _metrics(m: Optional[Dict]) -> str:
    if not m:
        return "–"
    keys = ("trades", "pnl", "win_rate", "max_drawdown", "profit_factor")
    return ", ".join(f"{k}={m[k]}" for k in keys if m.get(k) is not None) or "–"


def _regime_lines(regimes: List[Dict], skipped: List[int], perf: Optional[Dict]) -> List[str]:
    live = {r.get("regime"): r for r in ((perf or {}).get("regimes") or []) if isinstance(r, dict)}
    out = []
    for r in regimes:
        rid = r.get("id")
        if not r.get("traded"):
            why = "Verlust im Walk-Forward" if rid in skipped else "keine Strategie"
            line = f"  · R{rid} {r.get('label')}: NICHT HANDELN ({why})"
        else:
            line = (f"  · R{rid} {r.get('label')}: {r.get('strategy_name')} [{r.get('strategy_id')}]"
                    f" | Strategie-Parameter {_j(r.get('strategy_params') or {}, 220)}"
                    f" | Trade-Werte {_j(r.get('trade_params') or {}, 220)}")
        lp = live.get(rid)
        if lp and (lp.get("live") or {}).get("trades"):
            line += f" | Live/Paper: {_metrics(lp.get('live'))}"
            if (lp.get("verdict") or {}).get("text"):
                line += f" ({lp['verdict']['text']})"
        out.append(line)
    return out


def strategy_text(doc: Dict, regimes: List[Dict], live_state: Optional[Dict],
                  release_status: str, perf: Optional[Dict] = None) -> str:
    """Ausführliche Beschreibung EINER dynamischen Strategie (rein)."""
    s = doc.get("settings") or {}
    syms = doc.get("symbols") or []
    head = (f"- {doc.get('name')} [{doc['id']}] · Status {release_status} · TF {doc.get('timeframe')} · "
            f"{len(syms)} Assets ({', '.join(syms[:8])}{' …' if len(syms) > 8 else ''}) · "
            f"Regime-Analyse {s.get('analysis_id') or '–'} ({s.get('scope_key') or 'combined'}) · "
            f"Regimewechsel: {s.get('on_switch') or 'Trade läuft weiter'} · "
            f"Konfidenz min {s.get('confidence_min')} · Mindest-Haltedauer {s.get('min_hold_days')} Tage")
    lines = [head]
    if doc.get("verdict"):
        lines.append("  Walk-Forward-Urteil: " + _j(doc["verdict"], 420))
    lines += _regime_lines(regimes, list(s.get("skipped_regimes") or []), perf)
    if live_state:
        cur = [f"{sym}: {st.get('label')} ({st.get('confidence')} %)"
               for sym, st in list(live_state.items())[:8]]
        lines.append("  Aktuelles Live-Regime: " + "; ".join(cur))
    if perf and perf.get("total"):
        lines.append(f"  Live/Paper gesamt: {_metrics(perf['total'])}"
                     f" · Trades ohne Regime-Info {perf.get('unknown_regime_trades', 0)}")
    return "\n".join(lines)


def workbench_text(job: Optional[Dict]) -> str:
    """Stand + Ergebnis eines Werkbank-Laufs (rein, gekürzt)."""
    if not job:
        return ""
    p = job.get("params") or {}
    parts = [f"Lauf {job.get('id')} · Art {job.get('kind')} · Status {job.get('status')} · "
             f"Runde {job.get('round')} · Fortschritt {job.get('progress')} % · Phase {job.get('phase') or '–'}"]
    if job.get("error"):
        parts.append("Fehler: " + str(job["error"])[:240])
    keep = ("analysis_id", "regime_ids", "modes", "iterations", "objective", "timeframe", "days",
            "execution", "walkforward", "result_backtest", "rounds", "min_trades",
            "dd_filter", "robustness", "skip_losing", "label_basis")
    parts.append("Einstellungen: " + _j({k: p[k] for k in keep if k in p}, 700))
    if job.get("regimes"):
        parts.append("Regime-Suche (bester Kandidat je Regime): " + _j(job["regimes"], 900))
    res = job.get("result") or {}
    if res:
        wf = res.get("walkforward") or {}
        if wf:
            parts.append("Finaler Walk-Forward: " + _j({k: wf.get(k) for k in
                                                        ("verdict", "dynamic_test", "best_single",
                                                         "skipped_regimes", "switches")}, 700))
        bt = res.get("backtest") or {}
        if bt:
            parts.append("Ergebnis-Backtest gesamt: " + (bt.get("error") or _metrics(bt.get("total"))))
            per = [f"{r.get('label')}: {_metrics(r.get('metrics'))}"
                   for r in (bt.get("regimes") or []) if r.get("traded")]
            if per:
                parts.append("Ergebnis-Backtest je Regime: " + " | ".join(per))
            rb = bt.get("robustness")
            if rb:
                parts.append("Zusatz-Tests: " + ("bestanden" if rb.get("passed") else "NICHT bestanden")
                             + " – " + "; ".join(f"{c['label']}: {'✓' if c['passed'] else '✗'} {c['detail']}"
                                                 for c in rb.get("checks") or []))
        if res.get("dynamic_id"):
            parts.append(f"Daraus gebaute dynamische Strategie: {res['dynamic_id']}")
    return _j_text("\n".join(parts), JOB_LIMIT)


def _j_text(s: str, limit: int) -> str:
    return s if len(s) <= limit else s[:limit] + "…"


async def build_block(db, registry, ctx: Dict) -> str:
    """DYNAMISCHE-STRATEGIEN-Block + Werkbank-Stand für den Copilot-Kontext."""
    from services import dynamic_performance, dynamic_runtime, strategy_release
    from services import dynamic_workbench as wb

    ctx = ctx or {}
    focus = [str(x) for x in (ctx.get("dynamic_ids") or []) if x]
    for k in ("editing_strategy_id", "strategy_id"):
        if str(ctx.get(k) or "").startswith("dyn_"):
            focus.append(str(ctx[k]))
    job = wb.JOBS.get(str(ctx.get("dynamic_job_id") or "")) if ctx.get("dynamic_job_id") else None
    if job and (job.get("result") or {}).get("dynamic_id"):
        focus.append(job["result"]["dynamic_id"])
    focus = list(dict.fromkeys(focus))

    docs = await db.dynamic_strategies.find({}, {"_id": 0, "last_state": 0, "runtime_state": 0}) \
        .sort("created_at", -1).to_list(200)
    docs.sort(key=lambda d: (d["id"] not in focus))
    parts: List[str] = []
    for i, doc in enumerate(docs[:MAX_STRATEGIES]):
        strat = registry.get(doc["id"])
        regimes = strat.regimes() if strat is not None and getattr(strat, "IS_DYNAMIC", False) else \
            [{"id": r["id"], "label": r.get("label"), "traded": False}
             for r in (doc.get("model") or {}).get("regimes") or []]
        perf = None
        if i < MAX_FOCUS_PERF:
            try:
                perf = await dynamic_performance.regime_performance(db, doc, regimes)
            except Exception as e:  # noqa: BLE001 – Kontext darf den Chat nie blockieren
                logger.debug(f"Copilot: Regime-Performance {doc['id']}: {e}")
        parts.append(strategy_text(doc, regimes, dynamic_runtime.STATE.get(doc["id"]),
                                   strategy_release.effective_status(doc), perf))
    if len(docs) > MAX_STRATEGIES:
        parts.append(f"(+ {len(docs) - MAX_STRATEGIES} weitere: "
                     + ", ".join(f"{d.get('name')} [{d['id']}]" for d in docs[MAX_STRATEGIES:30]) + ")")
    out = []
    if parts:
        out.append("DYNAMISCHE STRATEGIEN (vollständiger Aufbau, nur lesend – fokussierte zuerst):\n"
                   + _j_text("\n".join(parts), BLOCK_LIMIT))
    if ctx.get("workbench"):
        out.append("DYNAMIK-WERKBANK – AKTUELLE EINSTELLUNGEN IM PANEL: " + _j(ctx["workbench"], 1400))
    wtxt = workbench_text(job)
    if wtxt:
        out.append("DYNAMIK-WERKBANK – LAUF/ERGEBNIS:\n" + wtxt)
    return "\n\n".join(out)
