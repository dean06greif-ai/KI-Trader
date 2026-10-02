"""Dynamik-Werkbank (Strategie-Optimizer × Regime-Lab).

Drei Abläufe – alle nutzen AUSSCHLIESSLICH die bestehenden Bausteine
(regime_opt.run_regime_optimizer, /assign-Format, run_walkforward, /build):

  * refine   – bestehende dynamische Strategie optimieren: gesamt oder nur
               ausgewählte Regime (Trade-/Strategie-Parameter oder neue
               Regeln), optional als Endlos-Suche (Runde für Runde, nur echte
               Verbesserungen werden übernommen).
  * create   – neue dynamische Strategie aus bestehenden Strategien + einer
               Regime-Erkennung (Regime -> Strategie frei zuordnen).
  * discover – für eine Regime-Erkennung je Regime eine NEUE Strategie finden
               (wie Discovery), optional endlos.

Abschluss immer: Zuordnungen in der Analyse speichern -> finaler Walk-Forward
auf dem unangetasteten Holdout -> dynamische Strategie bauen (Release:
validiert, wenn der Walk-Forward sie empfiehlt, sonst Entwurf). Beim Verfeinern
entsteht eine NEUE Version – die live gehandelte Strategie bleibt unberührt.
"""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import job_control
from services import regime_lab as lab

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict] = {}
POLL_S = 2.0
# Einstellungen, die 1:1 an regime_opt.run_regime_optimizer weitergereicht werden
PASSTHROUGH_KEYS = ("timeframe", "days", "max_capital", "leverage", "fee_percent", "sessions",
                    "optimize", "indicators", "regime_walk_forward", "regime_train_pct",
                    "deep_test", "label_basis")
# Verlust-Regime: erst ab so vielen Walk-Forward-Trades gilt ein negativer PnL als belegt
MIN_TRADES_SKIP = 5
# Mehrrunden-/Endlos-Suche: Regime ohne Verbesserung in PLATEAU_ROUNDS Runden in
# Folge pausieren und nur jede RETRY_EVERY-te Runde erneut versuchen.
PLATEAU_ROUNDS = 2
RETRY_EVERY = 4
# So oft darf die Suche eines Regimes scheitern, bevor es für den Lauf ausfällt
MAX_REGIME_FAILURES = 2
_NO_DATA_HINTS = ("Keine Kerzen-Abschnitte", "zu wenig", "Zu wenig")


def losing_regimes(wf: Optional[Dict], min_trades: int = MIN_TRADES_SKIP) -> List[Dict]:
    """Regime mit belegt negativem Walk-Forward (PnL < 0 bei >= min_trades) – rein."""
    out = []
    for r in (wf or {}).get("per_regime") or []:
        m = r.get("metrics") or {}
        if r.get("regime") is None or int(m.get("trades") or 0) < min_trades:
            continue
        if float(m.get("pnl") or 0) < 0:
            out.append({"regime": int(r["regime"]), "label": r.get("label"),
                        "pnl": round(float(m["pnl"]), 2), "trades": int(m["trades"])})
    return out


def kept_after_skip(wf: Optional[Dict], skipped: List[Dict]) -> Optional[Dict]:
    """Walk-Forward-Summe der weiter gehandelten Regime (nur Info: die Auswahl nutzt
    den Holdout, der Wert ist daher KEIN unabhängiger Test mehr)."""
    if not wf or not skipped:
        return None
    skip = {s["regime"] for s in skipped}
    rows = [r for r in wf.get("per_regime") or [] if r.get("regime") not in skip]
    return {"pnl": round(sum(float((r.get("metrics") or {}).get("pnl") or 0) for r in rows), 2),
            "trades": sum(int((r.get("metrics") or {}).get("trades") or 0) for r in rows),
            "regimes": len(rows), "independent": False}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_job(kind: str, params: Dict) -> str:
    jid = f"wb_{uuid.uuid4().hex[:10]}"
    JOBS[jid] = {"id": jid, "kind": kind, "status": "running", "progress": 0,
                 "phase": "Startet", "params": params, "stop": False, "cancel": False,
                 "round": 0, "log": [], "regimes": {}, "result": None, "error": None,
                 "created_at": _now_iso()}
    for k in list(JOBS.keys())[:-10]:
        if JOBS[k]["status"] != "running":
            JOBS.pop(k, None)
    return jid


def running_job() -> Optional[Dict]:
    return next((j for j in JOBS.values() if j["status"] == "running"), None)


def candidate_rank(entry: Optional[Dict]) -> tuple:
    """Vergleichsschlüssel eines Kandidaten (rein): bestandene Regime-Validierung
    vor Score – eine Suche übernimmt nur, was diesen Schlüssel verbessert."""
    if not entry:
        return (-1, -1e9)
    return (1 if entry.get("validation_passed") else 0, float(entry.get("score") or -1e9))


def candidate_from_result(res: Dict, entry: Dict, job_id: str) -> Dict:
    """Kandidat im Format von POST /api/regime-lab/{aid}/assign (rein)."""
    disc = res.get("discovery") or {}
    return {"mode": res.get("mode"), "strategy_id": res.get("strategy_id"),
            "strategy_name": res.get("strategy_name"),
            "definition": res.get("definition"), "rules": disc.get("rules") or [],
            "trade_params": entry.get("trade_params") or {},
            "strategy_params": entry.get("strategy_params") or {},
            "metrics": entry.get("metrics"), "validation": entry.get("validation"),
            "source_job_id": job_id, "score": entry.get("score"),
            "validation_passed": entry.get("validation_passed")}


def _log(job: Dict, msg: str):
    job["phase"] = msg[:240]
    job["log"].append({"at": _now_iso(), "msg": msg[:300]})
    del job["log"][:-60]


def request_pause(job: Dict, flag: bool) -> bool:
    """Pause/Fortsetzen der Werkbank (gleiche Semantik wie Optimizer/Regime-Lab):
    der Job bleibt 'running', das Flag wandert an den laufenden Unterjob."""
    if job.get("status") != "running" or job.get("cancel"):
        return False
    job["pause"] = bool(flag)
    if not flag:
        job_control._leave(job)
    return True


def request_stop(job: Dict) -> bool:
    if job.get("status") != "running":
        return False
    job["stop"] = True
    job["pause"] = False  # Pause aufheben, sonst würde der Stop nie greifen
    return True


def reset_running() -> int:
    """Notfall-Reset: laufende Werkbank-Jobs und ihre Regime-Lab-Unterjobs freigeben."""
    n = job_control.reset_running(JOBS, "Zurückgesetzt (Notfall-Reset)")
    for lj in lab.JOBS.values():
        if lj.get("status") == "running" and (lj.get("params") or {}).get("workbench_job"):
            lj["cancel"] = True
            lj["pause"] = False
            lj["status"] = "cancelled"
            lj["phase"] = "Zurückgesetzt (Notfall-Reset der Werkbank)"
    return n


def _mirror_sub(job: Dict, lj: Dict) -> None:
    """Steuer-Flags Werkbank -> Unterjob, Anzeige Unterjob -> Werkbank (rein)."""
    if job.get("cancel"):
        lj["cancel"] = True
    if job.get("stop"):
        lj["stop_explore"] = True
    want = job_control.pause_requested(job)
    if bool(lj.get("pause")) != want:
        lj["pause"] = want
        if not want:
            job_control._leave(lj)
    job["paused"] = bool(lj.get("paused")) and want
    job["sub_progress"] = lj.get("progress")
    job["sub_phase"] = lj.get("phase")
    job["sub_eta_seconds"] = lj.get("eta_seconds")


async def _await_lab_job(job: Dict, lab_job_id: str, db=None) -> Dict:
    last_pause = None
    while True:
        lj = lab.JOBS.get(lab_job_id) or {}
        if lj:
            _mirror_sub(job, lj)
            # Lokaler Worker: Pause-Flag zusätzlich in db.local_jobs spiegeln
            # (überlebt Server-Neustarts – wie bei Regime-Lab/Optimizer)
            if lj.get("pause") != last_pause and "local" in (
                    lj.get("execution"), (lj.get("params") or {}).get("execution")):
                last_pause = lj.get("pause")
                await job_control.persist_pause(db, lab_job_id, bool(last_pause))
        if lj.get("status") in ("done", "error", "cancelled") or not lj:
            job["paused"] = False
            return lj
        await asyncio.sleep(POLL_S)


async def _run_lab(job: Dict, kind_fn: str, body: Dict, analysis: Dict, deps: Dict) -> Dict:
    """Einen Regime-Lab-Job (regime_opt / walkforward) cloud oder lokal ausführen
    und auf das Ergebnis warten – exakt derselbe Weg wie die Regime-Lab-Buttons."""
    from services import regime_opt
    params = {k: body.get(k) for k in ("analysis_id", "scope", "symbol", "regime_id",
                                       "mode", "strategy_id")}
    params["execution"] = body.get("execution") or "cloud"
    params["workbench_job"] = job["id"]
    wid = (job.get("params") or {}).get("worker_id")
    if params["execution"] == "local" and wid:
        body = {**body, "worker_id": wid}
    ljid = lab.create_job("regime_opt" if kind_fn == "regime_opt" else "walkforward", params)
    if params["execution"] == "local":
        deps["enqueue_local"](kind_fn, ljid, {**body, "analysis_doc": deps["slim"](analysis)})
    else:
        fn = regime_opt.run_regime_optimizer if kind_fn == "regime_opt" else regime_opt.run_walkforward
        asyncio.create_task(fn(ljid, body, deps["registry"], deps["settings"],
                               deps["default_cfg"], deps["db"]))
    lj = await _await_lab_job(job, ljid, deps.get("db"))
    if lj.get("status") == "error":
        raise RuntimeError(lj.get("error") or f"{kind_fn} fehlgeschlagen")
    if lj.get("status") == "cancelled" and job.get("cancel"):
        raise asyncio.CancelledError()
    return {**(lj.get("result") or {}), "_lab_job_id": ljid}


async def _assign(db, aid: str, scope: str, symbol: Optional[str], rid: int,
                  cand: Optional[Dict], model: Dict, subset: Optional[List[str]] = None):
    field = lab.area_fields(subset)[0]
    doc = await db.regime_analyses.find_one({"id": aid}, {field: 1})
    assignments = dict((doc or {}).get(field) or {})
    key = f"{lab.area_key(scope, symbol, subset)}:{rid}"
    if cand is None:
        assignments.pop(key, None)
    else:
        reg = next((r for r in model.get("regimes") or [] if r["id"] == rid), {})
        assignments[key] = {"regime_id": rid, "regime_label": reg.get("label"),
                            **{k: cand.get(k) for k in
                               ("mode", "strategy_id", "strategy_name", "definition",
                                "trade_params", "strategy_params", "metrics",
                                "validation", "source_job_id", "score",
                                "validation_passed")},
                            "rules": cand.get("rules") or [],
                            "source": "dynamic_workbench", "assigned_at": _now_iso()}
    await db.regime_analyses.update_one({"id": aid}, {"$set": {field: assignments}})


def _search_body(p: Dict, aid: str, rid: int, mode: str, strategy_id: Optional[str],
                 round_no: int) -> Dict:
    base_it = int(min(max(int(p.get("iterations") or 40), 5), 500))
    body = {"analysis_id": aid, "scope": p.get("scope") or "combined",
            "symbol": p.get("symbol"), "regime_id": rid, "mode": mode,
            "objective": p.get("objective") or "combo",
            "iterations": min(base_it + 10 * (round_no - 1), 500),
            "min_trades": int(p.get("min_trades") or 10),
            # Endlos-Suche: je Runde andere Regel-Tiefe + andere Stichprobe
            "max_rules": int(p.get("max_rules") or (2 + (round_no - 1) % 5)),
            "seed": 1000 + rid + 7919 * round_no,
            "direction_bias": p.get("direction_bias") or "off",
            "optimize_strategy_params": bool(p.get("optimize_strategy_params", True)),
            "execution": p.get("execution") or "cloud"}
    if p.get("symbols"):
        body["symbols"] = list(p["symbols"])
    if p.get("reuse_key"):
        body["reuse_key"] = p["reuse_key"]
    # Optionale Einstellungen wie im klassischen Optimizer – nur setzen, wenn
    # angegeben (regime_opt behält sonst seine bisherigen Defaults)
    for k in PASSTHROUGH_KEYS:
        if p.get(k) is not None:
            body[k] = p[k]
    if strategy_id:
        body["strategy_id"] = strategy_id
        if mode in ("discovery", "combo") and p.get("start_from_current"):
            body["base_strategy_id"] = strategy_id
    return body


async def _local_result_backtest(job: Dict, doc: Dict, days: int, cfg: Dict, p: Dict,
                                 deps: Dict) -> Optional[Dict]:
    """Ergebnis-Backtest auf dem lokalen Worker (der hat die Kerzen schon) statt
    alle Assets nochmal auf dem Server zu laden. None = nicht lokal möglich
    (Cloud-Ausführung, kein/zu alter Worker) -> Aufrufer rechnet in der Cloud."""
    from services import backtester as bt
    from services import local_exec
    if (p.get("execution") or "cloud") != "local" or not local_exec.worker_online():
        return None
    wid = (job.get("params") or {}).get("worker_id") or p.get("worker_id")
    if not local_exec.worker_supports(local_exec.DYNAMIC_BACKTEST_MIN_VERSION, wid):
        return None
    did, syms, tf = doc["id"], list(doc.get("symbols") or []), p.get("timeframe") or None
    dyn_doc = {k: v for k, v in doc.items() if k not in ("last_state", "runtime_state", "pending_switch")}
    bjid = bt.create_job({"strategy_ids": [did], "symbols": syms, "days": days,
                          "execution": "local", "workbench_job": job["id"]})
    local_exec.enqueue_compute("backtest", bjid, {
        "kind": "backtest",
        "args": {"strategy_ids": [did], "symbols": syms, "days": days, "cfg": cfg,
                 "settings": dict(deps["settings"]), "strategy_configs": {did: {"timeframe": tf}} if tf else {},
                 "default_timeframe": tf, "dynamic_docs": {did: dyn_doc}},
        "custom_definitions": deps["registry"].list_custom_definitions(),
    }, worker_id=wid)
    _log(job, "Ergebnis-Backtest auf dem lokalen Worker (Kerzen dort schon vorhanden) ...")
    while True:
        bj = bt.JOBS.get(bjid) or {}
        if job.get("cancel") and bj:
            bj["cancel"] = True
        if not bj or bj.get("status") in ("done", "error", "cancelled"):
            break
        job["progress"] = 90 + round((bj.get("progress") or 0) / 10)
        await asyncio.sleep(POLL_S)
    if bj.get("status") != "done":
        raise RuntimeError(bj.get("error") or "Ergebnis-Backtest auf dem Worker fehlgeschlagen")
    bd = ((bj.get("result") or {}).get("dynamic_breakdown") or {}).get(did)
    if not bd:
        raise RuntimeError("Worker lieferte keine Aufschlüsselung je Regime")
    return bd


async def _result_backtest(job: Dict, built_id: str, p: Dict, deps: Dict) -> Optional[Dict]:
    """Ergebnis-Backtest der fertigen dynamischen Strategie über den vollen
    Analyse-Zeitraum: Gesamt + je Regime + Empfehlung (services.dynamic_backtest).
    Fehler hier brechen den Job nicht ab – die Strategie ist bereits gebaut."""
    from services import dynamic_backtest
    from services.bitunix_trade import DEFAULT_COIN_CFG
    db = deps["db"]
    doc = await db.dynamic_strategies.find_one({"id": built_id}, {"_id": 0})
    if not doc:
        return None
    analysis = await db.regime_analyses.find_one({"id": p["analysis_id"]}, {"days": 1})
    days = int(p.get("days") or (analysis or {}).get("days") or 180)
    cfg = dict(deps.get("default_cfg") or DEFAULT_COIN_CFG)
    for k in ("max_capital", "leverage", "fee_percent", "sessions"):
        if p.get(k) is not None:
            cfg[k] = p[k]
    try:
        local = await _local_result_backtest(job, doc, days, cfg, p, deps)
        if local is not None:
            return {**local, "days": days, "execution": "local",
                    "config": {k: cfg.get(k) for k in ("max_capital", "leverage", "fee_percent")}}
        _log(job, "Ergebnis-Backtest: Gesamt und je Regime ...")
        res = await dynamic_backtest.simulate_dynamic(
            doc, list(doc.get("symbols") or []), days, cfg, deps["settings"], deps["registry"],
            job, lambda: bool(job.get("cancel")), timeframe=p.get("timeframe"),
            progress=(90, 100))
        return {**res["breakdown"], "days": days,
                "config": {k: cfg.get(k) for k in ("max_capital", "leverage", "fee_percent")}}
    except Exception as e:  # noqa: BLE001
        logger.warning(f"workbench result backtest failed: {e}")
        _log(job, f"Ergebnis-Backtest nicht möglich: {str(e)[:160]}")
        return {"error": str(e)[:200]}


def regime_should_run(state: Dict, round_no: int) -> bool:
    """Plateau-Steuerung (rein): ausgefallene Regime nie, ausgereizte nur jede
    RETRY_EVERY-te Runde, sonst immer."""
    if state.get("dead"):
        return False
    return state.get("stale", 0) < PLATEAU_ROUNDS or round_no % RETRY_EVERY == 0


def all_exhausted(states: Dict[int, Dict]) -> bool:
    """Nichts mehr zu holen: jedes Regime ausgefallen oder ausgereizt (rein)."""
    return all(st.get("dead") or st.get("stale", 0) >= PLATEAU_ROUNDS for st in states.values())


def is_no_data_error(msg: str) -> bool:
    return any(h in (msg or "") for h in _NO_DATA_HINTS)


async def run(job_id: str, p: Dict, deps: Dict):
    """Gemeinsamer Ablauf für refine / create / discover."""
    job = JOBS[job_id]
    db = deps["db"]
    try:
        aid = p["analysis_id"]
        scope = p.get("scope") or "combined"
        symbol = p.get("symbol")
        subset = p.get("symbols") or None
        p = {**p, "reuse_key": job_id}
        analysis = await db.regime_analyses.find_one({"id": aid}, lab.NO_CHART)
        if not analysis:
            raise RuntimeError("Regime-Analyse nicht gefunden")
        analysis.pop("_id", None)
        model = lab.model_for(analysis, scope, symbol) or {}
        regimes = {r["id"]: r for r in model.get("regimes") or []}
        targets: Dict[int, Dict] = {int(k): v for k, v in (p.get("targets") or {}).items()}
        if not targets:
            raise RuntimeError("Keine Regime ausgewählt")
        kind = job["kind"]
        best: Dict[int, Dict] = {int(k): v for k, v in (p.get("current") or {}).items() if v}
        last_errors: List[str] = []

        if kind == "create":
            for rid, t in targets.items():
                sid = t.get("strategy_id")
                best[rid] = ({"mode": "params", "strategy_id": sid,
                              "strategy_name": deps["registry"].get(sid).STRATEGY_NAME,
                              "definition": None, "rules": [],
                              "trade_params": t.get("trade_params") or {},
                              "strategy_params": t.get("strategy_params") or {}}
                             if sid and deps["registry"].get(sid) else None)
        else:
            endless = bool(p.get("endless"))
            max_rounds = 10_000 if endless else max(int(p.get("rounds") or 1), 1)
            states: Dict[int, Dict] = {rid: {"stale": 0, "fails": 0} for rid in targets}
            round_no = 0
            while round_no < max_rounds and not job.get("stop"):
                round_no += 1
                job["round"] = round_no
                improved_round = 0
                runnable = [(rid, t) for rid, t in sorted(targets.items())
                            if regime_should_run(states[rid], round_no)]
                for i, (rid, t) in enumerate(runnable):
                    await job_control.wait_if_paused(job)
                    if job.get("stop") or job.get("cancel"):
                        break
                    st = states[rid]
                    label = (regimes.get(rid) or {}).get("label") or f"#{rid + 1}"
                    mode = t.get("mode") or ("discovery" if kind == "discover" else "params")
                    sid = t.get("strategy_id")
                    if mode == "params" and not deps["registry"].get(sid or ""):
                        job["regimes"][str(rid)] = {"label": label, "note": "keine Strategie – übersprungen"}
                        st["dead"] = True
                        continue
                    _log(job, f"Runde {round_no} · Regime '{label}' ({i + 1}/{len(runnable)}) · "
                              f"{ {'params': 'Parameter', 'discovery': 'neue Regeln', 'combo': 'Regeln + Parameter'}[mode] }"
                              + (" · erneuter Versuch" if st["stale"] >= PLATEAU_ROUNDS else ""))
                    job["progress"] = round((i / max(len(runnable), 1)) * 100, 1) if not endless else job["progress"]
                    try:
                        res = await _run_lab(job, "regime_opt",
                                             _search_body(p, aid, rid, mode, sid, round_no),
                                             analysis, deps)
                    except RuntimeError as e:
                        # Ein Regime ohne Daten (z.B. auf den gewählten Assets) oder mit
                        # fehlgeschlagener Suche bricht nicht mehr den ganzen Lauf ab.
                        st["fails"] += 1
                        st["last_error"] = str(e)[:200]
                        no_data = is_no_data_error(str(e))
                        if no_data or st["fails"] >= MAX_REGIME_FAILURES:
                            st["dead"] = True
                        _log(job, f"Regime '{label}' {'ohne Daten – übersprungen' if no_data else 'Suche fehlgeschlagen'}: {str(e)[:140]}")
                        entry = job["regimes"].setdefault(str(rid), {"label": label})
                        if not best.get(rid):
                            entry["note"] = ("keine Daten auf den gewählten Assets" if no_data
                                             else f"Suche fehlgeschlagen: {str(e)[:80]}")
                        continue
                    top = (res.get("top5") or [None])[0]
                    cand = candidate_from_result(res, top, res.get("_lab_job_id") or "") if top else None
                    prev = best.get(rid)
                    if cand and candidate_rank(cand) > candidate_rank(prev):
                        best[rid] = cand
                        improved_round += 1
                        st["stale"] = 0
                        job["regimes"][str(rid)] = {
                            "label": label, "score": cand.get("score"),
                            "validation_passed": cand.get("validation_passed"),
                            "pnl": (cand.get("metrics") or {}).get("pnl"),
                            "trades": (cand.get("metrics") or {}).get("trades"),
                            "strategy": cand.get("strategy_name") or "Eigene Regeln",
                            "improved_round": round_no}
                    else:
                        st["stale"] += 1
                        if st["stale"] >= PLATEAU_ROUNDS and str(rid) in job["regimes"]:
                            job["regimes"][str(rid)]["state"] = (
                                f"ausgereizt – nur jede {RETRY_EVERY}. Runde")
                if runnable:
                    _log(job, f"Runde {round_no} fertig · {improved_round} Regime verbessert")
                if all(st.get("dead") for st in states.values()):
                    break
                if not endless:
                    job["progress"] = round(round_no / max_rounds * 80, 1)
                    if all_exhausted(states) and round_no < max_rounds:
                        _log(job, f"Alle Regime ausgereizt (je {PLATEAU_ROUNDS} Runden ohne "
                                  f"Verbesserung) – restliche Runden übersprungen")
                        break
            job["search_state"] = {str(r): {k: v for k, v in st.items() if k != "last_error"}
                                   for r, st in states.items()}
            last_errors = [st["last_error"] for st in states.values() if st.get("last_error")]
        await job_control.wait_if_paused(job)
        if job.get("cancel"):
            raise asyncio.CancelledError()
        found = {rid: c for rid, c in best.items() if c}
        if not found:
            hint = last_errors[0] if last_errors else None
            raise RuntimeError("Kein verwertbares Ergebnis – kein Regime hat eine Strategie"
                               + (f" (zuletzt: {hint})" if hint else ""))
        # Zuordnungen schreiben: gesuchte/gewählte Regime + übernommene übrige
        for rid in regimes:
            if rid in targets or rid in best:
                await _assign(db, aid, scope, symbol, rid, best.get(rid), model, subset)
        # Basis-Strategie (nötig für Regime ohne eigene Regel-Definition):
        # explizit gewählt, sonst die erste zugeordnete Registry-Strategie
        base_sid = p.get("base_strategy_id") or next(
            (c["strategy_id"] for c in found.values()
             if c.get("strategy_id") and deps["registry"].get(c["strategy_id"])), None)
        wf = None
        if p.get("walkforward", True) and (analysis.get("settings") or {}).get("train_pct", 100) < 100:
            _log(job, "Finaler Walk-Forward auf dem unangetasteten Holdout ...")
            job["stop"] = False
            # Frisch laden: der Walk-Forward braucht die GERADE geschriebenen
            # Zuordnungen (der lokale Worker bekommt das Dokument mitgeschickt).
            analysis = {**analysis, **{k: v for k, v in (await db.regime_analyses.find_one(
                {"id": aid}, {"_id": 0, "assignments": 1, lab.SUBSET_ASSIGNMENTS: 1, "kept": 1}) or {}).items()}}
            wf_body = {"analysis_id": aid, "scope": scope, "symbol": symbol,
                       "strategy_id": base_sid, "execution": p.get("execution") or "cloud"}
            if subset:
                wf_body["symbols"] = list(subset)
            try:
                wf = await _run_lab(job, "walkforward", wf_body, analysis, deps)
            except RuntimeError as e:
                _log(job, f"Walk-Forward nicht möglich: {e}")
        _log(job, "Dynamische Strategie wird gebaut ...")
        skipped = losing_regimes(wf, int(p.get("min_trades_skip") or MIN_TRADES_SKIP)) \
            if wf and p.get("skip_losing", True) else []
        if skipped and len(skipped) >= len(found):
            _log(job, "Alle Regime im Walk-Forward negativ – nichts wird abgeschaltet, "
                      "Strategie bitte nicht freigeben")
            skipped = []
        for s in skipped:
            _log(job, f"Regime '{s['label']}' wird NICHT gehandelt: Walk-Forward PnL {s['pnl']} "
                      f"über {s['trades']} Trades")
        built = await deps["build"](aid, {"scope": scope, "symbol": symbol,
                                          "symbols": list(subset) if subset else None,
                                          "strategy_id": base_sid,
                                          "name": p.get("name"),
                                          "skip_regimes": [s["regime"] for s in skipped]})
        if kind == "refine" and p.get("dynamic_id") and built.get("id"):
            carried = await _link_refined(db, deps, p["dynamic_id"], built["id"], list(found),
                                          aid, scope, symbol, subset)
            if carried:
                _log(job, f"Deine Phasen-Anpassungen bleiben in Regime {carried} erhalten "
                          "(dort nichts verbessert)")
        job["result"] = {"dynamic_id": built.get("id"), "analysis_id": aid,
                         "regimes": job["regimes"],
                         "walkforward": {"verdict": (wf or {}).get("verdict"),
                                         "dynamic_test": (wf or {}).get("dynamic_test"),
                                         "best_single": (wf or {}).get("best_single"),
                                         "per_regime": (wf or {}).get("per_regime"),
                                         "switches": (wf or {}).get("switches"),
                                         "skipped_regimes": skipped,
                                         "kept_after_skip": kept_after_skip(wf, skipped)} if wf else None,
                         "rounds": job["round"], "source_dynamic_id": p.get("dynamic_id"),
                         "symbols": list(subset) if subset else list(analysis.get("symbols") or []),
                         "subset": bool(subset), "search_state": job.get("search_state")}
        if p.get("result_backtest", True) and built.get("id"):
            job["result"]["backtest"] = await _result_backtest(job, built["id"], p, deps)
        job["status"] = "done"
        job["progress"] = 100
        _log(job, "Fertig – neue dynamische Strategie angelegt")
    except asyncio.CancelledError:
        job["status"] = "cancelled"
        _log(job, "Abgebrochen")
    except Exception as e:  # noqa: BLE001
        logger.exception(f"dynamic workbench {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
        _log(job, f"Fehler: {str(e)[:200]}")
    finally:
        from services import regime_opt
        regime_opt.clear_history_slot(job_id)


async def _link_refined(db, deps: Dict, src_id: str, new_id: str, improved: List[int],
                        aid: str, scope: str, symbol: Optional[str], subset) -> List[int]:
    """Herkunft + Verlauf der neuen Version; deine Phasen-Anpassungen bleiben in
    Phasen erhalten, die dieser Lauf nicht verbessert hat (services/dynamic_versions)."""
    from services import dynamic_versions as dv
    from services import dynamic_runtime
    try:
        field = lab.area_fields(subset)[0]
        analysis = await db.regime_analyses.find_one({"id": aid}, {"_id": 0, field: 1}) or {}
        res = await dv.after_refine(db, deps["registry"], src_id, new_id, improved,
                                    current_candidates(analysis, scope, symbol, subset))
        await dynamic_runtime.reload(new_id)
        return (res or {}).get("carried") or []
    except Exception as e:  # noqa: BLE001 – Verlauf darf das Ergebnis nicht verhindern
        logger.warning(f"refine lineage {new_id}: {e}")
        return []


def current_candidates(analysis: Dict, scope: str, symbol: Optional[str],
                       subset: Optional[List[str]] = None) -> Dict[int, Dict]:
    """Bestehende Zuordnungen eines Bereichs als Startwerte (rein)."""
    key = lab.area_key(scope, symbol, subset)
    out = {}
    for k, a in (analysis.get(lab.area_fields(subset)[0]) or {}).items():
        if k.startswith(key + ":"):
            out[int(k.rsplit(":", 1)[1])] = a
    return out


def scope_of(doc: Dict) -> tuple:
    """(scope, symbol) einer dynamischen Strategie aus dem Regime-Lab (rein)."""
    sk = (doc.get("settings") or {}).get("scope_key") or ""
    if sk.startswith("per_coin:"):
        return "per_coin", sk.split(":", 1)[1]
    syms = doc.get("symbols") or []
    if (doc.get("settings") or {}).get("scope") == "per_coin" and len(syms) == 1:
        return "per_coin", syms[0]
    return "combined", None


def subset_of(doc: Dict) -> Optional[List[str]]:
    """Asset-Teilmenge, auf der eine Werkbank-Strategie optimiert wurde (rein)."""
    return list((doc.get("settings") or {}).get("subset_symbols") or []) or None


def regime_strategy_of(doc: Dict, rid: int) -> Optional[str]:
    return ((doc.get("regime_strategies") or {}).get(str(rid))
            or (None if (doc.get("sub_strategies") or {}).get(str(rid)) else doc.get("strategy_id")))


def targets_for_refine(doc: Dict, regime_ids: List[int], mode: str) -> Dict[int, Dict]:
    """Regime mit eigenen Discovery-Regeln haben keine Registry-Strategie für
    reine Parameter-Suche -> dort 'combo' (Regeln + Parameter); übernommen wird
    trotzdem nur, was die bestehende Zuordnung schlägt."""
    out = {}
    for rid in regime_ids:
        sid = regime_strategy_of(doc, int(rid))
        own = bool((doc.get("sub_strategies") or {}).get(str(rid)))
        out[int(rid)] = {"mode": "combo" if (mode == "params" and own) else mode,
                         "strategy_id": sid}
    return out
