"""Regime-Freigabe (PLAN_REGIME_BRUECKE, Baustein 1): Research -> Release, zweistufig.

Eine Lab-Analyse (`regime_analyses[aid]`) durchläuft je Assetklasse die Stufen
`none` -> `shadow` (beobachten, Daten sammeln) -> `active` (Prompt + optional Gate).
Jede Stufe ist NACHWEISGEBUNDEN (`validate_release` / `validate_activation`) und
im `release`-Dokument mit unveränderlicher `history` protokolliert.

Reine Funktionen oben (unit-testbar), dünne DB-Helfer unten. Die KI kann über den
bestehenden Proposal-Mechanismus (`ai_engine_governance`, Scope `regime_release`)
den Wechsel shadow->active vorschlagen/vollziehen – niemals ohne grünes Gate.
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from services import setup_asset_class

logger = logging.getLogger(__name__)

STAGES = ("none", "shadow", "active")
MIN_SEGMENTS_PER_REGIME = 5
ACTIVATION_MIN_TRADES = 30          # Standard/Obergrenze (unbewertete oder schwache Erkennung)
# Dynamische Shadow-Stichprobe je Erkennungs-Note: je besser die Erkennung
# nachgewiesen ist, desto weniger Shadow-Trades je Struktur-Regime braucht „Wirksam“.
ACTIVATION_MIN_TRADES_BY_GRADE = {"sehr gut": 10, "gut": 15, "mittel": 25, "schwach": 30}
# Manuelle Freigabe (Override): für „Wirksam“ mindestens diese Note (Risiko-Schutz)
OVERRIDE_MIN_GRADE_ACTIVE = "mittel"
ACTIVATION_MIN_DELTA_R = 0.25
AUTO_GRACE_HOURS = 24
PROPOSAL_COOLDOWN_DAYS = 7
PROPOSAL_SCOPE = "regime_release"
CONFIG_KEY_AUTONOMY = "structural_regime_autonomy"      # off | suggest | auto (Default suggest)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def evidence_hash(evidence: Dict) -> str:
    payload = {k: v for k, v in (evidence or {}).items() if k != "evidence_hash"}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:12]


def kept_regime_ids(doc: Dict, scope: str, symbol: Optional[str] = None) -> List[int]:
    from services import regime_lab as lab
    prefix = f"{lab.scope_key(scope, symbol)}:"
    return sorted(int(k[len(prefix):]) for k, v in (doc.get("kept") or {}).items()
                  if v and k.startswith(prefix) and k[len(prefix):].lstrip("-").isdigit())


def suggest_kept(doc: Dict, scope: str, symbol: Optional[str] = None) -> Dict[str, bool]:
    """Rein (18.09.): Behalten-Vorschlag – jedes Regime mit ≥ MIN_SEGMENTS_PER_REGIME
    Abschnitten wird 'behalten', dünne Regime 'verworfen'. Bereits gesetzte Häkchen
    bleiben unverändert (nur fehlende Keys werden ergänzt)."""
    from services import regime_lab as lab
    prefix = f"{lab.scope_key(scope, symbol)}:"
    kept = dict(doc.get("kept") or {})
    for rid, n in segments_per_regime(doc, scope, symbol).items():
        key = f"{prefix}{rid}"
        if key not in kept:
            kept[key] = n >= MIN_SEGMENTS_PER_REGIME
    return kept


def segments_per_regime(doc: Dict, scope: str, symbol: Optional[str] = None) -> Dict[int, int]:
    """Anzahl unabhängiger Abschnitte je Regime (über alle Symbole des Scopes)."""
    out: Dict[int, int] = {}
    if scope == "per_coin":
        entries = [((doc.get("per_coin") or {}).get(symbol) or {})]
    else:
        entries = list(((doc.get("combined") or {}).get("per_symbol") or {}).values())
    for e in entries:
        for s in (e or {}).get("segments") or []:
            rid = int(s.get("regime"))
            out[rid] = out.get(rid, 0) + 1
    return out


def ablation_delta_pct(run: Optional[Dict]) -> Optional[float]:
    """Holdout-Trefferquote volle Konfiguration minus beste einfache Alternative
    (`alt_*`). >= 0: Regime-Umschaltung verliert im Holdout nicht."""
    rows = ((run or {}).get("result") or run or {}).get("rows") or []
    full = next((r for r in rows if r.get("variant_key") == "full"), None)
    alts = [r for r in rows if str(r.get("variant_key") or "").startswith("alt_")]
    if not full or not alts:
        return None
    try:
        best_alt = max(float(r.get("holdout_direction_pct") or 0) for r in alts)
        return round(float(full.get("holdout_direction_pct") or 0) - best_alt, 2)
    except (TypeError, ValueError):
        return None


def _matches(run_like: Dict, doc: Dict) -> bool:
    r = (run_like or {}).get("result") or run_like or {}
    syms = set(r.get("symbols") or run_like.get("symbols") or [])
    tf = r.get("timeframe") or run_like.get("timeframe")
    return bool(syms) and set(doc.get("symbols") or []) <= syms and tf == doc.get("timeframe")


def validate_release(doc: Dict, stage: str, body: Dict, jobs: Dict) -> Tuple[bool, List[str], Dict]:
    """Nachweis-Gate für `shadow`/`active` (rein). Liefert (ok, fehlende Nachweise
    im Klartext, evidence-Dokument mit Hash). `jobs`: {"calibrations": [...],
    "ablations": [...]} – Läufe zur Analyse (Symbole/Timeframe passend)."""
    from services import regime as rg
    from services import regime_lab as lab
    reasons: List[str] = []
    if stage not in ("shadow", "active"):
        return False, [f"Stufe '{stage}' ist keine Freigabestufe"], {}
    # Analyse-Scope `both` (kombiniert + je Coin) -> Freigabe läuft über das kombinierte Modell
    scope = "per_coin" if str(body.get("scope") or doc.get("scope") or "") == "per_coin" else "combined"
    symbol = body.get("symbol")
    if scope == "per_coin" and not symbol:
        reasons.append("scope=per_coin braucht ein Symbol")
    model = lab.model_for(doc, scope, symbol)
    if not model:
        reasons.append("kein Modell für diesen Scope gespeichert")
    elif not rg.is_v2(model):
        reasons.append("Modell ist nicht v2 (Richtungs-IDs fehlen)")
    kept = kept_regime_ids(doc, scope, symbol) if model else []
    if model and not kept:
        reasons.append("kein Regime als 'behalten' markiert")
    segs = segments_per_regime(doc, scope, symbol) if model else {}
    thin = [(rid, segs.get(rid, 0)) for rid in kept if segs.get(rid, 0) < MIN_SEGMENTS_PER_REGIME]
    for rid, n in thin:
        reasons.append(f"Regime {rid} nur {n} Abschnitte (mind. {MIN_SEGMENTS_PER_REGIME})")
    calibs = [c for c in (jobs.get("calibrations") or []) if _matches(c, doc)]
    if not calibs:
        reasons.append("Kalibrierung fehlt (gleiche Symbole/Timeframe)")
    abls = [a for a in (jobs.get("ablations") or []) if _matches(a, doc)]
    delta = None
    if not abls:
        reasons.append("Ablation fehlt (gleiche Symbole/Timeframe)")
    else:
        deltas = [d for d in (ablation_delta_pct(a) for a in abls) if d is not None]
        delta = max(deltas) if deltas else None
        if delta is None:
            reasons.append("Ablation ohne auswertbare Holdout-Zahlen")
        elif delta < 0:
            reasons.append(f"Ablation: Regime-Umschaltung verliert im Holdout ({delta:+.1f} pp)")
    symbols = [str(s).upper() for s in (body.get("symbols") or doc.get("symbols") or [])]
    unknown = [s for s in symbols if s not in (doc.get("symbols") or [])]
    if unknown:
        reasons.append(f"Symbole nicht Teil der Analyse: {', '.join(unknown)}")
    classes = sorted({setup_asset_class.asset_class_of(s) for s in symbols}) if symbols else []
    wanted = [str(c) for c in (body.get("asset_classes") or classes)]
    if classes and set(wanted) != set(classes):
        reasons.append(f"Assetklasse inkonsistent: Symbole ergeben {classes}, angefragt {wanted}")
    from services import market_context as mc
    evidence = {"calibration_job_id": (calibs[0].get("id") if calibs else None),
                "ablation_job_id": (abls[0].get("id") if abls else None),
                "ablation_delta_pct": delta, "kept_regimes": len(kept),
                "min_segments_per_regime": min([segs.get(r, 0) for r in kept], default=0),
                "model_fingerprint": mc.model_fingerprint(model) if model else None,
                "scope": scope, "symbol": symbol, "symbols": symbols, "asset_classes": wanted}
    evidence["evidence_hash"] = evidence_hash(evidence)
    return not reasons, reasons, evidence


def activation_min_trades(grade: Optional[str]) -> int:
    """Mindest-Shadow-Trades je Struktur-Regime für `active` (rein)."""
    return ACTIVATION_MIN_TRADES_BY_GRADE.get(str(grade or ""), ACTIVATION_MIN_TRADES)


def quality_grade(doc: Optional[Dict], asset_classes: Optional[List[str]] = None) -> Optional[str]:
    """Erkennungs-Note der (freigegebenen) Analyse – schwächste der Klassen."""
    if not doc:
        return None
    from services import regime_quality
    classes = asset_classes or ((doc.get("release") or {}).get("asset_classes")) or None
    return regime_quality.grade_for_classes(doc, classes)


# Nicht übersteuerbar: ohne diese Grundlagen kann die Runtime kein Struktur-Regime liefern
HARD_REASON_PREFIXES = ("scope=per_coin braucht", "kein Modell", "Modell ist nicht v2",
                        "Symbole nicht Teil", "Assetklasse inkonsistent", "erst Stufe Shadow")


def split_override(reasons: List[str]) -> Tuple[List[str], List[str]]:
    """Gründe in (hart, übersteuerbar) trennen (rein)."""
    hard = [r for r in reasons if str(r).startswith(HARD_REASON_PREFIXES)]
    return hard, [r for r in reasons if r not in hard]


def override_blockers(stage: str, reasons: List[str], grade: Optional[str]) -> List[str]:
    """Was verhindert eine manuelle Freigabe (rein)? Shadow ist wirkungslos und
    daher immer übersteuerbar (nur harte Gründe blockieren). „Wirksam“ braucht
    zusätzlich mindestens OVERRIDE_MIN_GRADE_ACTIVE – bei schwacher/unbewerteter
    Erkennung wäre das zu viel Risiko."""
    from services.regime_quality import GRADE_ORDER
    hard, _ = split_override(reasons)
    if stage == "active" and GRADE_ORDER.get(str(grade or ""), -1) < GRADE_ORDER[OVERRIDE_MIN_GRADE_ACTIVE]:
        hard.append(f"Erkennungs-Qualität „{grade or 'unbewertet'}“ – manuelles Wirksam-Schalten erst ab "
                    f"„{OVERRIDE_MIN_GRADE_ACTIVE}“ (zu viel Risiko durch Fehl-Erkennung)")
    return hard


def validate_activation(rewards_by_structural: List[Dict],
                        min_trades: int = ACTIVATION_MIN_TRADES,
                        min_delta_r: float = ACTIVATION_MIN_DELTA_R) -> Tuple[bool, List[str], Dict]:
    """Stichproben-Gate für `active`: je Struktur-Regime >= min_trades im Shadow
    und Ø-Reward-Unterschied bestes/schlechtestes Regime >= min_delta_r."""
    rows = [r for r in (rewards_by_structural or []) if str(r.get("regime") or "") not in ("", "unbekannt")]
    reasons: List[str] = []
    total = sum(int(r.get("trades") or 0) for r in rows)
    if not rows:
        reasons.append(f"Shadow-Stichprobe fehlt (0/{min_trades} Trades mit Struktur-Regime)")
        return False, reasons, {"shadow_trades": 0, "min_trades": min_trades}
    thin = [r for r in rows if int(r.get("trades") or 0) < min_trades]
    for r in thin:
        reasons.append(f"Struktur-Regime '{r.get('regime')}' erst {r.get('trades')}/{min_trades} Trades")
    avgs = [float(r.get("avg_reward") or 0) for r in rows]
    delta = round(max(avgs) - min(avgs), 3) if len(avgs) >= 2 else 0.0
    if len(rows) < 2:
        reasons.append("nur ein Struktur-Regime beobachtet – Ebene trägt (noch) keine Information")
    elif delta < min_delta_r:
        reasons.append(f"Ø-Rewards unterscheiden sich nur um {delta:.2f} R (mind. {min_delta_r}) – bleibt Shadow")
    return not reasons, reasons, {"shadow_trades": total, "reward_delta_r": delta,
                                 "min_trades": min_trades,
                                 "regimes": [{"regime": r.get("regime"), "trades": r.get("trades"),
                                              "avg_reward": r.get("avg_reward")} for r in rows]}


def apply_stage(doc: Dict, stage: str, evidence: Dict, by: str, reason: str,
                proposal_id: Optional[str] = None, override: Optional[Dict] = None) -> Dict:
    """Neues `release`-Dokument (rein): Stufe setzen, History anhängen (nie kürzen).
    `override` = manuelle Freigabe trotz fehlender Nachweise (wird protokolliert)."""
    if stage not in STAGES:
        raise ValueError(f"ungültige Stufe {stage}")
    rel = dict(doc.get("release") or {})
    history = list(rel.get("history") or [])
    entry = {"stage": stage, "at": _now_iso(), "by": by, "reason": str(reason or "")[:200]}
    if proposal_id:
        entry["proposal_id"] = proposal_id
    if override:
        entry["override"] = True
        entry["override_missing"] = list(override.get("missing") or [])[:10]
        entry["quality_grade"] = override.get("grade")
    history.append(entry)
    ev = evidence or rel.get("evidence") or {}
    return {"stage": stage,
            "manual_override": bool(override) if stage != "none" else False,
            "scope": (ev or {}).get("scope") or rel.get("scope") or doc.get("scope") or "combined",
            "symbol": (ev or {}).get("symbol") or rel.get("symbol"),
            "symbols": (ev or {}).get("symbols") or rel.get("symbols") or doc.get("symbols") or [],
            "asset_classes": (ev or {}).get("asset_classes") or rel.get("asset_classes") or [],
            "model_fingerprint": (ev or {}).get("model_fingerprint") or rel.get("model_fingerprint"),
            "evidence": ev or {}, "history": history,
            "since": (entry["at"] if stage != "none" else None)}


def proposal_for(rec: Dict, gate_ok: bool, gate_reasons: List[str], autonomy: str) -> Optional[Dict]:
    """KI-Empfehlung (`regime_release_recommendation`) -> Proposal-Dokument (rein).
    Nur `activate` (conf >= 70) oder `revoke` erzeugen etwas; ohne grünes Gate
    -> `needs_data` (sichtbar, nicht anklickbar). autonomy=off -> None."""
    if autonomy == "off" or not isinstance(rec, dict):
        return None
    verdict = str(rec.get("verdict") or "").lower()
    conf = int(rec.get("confidence") or 0)
    if verdict not in ("activate", "revoke") or (verdict == "activate" and conf < 70):
        return None
    target = "active" if verdict == "activate" else "shadow"
    now = datetime.now(timezone.utc)
    prop = {"id": str(uuid.uuid4()), "ts": now.isoformat(), "scope": PROPOSAL_SCOPE,
            "symbol": str(rec.get("asset_class") or "crypto"), "source": "research_analyst",
            "changes": {"structural_regime_stage": target, "asset_class": rec.get("asset_class"),
                        "aid": rec.get("aid")},
            "reason": str(rec.get("reason") or "")[:300], "confidence": conf,
            "gate_reasons": list(gate_reasons or [])}
    if verdict == "activate" and not gate_ok:
        prop["status"] = "needs_data"
        return prop
    if autonomy == "auto":
        prop["status"] = "auto_applied"
        if verdict == "activate":
            prop["pending_auto_until"] = (now + timedelta(hours=AUTO_GRACE_HOURS)).isoformat()
            prop["applied_at"] = None
        else:
            prop["applied_at"] = now.isoformat()   # Sicherheitsrichtung sofort
        return prop
    prop["status"] = "pending"
    return prop


def grace_due(prop: Dict, now: Optional[datetime] = None) -> bool:
    until = prop.get("pending_auto_until")
    if not until or prop.get("applied_at"):
        return False
    try:
        dt = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
    except ValueError:
        return False
    return (now or datetime.now(timezone.utc)) >= dt


# --------------------------------------------------------------------------
# DB-Helfer (dünn)
# --------------------------------------------------------------------------
async def jobs_for(db, doc: Dict) -> Dict:
    calibs = await db.regime_calibrations.find({}, {"_id": 0, "report": 0}).sort("created_at", -1).to_list(50)
    runs = await db.regime_lab_runs.find({"result.kind": "ablation"}, {"_id": 0}).sort("created_at", -1).to_list(50)
    return {"calibrations": calibs, "ablations": runs}


async def released_for_class(db, asset_class: str, min_stage: str = "shadow") -> Optional[Dict]:
    """Die (einzige) Analyse der Klasse mit Stufe >= min_stage (ohne Chart-Daten)."""
    stages = ["shadow", "active"] if min_stage == "shadow" else ["active"]
    return await db.regime_analyses.find_one(
        {"release.stage": {"$in": stages}, "release.asset_classes": asset_class},
        {"_id": 0, "chart": 0, "chart_emas": 0})


async def all_releases(db) -> List[Dict]:
    rows = await db.regime_analyses.find(
        {"release.stage": {"$in": ["shadow", "active"]}},
        {"_id": 0, "id": 1, "name": 1, "symbols": 1, "timeframe": 1, "release": 1}).to_list(50)
    return rows


async def set_stage(db, aid: str, stage: str, evidence: Dict, by: str, reason: str,
                    proposal_id: Optional[str] = None, override: Optional[Dict] = None) -> Dict:
    """Stufe schreiben; Konkurrenz-Analyse derselben Klasse beim Hochstufen auf `none`."""
    doc = await db.regime_analyses.find_one({"id": aid}, {"_id": 0, "chart": 0, "chart_emas": 0})
    if not doc:
        raise ValueError("Analyse nicht gefunden")
    rel = apply_stage(doc, stage, evidence, by, reason, proposal_id, override)
    if stage != "none":
        for cls in rel.get("asset_classes") or []:
            other = await released_for_class(db, cls)
            if other and other.get("id") != aid:
                demoted = apply_stage(other, "none", {}, by, f"abgelöst durch {aid}", proposal_id)
                await db.regime_analyses.update_one({"id": other["id"]}, {"$set": {"release": demoted}})
    await db.regime_analyses.update_one({"id": aid}, {"$set": {"release": rel}})
    try:
        from services import structural_regime
        structural_regime.invalidate()
    except Exception:  # noqa: BLE001
        pass
    return rel


async def recent_proposal_exists(db, asset_class: str, days: int = PROPOSAL_COOLDOWN_DAYS) -> bool:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return bool(await db.ai_proposals.find_one(
        {"scope": PROPOSAL_SCOPE, "symbol": asset_class, "ts": {"$gte": cutoff}}, {"_id": 1}))


async def handle_recommendation(db, engine, rec: Dict) -> Optional[Dict]:
    """Forschungs-Analyst -> Proposal (1.4). Gate-Prüfung hier – die KI kann es nicht umgehen."""
    if not isinstance(rec, dict) or not rec.get("asset_class"):
        return None
    autonomy = str((engine.config or {}).get(CONFIG_KEY_AUTONOMY, "suggest"))
    if autonomy == "off":
        return None
    cls = str(rec.get("asset_class"))
    if await recent_proposal_exists(db, cls):
        return None
    doc = await released_for_class(db, cls)
    if not doc:
        return None
    rec = {**rec, "aid": doc["id"]}
    verdict = str(rec.get("verdict") or "").lower()
    gate_ok, reasons = True, []
    if verdict == "activate":
        from services import ai_rewards
        gate_ok, reasons, _ = validate_activation(await ai_rewards.by_structural_regime(db, 90),
                                                  activation_min_trades(quality_grade(doc)))
    elif verdict == "revoke" and (doc.get("release") or {}).get("stage") != "active":
        return None
    prop = proposal_for(rec, gate_ok, reasons, autonomy)
    if not prop:
        return None
    prop["symbol"] = cls
    await engine._insert_proposal(prop)
    if prop["status"] == "auto_applied" and prop.get("applied_at"):
        await set_stage(db, doc["id"], "shadow", {}, "ki_trader", prop["reason"], prop["id"])
    try:
        from services.ai_memory import memory
        await memory.remember("regime_release", f"Struktur-Regime {cls}: {verdict}",
                              f"{prop['status']}: {prop['reason']}", source="research_analyst",
                              tags=["regime", "release"])
    except Exception:  # noqa: BLE001
        pass
    return prop


async def apply_due_auto_proposals(db) -> int:
    """Karenz abgelaufen -> Stufe `active` vollziehen (aus dem Struktur-Loop)."""
    rows = await db.ai_proposals.find(
        {"scope": PROPOSAL_SCOPE, "status": "auto_applied", "applied_at": None,
         "pending_auto_until": {"$ne": None}}, {"_id": 0}).to_list(20)
    n = 0
    for p in rows:
        if not grace_due(p):
            continue
        ch = p.get("changes") or {}
        try:
            await set_stage(db, ch.get("aid"), ch.get("structural_regime_stage", "active"),
                            {}, "ki_trader", p.get("reason") or "Karenz abgelaufen", p.get("id"))
            await db.ai_proposals.update_one({"id": p["id"]}, {"$set": {"applied_at": _now_iso()}})
            n += 1
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Regime-Release Auto-Vollzug {p.get('id')}: {e}")
    return n
