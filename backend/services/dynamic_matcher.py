"""„Beste Strategie je Phase“ (Werkbank kind="match").

Stufe 1 – Vergleich: je Regime jede Kandidaten-Strategie mit ihren aktuellen
Einstellungen auf den Abschnitten DIESES Regimes testen (regime_opt mit 0
Iterationen = Basis-Konfiguration + Walk-Forward je Regime). Ranking exakt wie
die übrige Werkbank (dynamic_workbench.candidate_rank: robust validiert, genug
Trades, nicht überangepasst, Drawdown, Score).

Stufe 2 – Anpassen: die besten FINALISTS Strategien je Regime laufen danach in
der normalen Werkbank-Schleife (Parameter-Suche, Runden/Endlos, nur echte
Verbesserungen werden übernommen) – es entsteht kein neuer Such-Code.
"""
from typing import Callable, Dict, List

from services import regime_outliers

MAX_CANDIDATES = 20
FINALISTS = 2
TABLE_ROWS = 12


def comparison_row(sid: str, name: str, cand: Dict) -> Dict:
    m, v = cand.get("metrics") or {}, cand.get("validation") or {}
    return {"strategy_id": sid, "strategy_name": name, "score": cand.get("score"),
            "pnl": m.get("pnl"), "trades": m.get("trades"), "win_rate": m.get("win_rate"),
            "test_pnl": v.get("pnl") if v else None, "test_trades": v.get("trades") if v else None,
            "robust": regime_outliers.robust_validated(cand),
            "trade_sufficient": cand.get("trade_sufficient") is not False,
            "overfit": bool(cand.get("overfit")),
            "flags": regime_outliers.flag_texts(regime_outliers.candidate_flags(cand))}


def pick_finalists(scored: List[tuple], rank: Callable, n: int = FINALISTS) -> List[tuple]:
    """scored: [(sid, cand)] -> beste n mit mind. einem Trade (rein)."""
    usable = [(sid, c) for sid, c in scored if c and int((c.get("metrics") or {}).get("trades") or 0) > 0]
    return sorted(usable, key=lambda x: rank(x[1]), reverse=True)[:n]


async def compare_stage(job: Dict, p: Dict, targets: Dict[int, Dict], regimes: Dict[int, Dict],
                        best: Dict[int, Dict], improved: set, deps: Dict, run_search: Callable,
                        rank: Callable, log: Callable, candidate: Callable) -> None:
    """Stufe 1 für alle Regime; setzt targets[rid] auf die Finalisten (Stufe 2)."""
    total = sum(len(t.get("candidates") or []) for t in targets.values()) or 1
    done = 0
    for rid, t in sorted(targets.items()):
        label = (regimes.get(rid) or {}).get("label") or f"#{rid + 1}"
        scored = []
        for sid in (t.get("candidates") or [])[:MAX_CANDIDATES]:
            if job.get("stop") or job.get("cancel"):
                break
            strat = deps["registry"].get(sid)
            done += 1
            if not strat:
                continue
            log(job, f"Vergleich · Regime '{label}' · {strat.STRATEGY_NAME} ({done}/{total})")
            job["progress"] = round(done / total * 40, 1)
            try:
                res = await run_search(rid, sid)
            except RuntimeError as e:
                log(job, f"Vergleich '{label}' · {strat.STRATEGY_NAME}: {str(e)[:120]}")
                continue
            top = (res.get("top5") or [None])[0]
            if top:
                scored.append((sid, res, top))
        cands = sorted([(sid, candidate(res, top)) for sid, res, top in scored],
                       key=lambda x: rank(x[1]), reverse=True)
        rows = [comparison_row(sid, c.get("strategy_name") or sid, c) for sid, c in cands]
        entry = job["regimes"].setdefault(str(rid), {"label": label})
        entry["comparison"] = rows[:TABLE_ROWS]
        finals = pick_finalists(cands, rank)
        if not finals:
            entry["note"] = "keine Strategie mit Trades in diesem Regime"
            t["mode"], t["strategy_id"] = "params", None
            continue
        win_sid, win = finals[0]
        if all(float((c.get("metrics") or {}).get("pnl") or 0) <= 0 for _, c in cands):
            entry["edge_note"] = ("keine Strategie mit Gewinn in diesem Regime (kein Edge) – "
                             "wird nur übernommen, wenn die Anpassung es robust ins Plus bringt")
        t.update({"mode": "params", "strategy_id": win_sid,
                  "alternatives": [sid for sid, _ in finals]})
        entry["winner"] = win.get("strategy_name") or win_sid
        keep = {k: entry[k] for k in ("comparison", "winner", "edge_note") if k in entry}
        if rank(win) > rank(best.get(rid)):
            best[rid] = win
            improved.add(rid)
            entry.clear()
            entry.update({**deps["regime_entry"](label, win, "params", 0), **keep})
        else:
            entry.clear()
            entry.update({**deps["rejected_entry"](label, win), **keep})
        log(job, f"Regime '{label}': bester Vergleich {entry['winner']}"
                 + (f" · Finalisten {', '.join(s for s, _ in finals)}" if len(finals) > 1 else ""))
