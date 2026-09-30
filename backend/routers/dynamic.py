"""Dynamische Strategien: Verwaltung, Live-Regime-Erkennung & Konfig-Umschaltung.

- POST /api/dynamic/save            gespeicherte dynamische Strategie anlegen
- GET  /api/dynamic/list            alle dynamischen Strategien
- POST /api/dynamic/{id}/refresh    aktuelles Regime je Coin neu bestimmen
                                    (Wechsel werden protokolliert)
- POST /api/dynamic/{id}/apply      aktive Regime-Konfiguration als Coin-Override
                                    für Live/Paper übernehmen
- POST /api/dynamic/{id}/settings   Auto-Prüfung/Auto-Übernahme konfigurieren
- GET  /api/dynamic/{id}/log        Wechsel-Protokoll
- GET  /api/learning/summary        Lern-Gedächtnis (Robustheit je Marktphase)
- DELETE /api/dynamic/{id}
"""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict

from fastapi import APIRouter, Depends, HTTPException

from core import state
from core.auth import require_admin
from core.utils import _clean
from services import dynamic_live, dynamic_runtime, learning, strategy_release
from strategies.registry import registry as strategy_registry

logger = logging.getLogger(__name__)

router = APIRouter(tags=["dynamic"])


@router.get("/api/dynamic/current-regime")
async def current_market_regime(symbol: str = "BTCUSDT", timeframe: str = "5m",
                                days: int = 90, max_regimes: int = 5,
                                lookback_days: float = 3.0,
                                confidence_min: float = 70.0,
                                min_hold_days: float = 2.0,
                                engine: str = None):
    """Aktuelle Marktphase eines Coins – frisch berechnet, ohne dass eine
    dynamische Strategie gespeichert sein muss. Beantwortet die Frage
    'In welcher Marktphase sind wir gerade?' direkt in der Oberfläche."""
    try:
        return await dynamic_live.detect_current(
            symbol.upper(), timeframe, int(min(max(days, 14), 365)),
            int(min(max(max_regimes, 2), 10)), float(lookback_days),
            float(confidence_min) / 100.0, float(min_hold_days), engine=engine)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/dynamic/save")
async def dynamic_save(body: Dict, _: bool = Depends(require_admin)):
    """Ergebnis eines Dynamik-Laufs als dynamische Strategie speichern."""
    for k in ("strategy_id", "model", "configs"):
        if not body.get(k):
            raise HTTPException(status_code=400, detail=f"{k} erforderlich")
    if not strategy_registry.get(body["strategy_id"]):
        raise HTTPException(status_code=400, detail="Strategie nicht gefunden")
    did = f"dyn_{uuid.uuid4().hex[:8]}"
    doc = {"id": did,
           "name": body.get("name") or f"Dynamisch: {body['strategy_id']}",
           "strategy_id": body["strategy_id"],
           "symbols": body.get("symbols") or [],
           "timeframe": body.get("timeframe") or "1m",
           "model": body["model"],
           "configs": body["configs"],
           "fallback_config": body.get("fallback_config") or {},
           "rule_variants": body.get("rule_variants") or {},
           "sub_strategies": body.get("sub_strategies") or {},
           "settings": {**(body.get("settings") or {}),
                        "auto_check_enabled": False, "auto_apply_enabled": False,
                        "check_interval_minutes": 60, "check_days": 30},
           "verdict": body.get("verdict") or {},
           "created_at": datetime.now(timezone.utc).isoformat(),
           "last_state": {}}
    # AP04/R09: Release-Status (draft/validated) + Definitions-Fingerprint
    doc["release"] = strategy_release.initial_release(doc, doc.get("verdict"))
    await state.db.dynamic_strategies.replace_one({"id": did}, doc, upsert=True)
    await dynamic_runtime.reload(did)
    return {"status": "success", "id": did}


@router.get("/api/dynamic/list")
async def dynamic_list():
    rows = await state.db.dynamic_strategies.find(
        {"archived": {"$ne": True}}).sort("created_at", -1).to_list(100)
    aids = {((r.get("settings") or {}).get("analysis_id")) for r in rows}
    aids.discard(None)
    existing = set()
    if aids:
        existing = {a["id"] for a in await state.db.regime_analyses.find(
            {"id": {"$in": list(aids)}}, {"_id": 0, "id": 1}).to_list(len(aids))}
    out = []
    for r in rows:
        r = _clean(r)
        model = r.get("model") or {}
        aid = (r.get("settings") or {}).get("analysis_id")
        out.append({**r, "release_status": strategy_release.effective_status(r),
                    **dynamic_live.orphan_info(r, aid in existing),
                    "model": {"regimes": model.get("regimes") or [],
                              "silhouette": model.get("silhouette"),
                              "lookback_days": model.get("lookback_days")}})
    return {"strategies": out}


@router.delete("/api/dynamic/{did}")
async def dynamic_delete(did: str, _: bool = Depends(require_admin)):
    """AP04/R13: Löschen = Archivieren mit scoped Unapply. Eigene Coin-Overrides
    und Übergangssperren werden entfernt; Wechsel-Protokoll, offene Trades und
    schützende Brokerorders bleiben unangetastet."""
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    try:
        unapplied = await dynamic_live.unapply_dynamic(doc)
    except Exception as e:  # noqa: BLE001 – Archivieren nicht am Unapply scheitern lassen
        logger.warning(f"Unapply beim Archivieren von {did} fehlgeschlagen: {e}")
        unapplied = {"error": str(e)[:200]}
    await state.db.dynamic_strategies.update_one(
        {"id": did},
        {"$set": {"archived": True,
                  "archived_at": datetime.now(timezone.utc).isoformat()},
         "$unset": {"pending_switch": ""}})
    dynamic_runtime.unregister(did)
    from core.state import scanner
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != did]
    await scanner.save_settings({"enabled_strategies": enabled})
    return {"status": "deleted", "archived": True, "unapplied": unapplied}


async def _get_doc(did: str) -> Dict:
    doc = await state.db.dynamic_strategies.find_one({"id": did})
    if not doc:
        raise HTTPException(status_code=404, detail="Nicht gefunden")
    return doc


@router.post("/api/dynamic/{did}/refresh")
async def dynamic_refresh(did: str, body: Dict = None, _: bool = Depends(require_admin)):
    doc = await _get_doc(did)
    days = int(min(max(int((body or {}).get("days") or 30), 7), 90))
    try:
        res = await dynamic_live.check_one(doc, days, auto_apply=False)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": did, "switches": res["switches"], **res["state"]}


@router.post("/api/dynamic/{did}/apply")
async def dynamic_apply(did: str, _: bool = Depends(require_admin)):
    """Aktive Regime-Konfiguration je Coin als Live/Paper-Override übernehmen.

    AP04/R09: unvalidierte Entwürfe und veraltete Validierungen (stale) werden
    nicht live aktiviert – erst Walkforward bestehen oder ausdrücklich
    freigeben (POST /api/dynamic/{id}/approve)."""
    doc = await _get_doc(did)
    block = strategy_release.activation_block_reason(doc)
    if block:
        raise HTTPException(status_code=409, detail=block)
    version = strategy_release.state_version(doc.get("last_state") or {})
    try:
        applied = await dynamic_live.apply_active(doc)
    except RuntimeError as e:
        await dynamic_live._record_application(did, "failed", version, str(e)[:300])
        raise HTTPException(status_code=400, detail=str(e))
    await dynamic_live._record_application(did, "applied", version)
    return {"status": "success", "strategy_id": doc["strategy_id"], "applied": applied}


@router.post("/api/dynamic/{did}/settings")
async def dynamic_settings(did: str, body: Dict, _: bool = Depends(require_admin)):
    """Auto-Prüfung im Hintergrund konfigurieren (Intervall, Auto-Übernahme)."""
    doc = await _get_doc(did)
    s = dict(doc.get("settings") or {})
    if "auto_check_enabled" in body:
        s["auto_check_enabled"] = bool(body["auto_check_enabled"])
    if "auto_apply_enabled" in body:
        s["auto_apply_enabled"] = bool(body["auto_apply_enabled"])
    if "follow_release_enabled" in body:
        s["follow_release_enabled"] = bool(body["follow_release_enabled"])
    if "require_confirmation" in body:
        s["require_confirmation"] = bool(body["require_confirmation"])
    if body.get("check_interval_minutes") is not None:
        s["check_interval_minutes"] = int(min(max(int(body["check_interval_minutes"]), 5), 1440))
    if body.get("check_days") is not None:
        s["check_days"] = int(min(max(int(body["check_days"]), 7), 90))
    # Übergangsschutz beim Regime-Wechsel
    if "transition_protection_enabled" in body:
        s["transition_protection_enabled"] = bool(body["transition_protection_enabled"])
    if body.get("transition_mode") in ("block_new", "close_open"):
        s["transition_mode"] = body["transition_mode"]
    # Handel als eigene Strategie: Verhalten offener Trades beim Regimewechsel
    if body.get("on_switch") in ("close", "let_run"):
        s["on_switch"] = body["on_switch"]
    if body.get("runtime_interval_minutes") is not None:
        s["runtime_interval_minutes"] = int(min(max(int(body["runtime_interval_minutes"]), 5), 1440))
    if body.get("transition_lock_days") is not None:
        try:
            s["transition_lock_days"] = float(min(max(
                float(body["transition_lock_days"]), 0.0), 30.0))
        except (TypeError, ValueError):
            pass
    await state.db.dynamic_strategies.update_one({"id": did}, {"$set": {"settings": s}})
    await dynamic_runtime.reload(did)
    return {"status": "success", "settings": s}


@router.post("/api/dynamic/{did}/confirm")
async def dynamic_confirm(did: str, body: Dict = None, _: bool = Depends(require_admin)):
    """Offenen Regime-Wechsel bestätigen (Modus 'manuelle Bestätigung'):
    übernimmt die Konfiguration des aktuellen Regimes für Live/Paper.

    AP04/R13 (Compare-and-swap): Die Bestätigung bindet an die Command-ID und
    die beim Vorschlag beobachtete Zustandsversion. Ein abgelaufener Vorschlag
    oder ein inzwischen geänderter Regime-Zustand liefert 409 – dann zuerst
    'Regime aktualisieren' und den NEUEN Vorschlag bestätigen."""
    doc = await _get_doc(did)
    pending = doc.get("pending_switch")
    if not pending:
        raise HTTPException(status_code=400, detail="Kein offener Regime-Wechsel")
    want_cmd = (body or {}).get("command_id")
    if want_cmd and pending.get("command_id") and want_cmd != pending["command_id"]:
        raise HTTPException(status_code=409,
                            detail="Vorschlag wurde inzwischen ersetzt – bitte neu prüfen")
    exp = pending.get("expires_at")
    if exp:
        try:
            expired = datetime.fromisoformat(exp) < datetime.now(timezone.utc)
        except ValueError:
            expired = False
        if expired:
            await state.db.dynamic_strategies.update_one(
                {"id": did}, {"$unset": {"pending_switch": ""}})
            raise HTTPException(status_code=409,
                                detail="Vorschlag ist abgelaufen – 'Regime aktualisieren' "
                                       "und den neuen Vorschlag bestätigen")
    seen = pending.get("observed_version")
    if seen and strategy_release.state_version(doc.get("last_state") or {}) != seen:
        raise HTTPException(status_code=409,
                            detail="Regime-Zustand hat sich seit dem Vorschlag geändert – "
                                   "bitte neu prüfen und den neuen Vorschlag bestätigen")
    block = strategy_release.activation_block_reason(doc)
    if block:
        raise HTTPException(status_code=409, detail=block)
    # R13-Härtung: atomarer Claim in der DB (Compare-and-swap) – parallele
    # Bestätigungen desselben Vorschlags können nicht mehr doppelt durchlaufen
    # (Übergangsschutz-Closes und Apply liefen sonst zweimal).
    if pending.get("claimed_at") or not await dynamic_live.claim_pending_switch(did, pending):
        raise HTTPException(status_code=409,
                            detail="Bestätigung läuft bereits oder der Vorschlag "
                                   "wurde ersetzt – bitte Status neu laden")
    # AP01/R03: Übergangsschutz gehört zum FREIGEGEBENEN Apply (nicht zum
    # Refresh) – hier, direkt vor der Übernahme, gescoped ausführen.
    transition = None
    switched = pending.get("switched_symbols") or []
    if switched:
        try:
            transition = await dynamic_live.transition_protect(
                doc, switched, doc.get("last_state") or {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Übergangsschutz beim Confirm fehlgeschlagen: {e}")
            transition = {"status": "failed", "reason": str(e)[:200]}
    version = seen or strategy_release.state_version(doc.get("last_state") or {})
    try:
        applied = await dynamic_live.apply_active(doc)
    except RuntimeError as e:
        await dynamic_live.release_pending_claim(did)
        await dynamic_live._record_application(did, "failed", version, str(e)[:300])
        raise HTTPException(status_code=400, detail=str(e))
    await dynamic_live._record_application(did, "applied", version)
    await state.db.dynamic_strategies.update_one({"id": did},
                                                 {"$unset": {"pending_switch": ""}})
    return {"status": "success", "applied": applied, "transition": transition,
            "command_id": pending.get("command_id")}


@router.post("/api/dynamic/{did}/approve")
async def dynamic_approve(did: str, body: Dict = None, _: bool = Depends(require_admin)):
    """AP04/R09: Ausdrückliche menschliche Freigabe der AKTUELLEN Definition
    (draft/stale -> approved). Nachträgliche Änderungen an der Definition
    machen die Freigabe automatisch wieder 'stale'.

    R09-Härtung: Ohne Testnachweis für die aktuelle Definition wird die
    Freigabe abgelehnt (409). Bewusstes Übersteuern ist möglich mit
    'override_missing_evidence': true UND einer Begründung ('note') – das
    wird im Release ehrlich protokolliert (approved_without_evidence)."""
    doc = await _get_doc(did)
    body = body or {}
    note = str(body.get("note") or "")
    missing = strategy_release.approval_evidence_missing(doc)
    if missing:
        if not body.get("override_missing_evidence"):
            raise HTTPException(
                status_code=409,
                detail=f"Freigabe verweigert – Testnachweis fehlt: {missing}. "
                       "Zum bewussten Freigeben ohne Nachweis "
                       "'override_missing_evidence': true und eine Begründung "
                       "('note') mitsenden.")
        if not note.strip():
            raise HTTPException(
                status_code=400,
                detail="Begründung ('note') ist bei einer Freigabe ohne "
                       "Testnachweis erforderlich")
    rel = strategy_release.approve(doc, actor="admin", note=note,
                                   missing_evidence=missing)
    await state.db.dynamic_strategies.update_one({"id": did},
                                                 {"$set": {"release": rel}})
    await dynamic_runtime.reload(did)
    return {"status": "success", "release": rel, "release_status": "approved"}


@router.post("/api/dynamic/{did}/dismiss")
async def dynamic_dismiss(did: str, _: bool = Depends(require_admin)):
    """Offenen Regime-Wechsel verwerfen (nicht übernehmen)."""
    await state.db.dynamic_strategies.update_one({"id": did},
                                                 {"$unset": {"pending_switch": ""}})
    return {"status": "dismissed"}


@router.get("/api/dynamic/{did}/evidence")
async def dynamic_evidence(did: str):
    """AP12: Beweispaket (read-only) – Datensatz/Release/Validierung/
    Anwendung/Runtime-Health mit Klartext-Blockern für die gestufte Abnahme."""
    r = await state.db.dynamic_strategies.find_one({"id": did})
    if not r:
        raise HTTPException(status_code=404, detail="Dynamische Strategie nicht gefunden")
    analysis = None
    aid = ((r.get("settings") or {}).get("analysis_id"))
    if aid:
        analysis = await state.db.regime_analyses.find_one(
            {"id": aid}, {"chart": 0, "combined": 0, "per_coin": 0, "chart_emas": 0})
    safety = None
    try:
        from services import safety_status
        safety = await safety_status.status(state.db)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"evidence safety lookup failed: {e}")
    return {"evidence": strategy_release.evidence_bundle(
        _clean(r), _clean(analysis) if analysis else None, safety)}


@router.get("/api/dynamic/{did}/trade-plan")
async def dynamic_trade_plan(did: str):
    """Handel als eigene Strategie: Regime -> Strategie, Live-Regime je Symbol,
    Verhalten beim Wechsel und Freigabe-Status (für Blitz-Reiter & Verlauf)."""
    doc = await _get_doc(did)
    doc.pop("_id", None)
    strat = strategy_registry.get(did)
    regimes = strat.regimes() if strat is not None and getattr(strat, "IS_DYNAMIC", False) else []
    s = doc.get("settings") or {}
    return {"id": did, "name": doc.get("name"), "timeframe": doc.get("timeframe"),
            "regimes": regimes, "symbols": doc.get("symbols") or [],
            "current": dynamic_runtime.STATE.get(did) or {},
            "runtime_checked_at": (doc.get("runtime_state") or {}).get("checked_at"),
            "on_switch": s.get("on_switch") or dynamic_runtime.ON_SWITCH_DEFAULT,
            "traded": did in dynamic_runtime.traded_ids(),
            "release_status": strategy_release.effective_status(doc),
            "block_reason": strategy_release.activation_block_reason(doc),
            "verdict": doc.get("verdict") or {}}


@router.get("/api/dynamic/{did}/regime-performance")
async def dynamic_regime_performance(did: str, mode: str = "all"):
    """Gehandelte dynamische Strategie: Gewinn/Trefferquote je Regime (Live/Paper)
    neben den Walk-Forward-Zahlen je Regime."""
    from services import dynamic_performance
    doc = await _get_doc(did)
    doc.pop("_id", None)
    strat = strategy_registry.get(did)
    regimes = strat.regimes() if strat is not None and getattr(strat, "IS_DYNAMIC", False) else \
        [{"id": r["id"], "label": r.get("label")} for r in (doc.get("model") or {}).get("regimes") or []]
    return await dynamic_performance.regime_performance(state.db, doc, regimes, mode)


@router.post("/api/dynamic/{did}/runtime-refresh")
async def dynamic_runtime_refresh(did: str, _: bool = Depends(require_admin)):
    """Live-Regime der handelbaren dynamischen Strategie sofort neu bestimmen."""
    doc = await _get_doc(did)
    doc.pop("_id", None)
    try:
        res = await dynamic_runtime.refresh(doc)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"id": did, "switches": res["switches"], "closed": res["closed"],
            "current": dynamic_runtime.STATE.get(did) or {}}


@router.get("/api/dynamic/{did}/log")
async def dynamic_log(did: str, limit: int = 100):
    """Wechsel-Protokoll: alle Regime-Wechsel mit Datum, Sicherheit & Begründung."""
    rows = await state.db.dynamic_switch_log.find({"dynamic_id": did}) \
        .sort("at", -1).to_list(int(min(max(limit, 1), 500)))
    return {"log": [_clean(r) for r in rows]}


@router.get("/api/learning/summary")
async def learning_summary():
    """Lern-Gedächtnis: welche Indikatoren/Strategien liefen je Marktphase am besten."""
    return await learning.summary(state.db)


# ---------------- Dynamik-Werkbank (Strategie-Optimizer) ----------------
def _wb_deps() -> Dict:
    from core.state import scanner
    from routers import regime_lab as rl
    from services.bitunix_trade import DEFAULT_COIN_CFG

    async def build(aid, body):
        return await rl.build_dynamic(aid, body, True)
    return {"db": state.db, "registry": strategy_registry, "settings": scanner.settings,
            "default_cfg": DEFAULT_COIN_CFG, "enqueue_local": rl._enqueue_local,
            "slim": rl._slim_doc, "build": build}


def _scored_current(analysis: Dict, scope: str, symbol, objective: str, min_trades: int,
                    subset=None) -> Dict:
    """Bestehende Zuordnungen mit vergleichbarem Score (gleiches Ziel) versehen,
    damit die Suche nur ECHTE Verbesserungen übernimmt."""
    from services import dynamic_strategy as dyn
    from services import dynamic_workbench as wb
    out = {}
    for rid, a in wb.current_candidates(analysis, scope, symbol, subset).items():
        a = dict(a)
        if a.get("score") is None and a.get("metrics"):
            a["score"] = round(dyn._guarded_score(a["metrics"], objective, min_trades), 3)
        if a.get("validation_passed") is None and a.get("validation"):
            a["validation_passed"] = dyn.validation_passed(a["validation"],
                                                           max(int(min_trades * 0.4), 3))
        out[str(rid)] = a
    return out


# Lokaler Worker: Asset-Teilmenge (symbols/reuse_key im regime_opt-Body) ab dieser Version
WB_SUBSET_WORKER = (1, 18, 0)


@router.post("/api/dynamic-workbench/start")
async def workbench_start(body: Dict, _: bool = Depends(require_admin)):
    """kind=refine: {dynamic_id, regime_ids[], mode} · kind=create: {analysis_id,
    scope, symbol?, mapping{rid: strategy_id}} · kind=discover: {analysis_id,
    scope, symbol?, regime_ids[], mode}. Gemeinsam: endless, rounds, iterations,
    objective, min_trades, execution (cloud|local), name, walkforward."""
    import asyncio as _asyncio
    from services import dynamic_workbench as wb
    from services import regime_lab as lab
    kind = body.get("kind")
    if kind not in ("refine", "create", "discover"):
        raise HTTPException(status_code=400, detail="kind muss refine|create|discover sein")
    if wb.running_job() or lab.running_job():
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Werkbank-/Regime-Lab-Job")
    p = {k: body.get(k) for k in ("endless", "rounds", "iterations", "objective", "min_trades",
                                  "execution", "name", "walkforward", "direction_bias",
                                  "optimize_strategy_params", "max_rules", "base_strategy_id",
                                  # Einstellungen wie im klassischen Optimizer (durchgereicht
                                  # an regime_opt.run_regime_optimizer)
                                  "timeframe", "days", "max_capital", "leverage", "fee_percent",
                                  "sessions", "optimize", "indicators", "regime_walk_forward",
                                  "regime_train_pct", "deep_test", "result_backtest",
                                  "label_basis", "skip_losing")}
    mode = body.get("mode") or ("discovery" if kind == "discover" else "params")
    if mode not in ("params", "discovery", "combo"):
        raise HTTPException(status_code=400, detail="mode muss params|discovery|combo sein")
    if kind == "refine":
        doc = await _get_doc(body.get("dynamic_id") or "")
        aid = (doc.get("settings") or {}).get("analysis_id")
        if not aid:
            raise HTTPException(status_code=400,
                                detail="Diese dynamische Strategie stammt nicht aus dem Regime-Lab "
                                       "(keine Regime-Analyse verknüpft) – bitte im Regime-Lab neu erstellen")
        scope, symbol = wb.scope_of(doc)
        rids = [int(r) for r in (body.get("regime_ids") or
                                 [r["id"] for r in (doc.get("model") or {}).get("regimes") or []])]
        p.update({"analysis_id": aid, "scope": scope, "symbol": symbol, "dynamic_id": doc["id"],
                  "targets": {str(k): v for k, v in wb.targets_for_refine(doc, rids, mode).items()},
                  "name": body.get("name") or f"{doc.get('name')} (optimiert)"})
    else:
        aid = body.get("analysis_id")
        p.update({"analysis_id": aid, "scope": body.get("scope") or "combined",
                  "symbol": body.get("symbol")})
        if kind == "create":
            mapping = {str(k): v for k, v in (body.get("mapping") or {}).items()}
            bad = [v for v in mapping.values() if v and (not strategy_registry.get(v)
                                                         or strategy_registry.is_dynamic(v))]
            if bad:
                raise HTTPException(status_code=400, detail=f"Unbekannte Strategie: {bad[0]}")
            p["targets"] = {k: {"strategy_id": v or None} for k, v in mapping.items()}
            if not any(mapping.values()):
                raise HTTPException(status_code=400, detail="Mindestens einem Regime eine Strategie zuordnen")
        else:
            p["targets"] = {str(int(r)): {"mode": mode} for r in (body.get("regime_ids") or [])}
    analysis = await state.db.regime_analyses.find_one({"id": p["analysis_id"]})
    if not analysis:
        raise HTTPException(status_code=404, detail="Regime-Analyse nicht gefunden")
    # Asset-Auswahl (nur gemeinsame Erkennung): None = alle Assets der Analyse
    p["symbols"] = None
    if p["scope"] != "per_coin":
        raw = body.get("symbols")
        if raw is None and kind == "refine":
            raw = wb.subset_of(doc)
        if raw is not None:
            if not [s for s in raw if s in (analysis.get("symbols") or [])]:
                raise HTTPException(status_code=400, detail="Mindestens 1 Asset der Analyse auswählen")
            p["symbols"] = lab.norm_subset(analysis.get("symbols"), raw)
    if (p.get("execution") or "cloud") == "local":
        from routers.regime_lab import _check_local_available
        _check_local_available()
        from services import local_exec
        if p["symbols"] and not local_exec.worker_supports_version(WB_SUBSET_WORKER):
            raise HTTPException(status_code=409,
                                detail="Der lokale Worker ist veraltet und kennt die Asset-Auswahl der "
                                       f"Dynamik-Werkbank noch nicht (Version {'.'.join(map(str, WB_SUBSET_WORKER))} "
                                       "nötig). Bitte das Worker-Paket neu herunterladen – oder Cloud wählen.")
    p["current"] = _scored_current(analysis, p["scope"], p.get("symbol"),
                                   p.get("objective") or "combo", int(p.get("min_trades") or 10),
                                   p["symbols"])
    if kind == "create":
        p["current"] = {}
    jid = wb.create_job(kind, p)
    _asyncio.create_task(wb.run(jid, p, _wb_deps()))
    return {"status": "started", "job_id": jid}


def _wb_public(j: Dict) -> Dict:
    return {k: v for k, v in j.items() if k not in ("params",)} | {
        "params": {k: v for k, v in (j.get("params") or {}).items() if k != "current"}}


@router.get("/api/dynamic-workbench/status/{jid}")
async def workbench_status(jid: str):
    from services import dynamic_workbench as wb
    j = wb.JOBS.get(jid)
    if not j:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _wb_public(j)


@router.get("/api/dynamic-workbench/active")
async def workbench_active():
    from services import dynamic_workbench as wb
    j = wb.running_job() or (list(wb.JOBS.values())[-1] if wb.JOBS else None)
    return {"job": _wb_public(j) if j else None}


@router.post("/api/dynamic-workbench/{action}/{jid}")
async def workbench_control(action: str, jid: str, _: bool = Depends(require_admin)):
    """stop = Suche beenden & Bestes übernehmen (Walk-Forward + Build laufen noch),
    cancel = hart abbrechen (nichts wird gespeichert)."""
    from services import dynamic_workbench as wb
    j = wb.JOBS.get(jid)
    if not j:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    if action not in ("stop", "cancel"):
        raise HTTPException(status_code=400, detail="action muss stop|cancel sein")
    j[action] = True
    return {"status": action + "_requested"}
