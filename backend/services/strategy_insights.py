"""Cross-Strategie-Einblicke für das KI-Trader-Training (NUR Lesezugriff).

Der KI-Trader soll beim Lernen/Training sehen, wie die ANDEREN Strategien
(Trendfolge, Scalping, …) konfiguriert sind und was davon in der Praxis
funktioniert. Dieses Modul aggregiert dafür rein lesend:

  1. Registry-Strategien inkl. aktuell wirksamer Parameter (Trader-Overrides)
  2. Echte Ergebnisse pro Strategie aus ``auto_trades`` (Paper + Live getrennt)
  3. Beste Optimizer-Ergebnisse pro Strategie (``optimizer_runs``)
  4. Erkenntnisse aus dem Optimizer-Lerngedächtnis (``learning_memory``)
  5. Laufende Lernnotizen des Strategie-Labors (``last_assist`` der Kandidaten)

WICHTIG: Der Block wird ausschließlich in LERN-/TRAININGS-Prompts injiziert
(Lernlauf, Strategie-Labor) – NICHT in Live-Analyse-/Trade-Entscheidungen.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

EXCLUDED_IDS = ("ai_trader",)
MAX_PARAM_KEYS = 8


def _fmt_params(params: Dict) -> str:
    if not params:
        return "Standard"
    items = list(params.items())[:MAX_PARAM_KEYS]
    txt = ", ".join(f"{k}={v}" for k, v in items)
    if len(params) > MAX_PARAM_KEYS:
        txt += f", … (+{len(params) - MAX_PARAM_KEYS} weitere)"
    return txt


async def strategy_trade_stats(db) -> Dict[str, Dict]:
    """Geschlossene Trades pro Strategie und Modus (paper/live) aggregieren."""
    out: Dict[str, Dict] = {}
    if db is None:
        return out
    pipeline = [
        {"$match": {"status": "closed", "strategy_id": {"$nin": list(EXCLUDED_IDS)}}},
        {"$group": {
            "_id": {"sid": "$strategy_id", "mode": "$mode"},
            "trades": {"$sum": 1},
            "wins": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$realized_pnl", 0]}, 0]}, 1, 0]}},
            "pnl": {"$sum": {"$ifNull": ["$realized_pnl", 0]}},
        }},
    ]
    try:
        rows = await db.auto_trades.aggregate(pipeline).to_list(200)
    except Exception as e:
        logger.warning(f"strategy_insights: Trade-Aggregation fehlgeschlagen: {e}")
        return out
    for r in rows:
        sid = str((r.get("_id") or {}).get("sid") or "")
        mode = str((r.get("_id") or {}).get("mode") or "paper")
        if not sid:
            continue
        trades = int(r.get("trades", 0))
        wins = int(r.get("wins", 0))
        entry = out.setdefault(sid, {})
        entry[mode] = {
            "trades": trades,
            "win_rate": round(100.0 * wins / trades, 1) if trades else 0.0,
            "pnl": round(float(r.get("pnl", 0) or 0), 2),
        }
    return out


async def optimizer_best(db, limit: int = 40) -> Dict[str, Dict]:
    """Jüngstes Optimizer-Best-Ergebnis pro Strategie (neueste zuerst)."""
    out: Dict[str, Dict] = {}
    if db is None:
        return out
    try:
        runs = await db.optimizer_runs.find(
            {}, {"params.strategy_id": 1, "result.best": 1, "result.symbol": 1,
                 "result.timeframe": 1, "created_at": 1}
        ).sort("created_at", -1).limit(limit).to_list(limit)
    except Exception as e:
        logger.warning(f"strategy_insights: Optimizer-Lookup fehlgeschlagen: {e}")
        return out
    for run in runs:
        sid = str(((run.get("params") or {}).get("strategy_id")) or "")
        best = (run.get("result") or {}).get("best")
        if not sid or sid in out or not isinstance(best, dict):
            continue
        m = best.get("metrics") or {}
        out[sid] = {
            "symbol": (run.get("result") or {}).get("symbol"),
            "timeframe": (run.get("result") or {}).get("timeframe"),
            "trades": m.get("trades"),
            "win_rate": m.get("win_rate"),
            "pnl_pct": m.get("total_pnl_pct", m.get("pnl_pct")),
        }
    return out


async def learning_notes(db, limit: int = 12) -> List[str]:
    """Erkenntnisse aus dem Optimizer-Lerngedächtnis (learning_memory)."""
    if db is None:
        return []
    try:
        rows = await db.learning_memory.find(
            {"strategy_id": {"$nin": list(EXCLUDED_IDS) + [None]}}
        ).sort("at", -1).limit(limit).to_list(limit)
    except Exception as e:
        logger.warning(f"strategy_insights: learning_memory fehlgeschlagen: {e}")
        return []
    lines = []
    for r in rows:
        name = r.get("strategy_name") or r.get("strategy_id")
        m_parts = []
        for k in ("regime_label", "timeframe"):
            if r.get(k):
                m_parts.append(str(r[k]))
        metrics = r.get("metrics") or {}
        if metrics.get("trades") is not None:
            m_parts.append(f"{metrics.get('trades')} Trades, WR {metrics.get('win_rate', '?')}%, "
                           f"PnL {metrics.get('total_pnl_pct', metrics.get('pnl_pct', '?'))}%")
        note = str(r.get("note") or "").strip()
        line = f"- {name}: {' | '.join(m_parts)}" + (f" – {note[:140]}" if note else "")
        lines.append(line)
    return lines


async def lab_notes(db, limit: int = 6) -> List[str]:
    """Laufende Lernnotizen/Einschätzungen aus dem Strategie-Labor."""
    if db is None:
        return []
    try:
        rows = await db.ai_strategy_candidates.find(
            {"stage": {"$ne": "rejected"}, "last_assist": {"$exists": True}},
            {"name": 1, "stage": 1, "last_assist.feedback": 1,
             "last_assist.data_findings": 1, "updated_at": 1}
        ).sort("updated_at", -1).limit(limit).to_list(limit)
    except Exception as e:
        logger.warning(f"strategy_insights: Labor-Notizen fehlgeschlagen: {e}")
        return []
    lines = []
    for c in rows:
        la = c.get("last_assist") or {}
        fb = str(la.get("feedback") or "").strip()
        findings = [str(f) for f in (la.get("data_findings") or []) if f][:2]
        if not (fb or findings):
            continue
        parts = [fb[:160]] if fb else []
        parts += [f[:120] for f in findings]
        lines.append(f"- „{c.get('name', '?')}“ [{c.get('stage', '?')}]: " + " | ".join(parts))
    return lines


async def context_text(db, scanner_settings: Optional[Dict] = None,
                       max_chars: int = 2600) -> str:
    """Kompakter Vergleichsblock über alle anderen Strategien (nur Lernkontext)."""
    from strategies.registry import registry
    if scanner_settings is None:
        try:
            from core import state
            scanner_settings = getattr(state.scanner, "settings", {}) or {}
        except Exception:
            scanner_settings = {}
    stats = await strategy_trade_stats(db)
    opt = await optimizer_best(db)
    lines = [
        "=== ANDERE STRATEGIEN IM VERGLEICH (NUR LESE-/LERNKONTEXT) ===",
        "Vergleichsdaten der übrigen Plattform-Strategien: Parameter + echte "
        "Ergebnisse. Nutze sie, um Muster zu erkennen (was funktioniert, was "
        "nicht) und in Lektionen/neue Setups einfließen zu lassen. Es sind "
        "KEINE Handelsanweisungen und du darfst diese Strategien NICHT ändern.",
    ]
    rows = []
    for strat in registry._strategies.values():
        sid = strat.STRATEGY_ID
        if sid in EXCLUDED_IDS or getattr(strat, "IS_CUSTOM", False):
            continue
        try:
            params = strat.get_params(scanner_settings)
        except Exception:
            params = {}
        st = stats.get(sid) or {}
        perf_parts = []
        for mode in ("live", "paper"):
            m = st.get(mode)
            if m:
                perf_parts.append(f"{mode.capitalize()}: {m['trades']} Trades, "
                                  f"WR {m['win_rate']}%, PnL {m['pnl']:+.2f} USDT")
        perf = " | ".join(perf_parts) or "noch keine geschlossenen Trades"
        o = opt.get(sid)
        opt_txt = ""
        if o:
            opt_txt = (f" | Optimizer-Best ({o.get('symbol') or '?'} {o.get('timeframe') or ''}): "
                       f"{o.get('trades', '?')} Trades, WR {o.get('win_rate', '?')}%, "
                       f"PnL {o.get('pnl_pct', '?')}%")
        total_trades = sum(m.get("trades", 0) for m in st.values())
        rows.append((total_trades, f"- {strat.STRATEGY_NAME} [{sid}] (TF {strat.STRATEGY_TIMEFRAME}): "
                                   f"Parameter: {_fmt_params(params)} | {perf}{opt_txt}"))
    rows.sort(key=lambda r: r[0], reverse=True)
    row_lines = [r[1] for r in rows]
    tail_lines: List[str] = []
    notes = await learning_notes(db)
    if notes:
        tail_lines.append("Erkenntnisse aus Optimizer-/Backtest-Läufen (neueste zuerst):")
        tail_lines += notes
    lab = await lab_notes(db)
    if lab:
        tail_lines.append("Laufende Lernnotizen aus dem Strategie-Labor:")
        tail_lines += lab
    # Budget: Erkenntnisse/Notizen haben Vorrang – bei Platzmangel werden
    # zuerst die Strategie-Zeilen mit den wenigsten Trades weggelassen.
    tail_text = "\n".join(tail_lines)
    head_text = "\n".join(lines)
    budget = max_chars - len(head_text) - len(tail_text) - 2
    kept: List[str] = []
    used = 0
    for rl in row_lines:
        if used + len(rl) + 1 > budget:
            continue
        kept.append(rl)
        used += len(rl) + 1
    text = "\n".join([head_text] + kept + ([tail_text] if tail_text else []))
    if len(text) > max_chars:
        text = text[:max_chars - 2] + " …"
    return text
