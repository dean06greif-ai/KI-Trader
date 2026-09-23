"""Regime-Lab Endpoints: Analysen erstellen/ansehen, Regime behalten/verwerfen,
Strategie-Suche je Regime, Zuordnungen bestätigen, dynamische Strategie bauen
und final per Walk-Forward auf dem Holdout testen."""
import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException, Request

from core import state
from core.auth import require_admin
from core.state import scanner
from core.utils import _clean, _job_public, _watch_job_task
from services import job_control, ram_queue
from services import regime_lab as lab
from services.ai_engine import ai_engine
from services import regime_opt, regime_quality, research_dataset, research_validation, strategy_release
from services.bitunix_trade import DEFAULT_COIN_CFG
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["regime-lab"])


def _guard_no_running():
    j = lab.running_job()
    if j:
        raise HTTPException(status_code=409,
                            detail=f"Es läuft bereits ein Regime-Lab-Job ({j['kind']})")


def _regime_id_or_400(body: Dict) -> int:
    rid = body.get("regime_id")
    if rid is None or not str(rid).lstrip("-").isdigit():
        raise HTTPException(status_code=400, detail="regime_id (Zahl) erforderlich")
    return int(rid)


async def _get_doc(aid: str) -> Dict:
    doc = await state.db.regime_analyses.find_one({"id": aid})
    if not doc:
        raise HTTPException(status_code=404, detail="Regime-Analyse nicht gefunden")
    return doc


def _slim_doc(doc: Dict) -> Dict:
    """Analyse-Dokument für den Worker-Payload: ohne Chart-Daten (unnötig groß)."""
    return {k: v for k, v in _clean(doc).items() if k != "chart"}


def _enqueue_local(kind_fn: str, job_id: str, body: Dict):
    from services import local_exec
    local_exec.enqueue_compute("regime_lab", job_id, {
        "kind": "regime_lab",
        "args": {"fn": kind_fn, "body": body,
                 "settings": dict(scanner.settings),
                 "default_cfg": dict(DEFAULT_COIN_CFG)},
        "custom_definitions": strategy_registry.list_custom_definitions(),
    })


def _check_local_available():
    from services import local_exec
    if not local_exec.worker_online():
        raise HTTPException(status_code=503,
                            detail="Kein lokaler Worker verbunden – Worker starten "
                                   "oder Cloud-Ausführung wählen")
    if not local_exec.worker_supports_regime_lab():
        raise HTTPException(status_code=409,
                            detail="Der verbundene lokale Worker ist veraltet und kennt "
                                   "Regime-Lab-Jobs noch nicht. Bitte das Worker-Paket neu "
                                   "herunterladen (Ausführung → Lokal → ⚙ Verwalten → "
                                   "Download) und den Worker neu starten – oder "
                                   "Cloud-Ausführung wählen.")


@router.post("/api/regime-lab/analyze")
async def start_analysis(body: Dict, _: bool = Depends(require_admin)):
    """Regime-Analyse starten: Regime für Coins/Timeframe/Zeitraum suchen und
    speichern – kombiniert über alle Coins und je Coin einzeln.
    execution=local rechnet auf dem lokalen Worker (empfohlen ab ~1000 Tagen)."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    if (body.get("scope") or "both") not in ("both", "combined", "per_coin"):
        raise HTTPException(status_code=400, detail="scope muss both|combined|per_coin sein")
    if (body.get("engine") or "v2").lower() not in ("v2", "kmeans"):
        raise HTTPException(status_code=400, detail="engine muss v2|kmeans sein")
    _guard_no_running()
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "scope", "max_regimes",
               "lookback_days", "min_share_pct", "confidence_min",
               "min_hold_days", "train_pct", "name", "engine", "engine_config")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("analysis", params)
        _enqueue_local("analysis", job_id, body)
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("analysis", params)
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: lab.run_analysis(job_id, body, state.db),
                              kind="regime_analysis")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/calibrate")
async def start_calibration(body: Dict, _: bool = Depends(require_admin)):
    """Wissenschaftliche Kalibrierung: Referenz-Regime (zentriert/HMM) berechnen
    und die Engine-Parameter per Suche daran ausrichten (kein Raten)."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    if (body.get("truth_source") or "centered").lower() not in ("centered", "hmm", "vote"):
        raise HTTPException(status_code=400, detail="truth_source muss centered|hmm|vote sein")
    _guard_no_running()
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "engine_config", "truth_source")}
    params["execution"] = execution
    job_id = lab.create_job("calibration", params)
    if execution == "local":
        _check_local_available()
        _enqueue_local("calibrate", job_id, body)
        return {"status": "started", "job_id": job_id, "execution": "local"}
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: lab.run_calibration(job_id, body, state.db),
                              kind="regime_calibration")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/ema-compare")
async def start_ema_compare(body: Dict, _: bool = Depends(require_admin)):
    """EMA-Perioden-Vergleich (z.B. 5/9/14) für den Detektor 'ema': gleiche
    Daten, mehrere Perioden, Kennzahlen als Tabelle (Live=Final-Treffer,
    Holdout, Phasendauern, Prüfung)."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    _guard_no_running()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "periods", "train_pct",
               "engine_config")}
    job_id = lab.create_job("ema_compare", params)
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: lab.run_ema_compare(job_id, body, state.db),
                              kind="regime_ema_compare")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/ablation")
async def start_ablation(body: Dict, _: bool = Depends(require_admin)):
    """Indikator-Ablation (AP13): konfigurierter Detektor voll vs. ohne je
    eine Bestätigungs-Komponente vs. einfache Alternative – Auswahl über die
    innere Validierung, Holdout bleibt finaler Test; je Variante
    Anlageklassen-Pooling mit Symbol-Abweichung."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    _guard_no_running()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "train_pct", "engine_config")}
    job_id = lab.create_job("ablation", params)
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: lab.run_ablation(job_id, body, state.db),
                              kind="regime_ablation")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/kombi-calibrate")
async def start_kombi_calibrate(body: Dict, _: bool = Depends(require_admin)):
    """Auto-Kalibrierung Kombi-Detektor: findet Trend-Schwelle und
    Steigungs-Fenster, mit denen die mittlere Final-Phasendauer im
    5-15-Tage-Zielband liegt und die Holdout-Trefferquote maximal ist."""
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    _guard_no_running()
    params = {k: body.get(k) for k in
              ("symbols", "timeframe", "days", "train_pct", "thr_grid",
               "slope_grid", "target_min_days", "target_max_days",
               "engine_config")}
    job_id = lab.create_job("kombi_calibrate", params)
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: lab.run_kombi_calibrate(job_id, body, state.db),
                              kind="regime_kombi")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.get("/api/regime-lab/status/{job_id}")
async def job_status(job_id: str):
    job = lab.JOBS.get(job_id)
    if not job:
        # Nach einem Backend-Neustart (Deploy) ist die In-Memory-Job-Liste
        # leer, das fertige Ergebnis aber gespeichert. Fallback: Analyse,
        # Kalibrierung oder Forschungs-Lauf mit dieser job_id suchen, damit
        # die Oberfläche sauber abschließt statt still zu verschwinden.
        doc = await state.db.regime_analyses.find_one({"job_id": job_id},
                                                      {"id": 1, "kind": 1})
        if doc:
            return {"id": job_id, "kind": "analysis", "status": "done",
                    "progress": 100, "phase": "Fertig",
                    "result": {"kind": "analysis", "analysis_id": doc["id"]}}
        cal = await state.db.regime_calibrations.find_one({"id": job_id}, {"_id": 0})
        if cal:
            return {"id": job_id, "kind": "calibration", "status": "done",
                    "progress": 100, "phase": "Fertig",
                    "result": {"kind": "calibration", "report": cal.get("report")}}
        run = await state.db.regime_lab_runs.find_one({"id": job_id}, {"_id": 0})
        if run:
            return {"id": job_id, "kind": (run.get("result") or {}).get("kind"),
                    "status": "done", "progress": 100, "phase": "Fertig",
                    "result": run.get("result")}
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _job_public(job)


@router.get("/api/regime-lab/calibrations")
async def list_calibrations(limit: int = 20, include_autopilot: bool = True):
    """Verlauf ALLER Kalibrierungen (Cloud + lokaler Worker): wissenschaftliche
    Kalibrierung UND Autopilot-Läufe in einem Zeilenformat (`source`,
    `report.metric`, `report.best_config`) – kompakt, ohne Rohdaten – zum
    Nachschlagen und jederzeit erneuten Übernehmen."""
    from services import regime_calibration_history as hist
    return {"calibrations": await hist.list_history(state.db, limit, include_autopilot)}


@router.get("/api/regime-lab/engine/snapshots")
async def list_engine_snapshots(limit: int = 12):
    """Gesicherte Engine-Einstellungen (vor jeder Übernahme) – zum Zurückholen."""
    from services import regime_calibration_history as hist
    return {"snapshots": await hist.list_snapshots(state.db, limit)}


@router.post("/api/regime-lab/engine/snapshots")
async def save_engine_snapshot(body: Dict, _: bool = Depends(require_admin)):
    """Aktuellen Stand (engine_config + aktive Kalibrierung) sichern, BEVOR eine
    Kalibrierung/der Autopilot ihn überschreibt."""
    from services import regime_calibration_history as hist
    cfg = body.get("engine_config")
    if not isinstance(cfg, dict) or not cfg:
        raise HTTPException(status_code=400, detail="engine_config (Objekt) erforderlich")
    doc = await hist.save_snapshot(state.db, cfg, body.get("calib_applied"),
                                   body.get("reason") or "Übernahme")
    return {"status": "success", "snapshot": doc}


@router.post("/api/regime-lab/shadow/backfill")
async def shadow_backfill(body: Dict = None, _: bool = Depends(require_admin)):
    """Shadow-Phase beschleunigen: Struktur-Regime für bereits geschlossene
    KI-Trades im Reward-Fenster nachtragen (deterministisch, ohne Lookahead)."""
    from services import regime_shadow_backfill
    body = body or {}
    days = int(min(max(int(body.get("days") or regime_shadow_backfill.WINDOW_DAYS), 1), 365))
    summary = await regime_shadow_backfill.backfill_structural(state.db, days, body.get("aid"))
    return {"status": "success", **summary}


@router.get("/api/regime-lab/active")
async def active_job():
    j = lab.running_job()
    return {"active": _job_public(j) if j else None}


@router.post("/api/regime-lab/cancel/{job_id}")
async def cancel_job(job_id: str, _: bool = Depends(require_admin)):
    job = lab.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    job["cancel"] = True
    if job.get("status") == "running":
        job["phase"] = "Wird abgebrochen..."
    from services import local_exec
    local_exec.check_stale()  # wartende lokale Jobs sofort stornieren
    return {"status": "cancelling"}


# ---------------- Job-Steuerung wie im Strategie-Optimizer ----------------
# Pausieren / Fortsetzen / Suche beenden / Notfall-Reset für ALLE Regime-Lab-
# Jobs (Analyse, Kalibrierung, Regime-Suche, Walk-Forward, Autopilot, ...).
# Gleiche Semantik wie /api/optimizer/{pause,resume,explore/stop,reset}.
def _job_or_404(job_id: str) -> Dict:
    job = lab.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return job


@router.post("/api/regime-lab/pause/{job_id}")
async def pause_job(job_id: str, _: bool = Depends(require_admin)):
    """Job anhalten – Zwischenstand bleibt im Speicher (lokaler Worker:
    überlebt auch Server-Neustarts), Fortsetzen jederzeit."""
    job = _job_or_404(job_id)
    if not job_control.request_pause(job):
        raise HTTPException(status_code=409, detail="Job läuft nicht (mehr)")
    await job_control.persist_pause(state.db, job_id, True)
    return {"status": "pausing", "job_id": job_id}


@router.post("/api/regime-lab/resume/{job_id}")
async def resume_job(job_id: str, _: bool = Depends(require_admin)):
    job = _job_or_404(job_id)
    if not job_control.request_resume(job):
        raise HTTPException(status_code=409, detail="Job läuft nicht (mehr)")
    await job_control.persist_pause(state.db, job_id, False)
    return {"status": "resuming", "job_id": job_id}


@router.post("/api/regime-lab/stop/{job_id}")
async def stop_job(job_id: str, _: bool = Depends(require_admin)):
    """Sanfter Stop: Suche beenden, bisher Bestes behalten (im Gegensatz zu
    'cancel', das den Job komplett verwirft). Regime-Suche: bricht die
    Discovery-/Parameter-Suche am nächsten Checkpoint ab und prüft die
    besten Kandidaten; Autopilot: beendet die Runden, beste Erkennung bleibt."""
    job = _job_or_404(job_id)
    if not job_control.request_stop(job):
        raise HTTPException(status_code=409, detail="Job läuft nicht mehr")
    await job_control.persist_pause(state.db, job_id, False)
    return {"status": "stopping", "job_id": job_id,
            "info": "Suche wird beendet – beste Ergebnisse werden behalten"}


@router.post("/api/regime-lab/reset")
async def reset_jobs(_: bool = Depends(require_admin)):
    """Notfall-Reset: hängende/geisterhafte Regime-Lab-Jobs freigeben."""
    n = job_control.reset_running(lab.JOBS)
    from services import local_exec
    local_exec.check_stale()  # wartende lokale Jobs sofort stornieren
    return {"status": "reset", "cleared": n}


# ---------------- Regime-Autopilot (Endlos-Suche der Regime-Erkennung) ----------------
AUTOPILOT_PARAM_KEYS = ("symbols", "timeframe", "days", "train_pct", "engine_config",
                        "max_minutes", "target_pct", "search_detectors",
                        "min_phase_days_target", "max_phase_days_target", "max_rounds",
                        "auto_chain")


@router.post("/api/regime-lab/autopilot")
async def start_autopilot(body: Dict, _: bool = Depends(require_admin)):
    """Regime-Autopilot: verändert die Detektor-Einstellungen Runde für Runde,
    bewertet jede Variante mit der Live=Final-Kennzahl (Auswahl: innere
    Validierung, Holdout = finaler Test) und behält die beste – bis Zeitlimit,
    Ziel, Runden-Limit oder sanfter Stop. Cloud oder lokaler Worker (>= 1.12)."""
    from services import regime_autopilot
    symbols = [s for s in (body.get("symbols") or []) if isinstance(s, str)]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 Coin erforderlich")
    _guard_no_running()
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in AUTOPILOT_PARAM_KEYS}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        from services import local_exec
        if not local_exec.worker_supports_autopilot():
            raise HTTPException(status_code=409,
                                detail="Der verbundene lokale Worker ist veraltet und kennt den "
                                       "Regime-Autopilot noch nicht (Version 1.12.0 nötig). Bitte "
                                       "das Worker-Paket neu herunterladen und den Worker neu "
                                       "starten – oder Cloud-Ausführung wählen.")
        job_id = lab.create_job("autopilot", params)
        _enqueue_local("autopilot", job_id, body)
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("autopilot", params)
    queued = ram_queue.submit(lab.JOBS, job_id,
                              lambda: regime_autopilot.run_autopilot(job_id, body, state.db),
                              kind="regime_autopilot")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/autopilot/stop/{job_id}")
async def stop_autopilot(job_id: str, _: bool = Depends(require_admin)):
    """Sanfter Stop: Suche beenden, beste Erkennung behalten (kein Abbruch)."""
    job = _job_or_404(job_id)
    if not job_control.request_stop(job):
        raise HTTPException(status_code=409, detail="Job läuft nicht mehr")
    return {"status": "stopping", "info": "Autopilot wird beendet – beste Erkennung bleibt"}


@router.get("/api/regime-lab/autopilot/runs")
async def autopilot_runs(limit: int = 15):
    """Verlauf der Autopilot-Läufe (Cloud + lokaler Worker), kompakt."""
    rows = await state.db.regime_lab_runs.find(
        {"result.kind": "autopilot"}, {"_id": 0, "result.history": 0}) \
        .sort("created_at", -1).to_list(max(1, min(int(limit), 50)))
    return {"runs": rows}


@router.get("/api/regime-lab/list")
async def list_analyses():
    # Nur Übersichtsfelder laden – chart_emas (mehrere MB je Analyse) bremst
    # die Liste über Atlas sonst massiv aus.
    rows = await state.db.regime_analyses.find(
        {}, {"chart": 0, "chart_emas": 0, "combined.per_symbol": 0, "per_coin": 0}) \
        .sort("created_at", -1).to_list(lab.MAX_ANALYSES)
    out = []
    for r in rows:
        r = _clean(r)
        comb = r.get("combined") or {}
        out.append({"id": r["id"], "name": r.get("name"), "symbols": r.get("symbols"),
                    "timeframe": r.get("timeframe"), "days": r.get("days"),
                    "scope": r.get("scope"), "settings": r.get("settings"),
                    "created_at": r.get("created_at"),
                    "n_regimes_combined": len((comb.get("model") or {}).get("regimes") or []),
                    "n_assignments": len(r.get("assignments") or {}),
                    "has_walkforward": bool(r.get("walkforward")),
                    # AP10/R17: differenzierter WF-Status statt pauschal "getestet"
                    # (nicht geprüft / bestanden / nicht bestanden / veraltet)
                    "walkforward_passed": research_validation.walkforward_status(r)["passed"],
                    "walkforward_stale": research_validation.walkforward_status(r)["stale"],
                    # AP05/R06: 'pinned' = Datensatz-Manifest vorhanden;
                    # Bestandsanalysen = 'legacy_unpinned' (nicht reproduzierbar fixiert)
                    "dataset_status": research_dataset.dataset_status(r),
                    # Regime-Brücke 1.3: Freigabe-Stufe für Badge/Knöpfe
                    "release": r.get("release") or {"stage": "none"}})
    return {"analyses": out}


@router.get("/api/regime-lab/engine/defaults")
async def engine_defaults():
    """Standard-Einstellungen + Erklärungen der Regime-Engine v2 (für die
    Oberfläche) inklusive der 9er-Taxonomie und der NNFX-Zuordnung."""
    from services import regime_engine as eng
    return {"engine": eng.ENGINE, **eng.engine_defaults()}


@router.get("/api/regime-lab/run/{job_id}")
async def run_result(job_id: str):
    job = lab.JOBS.get(job_id)
    if job and job.get("result"):
        return {"result": job["result"]}
    doc = await state.db.regime_lab_runs.find_one({"id": job_id})
    if not doc:
        raise HTTPException(status_code=404, detail="Ergebnis nicht gefunden")
    return {"result": _clean(doc).get("result")}


@router.get("/api/regime-lab/releases")
async def list_releases():
    """Regime-Brücke 1.2: aktive Stufen je Klasse inkl. Nachweis + Shadow-Stichprobe
    (`rewards_by_structural`), damit die UI zeigt, ob `active` schon möglich ist.
    Stichproben-Ziel dynamisch nach Erkennungs-Note (strengste Shadow-Analyse)."""
    from services import ai_rewards, regime_release
    rows = await regime_release.all_releases(state.db)
    rewards = await ai_rewards.by_structural_regime(state.db, 90)
    min_trades = regime_release.ACTIVATION_MIN_TRADES
    shadow_ids = [r.get("id") for r in rows if r.get("id") and (r.get("release") or {}).get("stage") == "shadow"]
    if shadow_ids:
        docs = await state.db.regime_analyses.find(
            {"id": {"$in": shadow_ids}}, {"_id": 0, "chart": 0, "chart_emas": 0}).to_list(10)
        min_trades = max(regime_release.activation_min_trades(regime_release.quality_grade(d))
                         for d in docs) if docs else min_trades
    act_ok, act_reasons, act_info = regime_release.validate_activation(rewards, min_trades)
    autonomy = str(ai_engine.config.get(regime_release.CONFIG_KEY_AUTONOMY, "suggest"))
    pending = await state.db.ai_proposals.find(
        {"scope": regime_release.PROPOSAL_SCOPE,
         "status": {"$in": ["pending", "needs_data", "auto_applied"]}},
        {"_id": 0}).sort("ts", -1).to_list(10)
    return {"releases": rows, "rewards_by_structural": rewards,
            "activation": {"ok": act_ok, "reasons": act_reasons, **act_info},
            "autonomy": autonomy, "proposals": pending}


async def _release_gate(doc: Dict, stage: str, body: Dict):
    """Nachweis-Gate für shadow/active inkl. dynamischer Shadow-Stichprobe.
    Rückgabe: (ok, reasons, evidence, grade, min_trades)."""
    from services import ai_rewards, regime_release
    ok, reasons, evidence = regime_release.validate_release(
        doc, stage, body, await regime_release.jobs_for(state.db, doc))
    grade = regime_release.quality_grade(doc, evidence.get("asset_classes") or None)
    min_trades = regime_release.activation_min_trades(grade)
    if stage == "active":
        if (doc.get("release") or {}).get("stage") not in ("shadow", "active"):
            reasons.append("erst Stufe Shadow (Beobachten) freigeben")
        a_ok, a_reasons, _ = regime_release.validate_activation(
            await ai_rewards.by_structural_regime(state.db, 90, aid=doc.get("id")), min_trades)
        reasons += a_reasons
        ok = ok and a_ok and not reasons
    return ok, reasons, evidence, grade, min_trades


@router.get("/api/regime-lab/{aid}/release-check")
async def release_check(aid: str, stage: str = "shadow", scope: str = None, symbol: str = None):
    """Vorab-Prüfung für den Knopf (grau/aktiv + Tooltip mit fehlenden Nachweisen)
    inkl. Erkennungs-Note, Stichproben-Ziel und ob eine manuelle Freigabe möglich ist."""
    from services import regime_release
    doc = await _get_doc(aid)
    body = {"scope": scope or doc.get("scope"), "symbol": symbol}
    ok, reasons, evidence, grade, min_trades = await _release_gate(doc, stage, body)
    blockers = regime_release.override_blockers(stage, reasons, grade) if not ok else []
    return {"ok": ok, "reasons": reasons, "evidence": evidence,
            "quality_grade": grade, "activation_min_trades": min_trades,
            "override_possible": not ok and not blockers, "override_blockers": blockers,
            "release": doc.get("release") or {"stage": "none"}}


@router.post("/api/regime-lab/{aid}/release")
async def set_release(aid: str, body: Dict, request: Request, _: bool = Depends(require_admin)):
    """Stufe setzen: none | shadow | active. Bei fehlenden Nachweisen 400 mit ALLEN
    Gründen (kein Teil-Erfolg). Schreibt release.stage/evidence/history[].
    `override: true` + `override_ack: true` = manuelle Freigabe trotz fehlender
    (übersteuerbarer) Nachweise – wird in History/Audit protokolliert."""
    from services import regime_release
    from core.audit import log_action
    stage = str(body.get("stage") or "").lower()
    if stage not in regime_release.STAGES:
        raise HTTPException(status_code=400, detail="stage muss none|shadow|active sein")
    reason = str(body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="reason (kurz) ist Pflicht")
    doc = await _get_doc(aid)
    evidence: Dict = {}
    override_info = None
    if stage != "none":
        ok, reasons, evidence, grade, _min = await _release_gate(doc, stage, body)
        if not ok:
            if not body.get("override"):
                raise HTTPException(status_code=400, detail={"reasons": reasons})
            if not body.get("override_ack"):
                raise HTTPException(status_code=400, detail={"reasons": [
                    "Manuelle Freigabe: Risiko-Bestätigung (override_ack) fehlt"]})
            blockers = regime_release.override_blockers(stage, reasons, grade)
            if blockers:
                raise HTTPException(status_code=400, detail={"reasons": blockers})
            override_info = {"missing": reasons, "grade": grade}
            evidence = {**evidence, "manual_override": True, "override_missing": reasons[:10],
                        "quality_grade": grade}
            evidence["evidence_hash"] = regime_release.evidence_hash(evidence)
    rel = await regime_release.set_stage(state.db, aid, stage, evidence, "Admin", reason,
                                         override=override_info)
    await log_action(request, "regime_release", {"aid": aid, "stage": stage, "reason": reason,
                                                 "override": bool(override_info),
                                                 "evidence_hash": (evidence or {}).get("evidence_hash")})
    if stage in ("shadow", "active"):
        # Shadow-Phase beschleunigen: vorhandene Trades im Hintergrund nachlabeln
        from services import regime_shadow_backfill
        asyncio.create_task(regime_shadow_backfill.backfill_structural(
            state.db, regime_shadow_backfill.WINDOW_DAYS, aid))
    return {"status": "success", "release": rel}


@router.post("/api/regime-lab/release-proposals/{pid}/stop")
async def stop_release_proposal(pid: str, _: bool = Depends(require_admin)):
    """Karenz-Stopp (autonomy=auto): verhindert den Vollzug shadow -> active."""
    from services import regime_release
    prop = await state.db.ai_proposals.find_one(
        {"id": pid, "scope": regime_release.PROPOSAL_SCOPE, "applied_at": None}, {"_id": 0})
    if not prop:
        raise HTTPException(status_code=404, detail="Vorschlag nicht (mehr) offen")
    await state.db.ai_proposals.update_one(
        {"id": pid}, {"$set": {"status": "rejected", "decided_at": datetime.now(timezone.utc).isoformat(),
                               "decision_note": "Karenz-Stopp durch Trader"}})
    return {"status": "success"}


@router.get("/api/regime-lab/{aid}")
async def get_analysis(aid: str, full: bool = False):
    """Analyse-Dokument. Standard OHNE `chart`/`chart_emas` (ca. 2/3 der Größe,
    bei 20+ Assets mehrere MB -> Punkt 3 im Regime-Lab blieb sekundenlang leer);
    Chart-Daten holt das Frontend je Asset über /chart/{symbol}. `full=1`
    liefert wie bisher alles (Rückwärtskompatibilität)."""
    proj = None if full else {"chart": 0, "chart_emas": 0}
    doc = await state.db.regime_analyses.find_one({"id": aid}, proj)
    if not doc:
        raise HTTPException(status_code=404, detail="Regime-Analyse nicht gefunden")
    # Label-Migration: ältere Analysen auf die aktuelle Beschriftungs-Logik heben
    # (gezieltes $set statt replace_one – das Dokument ist hier ggf. projiziert).
    from services import regime as rg
    sets: Dict = {}
    comb_model = ((doc.get("combined") or {}).get("model"))
    if comb_model and rg.relabel_regimes(comb_model):
        sets["combined.model"] = comb_model
    for sym, pc in (doc.get("per_coin") or {}).items():
        if pc.get("model") and rg.relabel_regimes(pc["model"]):
            sets[f"per_coin.{sym}.model"] = pc["model"]
    if sets:
        await state.db.regime_analyses.update_one({"id": aid}, {"$set": sets})
    # Erkennungs-Qualität (Live=Final im Holdout je Anlageklasse) – wird aus
    # den gespeicherten Kennzahlen berechnet, gilt daher auch für Bestandsanalysen.
    return {"analysis": _clean(doc), "quality": regime_quality.summarize(doc),
            "charts_included": bool(full)}


@router.get("/api/regime-lab/{aid}/chart/{symbol}")
async def get_analysis_chart(aid: str, symbol: str):
    """Chart-Daten (Preise + EMAs) EINES Assets der Analyse – schlank per Projektion."""
    doc = await state.db.regime_analyses.find_one(
        {"id": aid}, {"_id": 0, f"chart.{symbol}": 1, f"chart_emas.{symbol}": 1})
    if doc is None:
        raise HTTPException(status_code=404, detail="Regime-Analyse nicht gefunden")
    return {"symbol": symbol, "prices": (doc.get("chart") or {}).get(symbol) or [],
            "emas": (doc.get("chart_emas") or {}).get(symbol)}


@router.get("/api/regime-lab/{aid}/transitions")
async def get_transitions(aid: str, scope: str = "combined",
                          symbol: str = None, view: str = "final"):
    """Regime-Übergangs-Matrix (Etappe 2): historische Übergangs-
    Wahrscheinlichkeiten zwischen den Regimen dieser Analyse – je Regime und
    auf Richtungs-Ebene (z.B. 'was folgt auf Seitwärts?'). view=final|live."""
    if scope not in ("combined", "per_coin"):
        raise HTTPException(status_code=400, detail="scope muss combined|per_coin sein")
    if view not in ("final", "live"):
        raise HTTPException(status_code=400, detail="view muss final|live sein")
    if scope == "per_coin" and not symbol:
        raise HTTPException(status_code=400, detail="symbol erforderlich bei scope=per_coin")
    doc = await _get_doc(aid)
    return lab.transition_matrix(_clean(doc), scope, symbol, view)


@router.delete("/api/regime-lab/{aid}")
async def delete_analysis(aid: str, _: bool = Depends(require_admin)):
    res = await state.db.regime_analyses.delete_one({"id": aid})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"status": "deleted"}


@router.post("/api/regime-lab/{aid}/rename")
async def rename_analysis(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Analyse nachträglich umbenennen (nur der Anzeigename – Modell, Nachweise
    und Freigabe bleiben unverändert)."""
    name = str(body.get("name") or "").strip()
    if not name or len(name) > 120:
        raise HTTPException(status_code=400, detail="name (1-120 Zeichen) erforderlich")
    res = await state.db.regime_analyses.update_one({"id": aid}, {"$set": {"name": name}})
    if not res.matched_count:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return {"status": "success", "id": aid, "name": name}


@router.post("/api/regime-lab/{aid}/keep/suggest")
async def keep_suggest(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Behalten-Vorschlag: Regime mit genug Abschnitten (≥ MIN_SEGMENTS_PER_REGIME)
    als 'behalten' markieren, dünne verwerfen. Vorhandene Häkchen bleiben."""
    from services import regime_release
    doc = await _get_doc(aid)
    kept = regime_release.suggest_kept(doc, body.get("scope") or "combined", body.get("symbol"))
    await state.db.regime_analyses.update_one({"id": aid}, {"$set": {"kept": kept}})
    n_keep = sum(1 for v in kept.values() if v)
    return {"status": "success", "kept": kept, "kept_count": n_keep, "discarded_count": len(kept) - n_keep}


@router.post("/api/regime-lab/{aid}/keep")
async def keep_regime(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Regime behalten/verwerfen (nur Markierung – verworfene Regime werden bei
    der Strategie-Suche und beim Zusammenbau übersprungen)."""
    doc = await _get_doc(aid)
    key = f"{lab.scope_key(body.get('scope') or 'combined', body.get('symbol'))}:" \
          f"{_regime_id_or_400(body)}"
    kept = dict(doc.get("kept") or {})
    kept[key] = bool(body.get("keep", True))
    await state.db.regime_analyses.update_one({"id": aid}, {"$set": {"kept": kept}})
    return {"status": "success", "kept": kept}


@router.post("/api/regime-lab/{aid}/optimize")
async def start_regime_optimize(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Strategie-Discovery/Optimierung NUR für ein ausgewähltes Regime dieser
    Analyse (alle Optimizer-Einstellungen verfügbar)."""
    doc = await _get_doc(aid)
    rid = _regime_id_or_400(body)
    kept_key = f"{lab.scope_key(body.get('scope') or 'combined', body.get('symbol'))}:{rid}"
    if (doc.get("kept") or {}).get(kept_key) is False:
        raise HTTPException(status_code=400,
                            detail="Dieses Regime wurde verworfen – erst wieder auf "
                                   "'behalten' setzen")
    mode = body.get("mode") or "combo"
    if mode not in ("params", "discovery", "combo"):
        raise HTTPException(status_code=400, detail="mode muss params|discovery|combo sein")
    if mode == "params" and not strategy_registry.get(body.get("strategy_id") or ""):
        raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
    _guard_no_running()
    body["analysis_id"] = aid
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("analysis_id", "scope", "symbol", "regime_id", "mode",
               "strategy_id", "timeframe", "objective", "iterations",
               "min_trades", "max_rules", "optimize_strategy_params",
               "strategy_param_keys", "include_flag_params", "direction_bias")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("regime_opt", params)
        _enqueue_local("regime_opt", job_id, {**body, "analysis_doc": _slim_doc(doc)})
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("regime_opt", params)
    queued = ram_queue.submit(lab.JOBS, job_id, lambda: regime_opt.run_regime_optimizer(
        job_id, body, strategy_registry, scanner.settings, DEFAULT_COIN_CFG, state.db),
        kind="regime_opt")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}


@router.post("/api/regime-lab/{aid}/assign")
async def assign_regime_strategy(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Gefundene Strategie/Konfiguration für ein Regime bestätigen (oder mit
    remove=true wieder entfernen)."""
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    rid = _regime_id_or_400(body)
    key = f"{lab.scope_key(scope, body.get('symbol'))}:{rid}"
    assignments = dict(doc.get("assignments") or {})
    if body.get("remove"):
        assignments.pop(key, None)
    else:
        cand = body.get("candidate") or {}
        model = lab.model_for(doc, scope, body.get("symbol")) or {}
        reg = next((r for r in model.get("regimes") or []
                    if r["id"] == rid), {})
        assignments[key] = {
            "regime_id": rid,
            "regime_label": reg.get("label"),
            "mode": cand.get("mode"),
            "strategy_id": cand.get("strategy_id"),
            "strategy_name": cand.get("strategy_name"),
            "definition": cand.get("definition"),
            "rules": cand.get("rules") or [],
            "trade_params": cand.get("trade_params") or {},
            "strategy_params": cand.get("strategy_params") or {},
            "metrics": cand.get("metrics"),
            "validation": cand.get("validation"),
            "source_job_id": cand.get("source_job_id"),
            "assigned_at": datetime.now(timezone.utc).isoformat(),
        }
    await state.db.regime_analyses.update_one({"id": aid},
                                              {"$set": {"assignments": assignments}})
    return {"status": "success", "assignments": assignments}


@router.post("/api/regime-lab/{aid}/build")
async def build_dynamic(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Aus den bestätigten Regime-Strategien eine dynamische Strategie erzeugen
    (gleiches Format wie der Dynamik-Modus – Live-Umschaltung, Auto-Prüfung etc.
    funktionieren sofort)."""
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    symbol = body.get("symbol")
    model = lab.model_for(doc, scope, symbol)
    if not model:
        raise HTTPException(status_code=400, detail="Kein Regime-Modell für diesen Bereich")
    assignments = regime_opt._assignment_items(doc, scope, symbol)
    if not assignments:
        raise HTTPException(status_code=400, detail="Keine bestätigten Regime-Strategien")
    sid = body.get("strategy_id")
    needs_base = any(not a.get("definition") for a in assignments.values())
    strategy = strategy_registry.get(sid or "")
    if not strategy:
        if needs_base:
            raise HTTPException(status_code=400,
                                detail="Basis-Strategie erforderlich (mind. ein Regime "
                                       "ohne eigene Regel-Definition)")
        sid = next(a.get("strategy_id") for a in assignments.values()
                   if a.get("definition")) or None
    # Regime ohne eigene Definition nutzen die Basis-Strategie; falls kein sid
    # aus der Registry, die erste Definition als Custom-Strategie registrieren.
    if not strategy_registry.get(sid or ""):
        first = next(a for a in assignments.values() if a.get("definition"))
        new_sid = f"custom_{uuid.uuid4().hex[:8]}"
        definition = {**first["definition"], "id": new_sid,
                      "name": (body.get("name") or "Regime-Lab") + " (Basis)",
                      "timeframe": doc.get("timeframe"),
                      "description": "Vom Regime-Lab erzeugte Basis-Strategie"}
        await state.db.custom_strategies.update_one({"id": new_sid},
                                                    {"$set": definition}, upsert=True)
        strategy_registry.upsert_custom(definition)
        sid = new_sid
    did = f"dyn_{uuid.uuid4().hex[:8]}"
    wf = (doc.get("walkforward") or {}).get(lab.scope_key(scope, symbol)) or {}
    # Regime, die eine eigene Registry-Strategie (z.B. NNFX) nutzen, werden live
    # per Strategie-Umschaltung bedient; Regime-spezifische Strategie-Parameter
    # kommen aus den Zuordnungen.
    regime_strategies = {str(rid): a.get("strategy_id")
                         for rid, a in assignments.items()
                         if not a.get("definition") and strategy_registry.get(a.get("strategy_id") or "")}
    regime_params = {str(rid): a.get("strategy_params") or {}
                     for rid, a in assignments.items() if a.get("strategy_params")}
    dyn_doc = {"id": did,
               "name": body.get("name") or f"Regime-Lab: {doc.get('name')}",
               "strategy_id": sid,
               "symbols": [symbol] if scope == "per_coin" else doc.get("symbols") or [],
               "timeframe": doc.get("timeframe"),
               "model": model,
               "configs": {str(a["regime_id"]): a.get("trade_params") or {}
                           for a in assignments.values()},
               **({"regime_strategies": regime_strategies}
                  if len(regime_strategies) == len(assignments) else {}),
               "regime_params": regime_params,
               "fallback_config": {},
               "rule_variants": {},
               "sub_strategies": {str(a["regime_id"]):
                                  {"rules": a.get("rules") or [],
                                   "definition": a.get("definition")}
                                  for a in assignments.values() if a.get("definition")},
               "settings": {"confidence_min": (doc.get("settings") or {}).get("confidence_min"),
                            "min_hold_days": (doc.get("settings") or {}).get("min_hold_days"),
                            "auto_check_enabled": False, "auto_apply_enabled": False,
                            "check_interval_minutes": 60, "check_days": 30,
                            "source": "regime_lab", "analysis_id": aid},
               "verdict": wf.get("verdict") or {},
               "created_at": datetime.now(timezone.utc).isoformat(),
               "last_state": {}}
    # AP04/R09: Release-Status – Walkforward-Verdict validiert, sonst Entwurf
    dyn_doc["release"] = strategy_release.initial_release(dyn_doc, wf.get("verdict"))
    await state.db.dynamic_strategies.replace_one({"id": did}, dyn_doc, upsert=True)
    return {"status": "success", "id": did, "strategy_id": sid,
            "regimes": sorted(assignments.keys())}


@router.post("/api/regime-lab/{aid}/build-nnfx")
async def build_nnfx(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """NNFX-Framework anwenden: die erkannten Regime werden auf die drei
    NNFX-Regime (Trend / Seitwärts / Volatilität) gemappt und jedem Regime die
    passende NNFX-Strategie zugewiesen. Ergebnis ist eine dynamische Strategie,
    die im Live-/Paper-Betrieb automatisch die richtige Strategie aktiviert.

    Body: {scope, symbol?, name?, configs?: {regime_id: trade_params},
           strategy_overrides?: {trend|range|breakout: strategy_id}}
    """
    from services import regime_engine as eng
    from strategies.nnfx_strategies import NNFX_STRATEGY_BY_REGIME
    doc = await _get_doc(aid)
    scope = body.get("scope") or "combined"
    symbol = body.get("symbol")
    model = lab.model_for(doc, scope, symbol)
    if not model:
        raise HTTPException(status_code=400, detail="Kein Regime-Modell für diesen Bereich")
    if not model.get("engine"):
        raise HTTPException(status_code=400,
                            detail="NNFX benötigt eine Analyse mit der Regime-Engine v2")
    overrides = {k: v for k, v in (body.get("strategy_overrides") or {}).items()
                 if strategy_registry.get(v or "")}
    mapping, used = {}, {}
    kept = doc.get("kept") or {}
    key = lab.scope_key(scope, symbol)
    for r in model.get("regimes") or []:
        if kept.get(f"{key}:{r['id']}") is False:
            continue
        nnfx = r.get("nnfx") or eng.nnfx_regime(
            int(r["id"]), eng.norm_mode((model.get("config") or {}).get("regime_mode", 9)))
        sid = overrides.get(nnfx) or NNFX_STRATEGY_BY_REGIME[nnfx]
        mapping[str(r["id"])] = sid
        used.setdefault(nnfx, sid)
    if not mapping:
        raise HTTPException(status_code=400, detail="Keine behaltenen Regime vorhanden")
    configs = {str(k): (v or {}) for k, v in (body.get("configs") or {}).items()}
    # Idempotent: für dieselbe Analyse + denselben Bereich wird die bestehende
    # NNFX-Strategie aktualisiert statt jedes Mal eine neue anzulegen.
    existing = await state.db.dynamic_strategies.find_one(
        {"framework": "nnfx", "settings.analysis_id": aid, "settings.scope_key": key})
    did = (existing or {}).get("id") or f"dyn_{uuid.uuid4().hex[:8]}"
    prev_set = (existing or {}).get("settings") or {}
    dyn_doc = {"id": did,
               "name": body.get("name") or f"NNFX: {doc.get('name')}",
               "framework": "nnfx",
               "strategy_id": mapping[sorted(mapping.keys())[0]],
               "regime_strategies": mapping,
               "regime_params": {rid: ((doc.get("assignments") or {})
                                       .get(f"{key}:{rid}", {}).get("strategy_params") or {})
                                 for rid in mapping
                                 if (doc.get("assignments") or {})
                                 .get(f"{key}:{rid}", {}).get("strategy_params")},
               "symbols": [symbol] if scope == "per_coin" else doc.get("symbols") or [],
               "timeframe": doc.get("timeframe"),
               "model": model,
               "configs": configs,
               "fallback_config": {}, "rule_variants": {}, "sub_strategies": {},
               "settings": {"confidence_min": (doc.get("settings") or {}).get("confidence_min"),
                            "min_hold_days": (doc.get("settings") or {}).get("min_hold_days"),
                            "auto_check_enabled": bool(prev_set.get("auto_check_enabled")),
                            "auto_apply_enabled": bool(prev_set.get("auto_apply_enabled")),
                            "require_confirmation": bool(
                                prev_set.get("require_confirmation",
                                             body.get("require_confirmation", True))),
                            "check_interval_minutes": prev_set.get("check_interval_minutes") or 60,
                            "check_days": prev_set.get("check_days") or 30,
                            "source": "regime_lab_nnfx", "analysis_id": aid,
                            "scope_key": key},
               "verdict": {}, "created_at": ((existing or {}).get("created_at")
                                             or datetime.now(timezone.utc).isoformat()),
               "last_state": (existing or {}).get("last_state") or {}}
    # AP04/R09: erneuter Build derselben Definition behält den Release;
    # geänderte Definition erzeugt eine neue Revision (alte Freigabe = stale).
    dyn_doc["release"] = strategy_release.revised_release(
        (existing or {}).get("release"), dyn_doc)
    await state.db.dynamic_strategies.replace_one({"id": did}, dyn_doc, upsert=True)
    # Zusätzlich als Zuordnungen in der Analyse speichern -> der bestehende
    # Walk-Forward/Build-Weg kann die NNFX-Kombination direkt testen.
    if body.get("write_assignments", True):
        assignments = dict(doc.get("assignments") or {})
        now_iso = datetime.now(timezone.utc).isoformat()
        for rid_str, sid in mapping.items():
            reg = next((r for r in model.get("regimes") or []
                        if str(r["id"]) == rid_str), {})
            strat = strategy_registry.get(sid)
            prev_a = assignments.get(f"{key}:{rid_str}") or {}
            keep_params = (prev_a.get("strategy_params") or {}) \
                if prev_a.get("strategy_id") == sid else {}
            assignments[f"{key}:{rid_str}"] = {
                "regime_id": int(rid_str), "regime_label": reg.get("label"),
                "mode": "params", "strategy_id": sid,
                "strategy_name": getattr(strat, "STRATEGY_NAME", sid),
                "definition": None, "rules": [],
                "trade_params": configs.get(rid_str) or prev_a.get("trade_params") or {},
                "strategy_params": keep_params,
                "metrics": None, "validation": None,
                "source_job_id": None, "nnfx": reg.get("nnfx"),
                "assigned_at": now_iso}
        await state.db.regime_analyses.update_one({"id": aid},
                                                  {"$set": {"assignments": assignments}})
    return {"status": "success", "id": did, "regime_strategies": mapping,
            "nnfx_strategies": used}


@router.post("/api/regime-lab/{aid}/walkforward")
async def start_walkforward(aid: str, body: Dict, _: bool = Depends(require_admin)):
    """Finaler Walk-Forward: die zusammengestellte dynamische Strategie auf dem
    unangetasteten Holdout testen (kein Lookahead – identisch zum Live-Verhalten)."""
    doc = await _get_doc(aid)
    _guard_no_running()
    body["analysis_id"] = aid
    execution = (body.get("execution") or "cloud").lower()
    params = {k: body.get(k) for k in
              ("analysis_id", "scope", "symbol", "strategy_id")}
    params["execution"] = execution
    if execution == "local":
        _check_local_available()
        job_id = lab.create_job("walkforward", params)
        _enqueue_local("walkforward", job_id, {**body, "analysis_doc": _slim_doc(doc)})
        return {"status": "started", "job_id": job_id, "execution": "local"}
    job_id = lab.create_job("walkforward", params)
    queued = ram_queue.submit(lab.JOBS, job_id, lambda: regime_opt.run_walkforward(
        job_id, body, strategy_registry, scanner.settings, DEFAULT_COIN_CFG, state.db),
        kind="regime_walkforward")
    return {"status": "started", "job_id": job_id, "ram_queued": queued}
