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
from services import dynamic_live, learning, strategy_release
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
    if body.get("transition_lock_days") is not None:
        try:
            s["transition_lock_days"] = float(min(max(
                float(body["transition_lock_days"]), 0.0), 30.0))
        except (TypeError, ValueError):
            pass
    await state.db.dynamic_strategies.update_one({"id": did}, {"$set": {"settings": s}})
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
