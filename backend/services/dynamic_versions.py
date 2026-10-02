"""Phasen-Anpassung & Versionsverlauf dynamischer Strategien.

Je Regime (Phase) einer dynamischen Strategie kann der Nutzer nach
Bestätigung
  * „nicht handeln“ schalten (action=skip),
  * eine andere Ausgangs-Strategie wählen (action=strategy) oder
  * die im Regime-Lab/in der Werkbank optimierte Strategie dieser Phase
    (wieder) aktivieren (action=optimized).

Jede Änderung erzeugt eine neue Version in `dynamic_strategy_versions`
(vollständiger Schnappschuss der entscheidungsrelevanten Felder). Jede
ältere Version kann angesehen und wiederhergestellt werden – das
Wiederherstellen ist selbst wieder eine neue Version (nichts geht verloren).

Release (R09): eine geänderte Definition erzeugt eine neue Revision
(strategy_release.revised_release). Optional „Freigabe beibehalten“: dann
wird die neue Revision ausdrücklich freigegeben und ehrlich als Freigabe
ohne neuen Testnachweis protokolliert.
"""
import copy
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import strategy_plan, strategy_release

COLLECTION = "dynamic_strategy_versions"
SNAPSHOT_FIELDS = strategy_release._SEMANTIC_FIELDS + ("release", "verdict")
ACTIONS = ("skip", "strategy", "optimized", "variant")
LINEAGE_DEPTH = 8
OWN_RULES = "Eigene Regeln (Discovery)"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def snapshot_of(doc: Dict) -> Dict:
    """Entscheidungsrelevanter Stand einer dynamischen Strategie (rein)."""
    snap = {k: copy.deepcopy(doc.get(k)) for k in SNAPSHOT_FIELDS}
    s = doc.get("settings") or {}
    snap["skipped_regimes"] = list(s.get("skipped_regimes") or [])
    snap["phase_overrides"] = copy.deepcopy(s.get("phase_overrides") or {})
    return snap


def phase_summary(doc: Dict, registry) -> List[Dict]:
    """Je Regime: gehandelt? welche Strategie? (rein, gleiche Auflösung wie live)."""
    out = []
    for r in (doc.get("model") or {}).get("regimes") or []:
        plan = strategy_plan.resolve_symbol_plan(doc, "_", {"regime": r["id"], "label": r.get("label")})
        sub = registry.get(plan["strategy_id"] or "")
        traded = bool(sub) and not plan["unmapped"] and not getattr(sub, "IS_DYNAMIC", False)
        own = bool(plan.get("rules_override") and plan.get("sub_strategy"))
        out.append({"regime": int(r["id"]), "label": r.get("label") or f"#{int(r['id']) + 1}",
                    "traded": traded,
                    "strategy_id": plan["strategy_id"] if traded else None,
                    "strategy_name": ((OWN_RULES if own else getattr(sub, "STRATEGY_NAME", plan["strategy_id"]))
                                      if traded else None),
                    "own_rules": own and traded,
                    "rules": ((plan.get("sub_strategy") or {}).get("rules") or []) if own else [],
                    "trade_params": plan["overrides"], "strategy_params": plan["params"]})
    return out


def _explicit_mapping(doc: Dict) -> Dict[str, Optional[str]]:
    """Regime -> Strategie IMMER explizit (Alt-Dokumente ohne Mapping: alle
    Regime liefen mit der Basis-Strategie)."""
    mapping = {str(k): v for k, v in (doc.get("regime_strategies") or {}).items()}
    if mapping:
        return mapping
    return {str(r["id"]): doc.get("strategy_id") for r in (doc.get("model") or {}).get("regimes") or []}


def apply_phase_change(doc: Dict, rid: int, action: str, registry,
                       strategy_id: Optional[str] = None,
                       optimized: Optional[Dict] = None,
                       reset_trade_params: bool = False,
                       variant: Optional[Dict] = None) -> Dict:
    """Neue Definitionsfelder nach einer Phasen-Änderung (rein, wirft ValueError)."""
    key = str(int(rid))
    if key not in {str(r["id"]) for r in (doc.get("model") or {}).get("regimes") or []}:
        raise ValueError("Regime gehört nicht zu dieser dynamischen Strategie")
    if action not in ACTIONS:
        raise ValueError("action muss skip|strategy|optimized|variant sein")
    mapping = _explicit_mapping(doc)
    subs = copy.deepcopy(doc.get("sub_strategies") or {})
    params = copy.deepcopy(doc.get("regime_params") or {})
    configs = copy.deepcopy(doc.get("configs") or {})
    variants = copy.deepcopy(doc.get("rule_variants") or {})
    if action == "skip":
        mapping[key] = None
    elif action == "strategy":
        sub = registry.get(strategy_id or "")
        if sub is None or getattr(sub, "IS_DYNAMIC", False) or strategy_id == "ai_trader":
            raise ValueError("Unbekannte oder nicht wählbare Strategie")
        mapping[key] = strategy_id
        subs.pop(key, None)       # eigene Regeln gehörten zur alten Strategie
        params.pop(key, None)     # Strategie-Parameter ebenso
        variants.pop(key, None)
        if reset_trade_params:
            configs[key] = {}
    elif action == "variant":
        v = variant or {}
        if not v:
            raise ValueError("Variante nicht gefunden")
        if not v.get("traded"):
            mapping[key] = None
        else:
            if registry.get(v.get("strategy_id") or "") is None:
                raise ValueError("Strategie der Variante ist nicht mehr vorhanden")
            mapping[key] = v["strategy_id"]
            for store, val in ((subs, v.get("sub_strategy")), (params, v.get("strategy_params"))):
                if val:
                    store[key] = copy.deepcopy(val)
                else:
                    store.pop(key, None)
            configs[key] = copy.deepcopy(v.get("trade_params") or {})
        variants.pop(key, None)
    else:
        a = optimized or {}
        if not a:
            raise ValueError("Für diese Phase ist keine optimierte Strategie gespeichert")
        if a.get("definition"):
            mapping[key] = doc.get("strategy_id")
            subs[key] = {"rules": a.get("rules") or [], "definition": a.get("definition")}
        else:
            sid = a.get("strategy_id")
            if registry.get(sid or "") is None:
                raise ValueError("Optimierte Strategie ist nicht mehr vorhanden")
            mapping[key] = sid
            subs.pop(key, None)
        if a.get("strategy_params"):
            params[key] = a["strategy_params"]
        else:
            params.pop(key, None)
        configs[key] = a.get("trade_params") or {}
        variants.pop(key, None)
    return {"regime_strategies": mapping, "sub_strategies": subs, "regime_params": params,
            "configs": configs, "rule_variants": variants}


def next_release(prev_doc: Dict, new_doc: Dict, keep_release: bool, note: str) -> Dict:
    """Release der geänderten Definition (rein). keep_release nur, wenn die
    vorherige Definition validiert/freigegeben war."""
    prev_rel = prev_doc.get("release")
    rel = strategy_release.revised_release(prev_rel, new_doc, None)
    if rel.get("fingerprint") != (prev_rel or {}).get("fingerprint"):
        # Der alte Walk-Forward galt der alten Definition -> nicht neu validieren
        rel = {**strategy_release.initial_release(new_doc, prev_doc.get("verdict"), verdict_stale=True),
               "revision": rel.get("revision")}
    if keep_release and strategy_release.effective_status(prev_doc) in ("validated", "approved") \
            and rel.get("fingerprint") != (prev_doc.get("release") or {}).get("fingerprint"):
        tmp = {**new_doc, "release": rel}
        rel = strategy_release.approve(tmp, actor="admin", note=note,
                                       missing_evidence=strategy_release.approval_evidence_missing(tmp))
    return rel


async def list_versions(db, did: str, limit: int = 50) -> List[Dict]:
    return await db[COLLECTION].find({"dynamic_id": did}, {"_id": 0}) \
        .sort("version", -1).to_list(int(min(max(limit, 1), 200)))


def summary_with_metrics(doc: Dict, registry, optimized: Optional[Dict[int, Dict]] = None) -> List[Dict]:
    """Phasen-Übersicht + Kennzahlen der Optimierung, wenn die Phase genau die
    optimierte Zuordnung handelt (rein)."""
    snap = snapshot_of(doc)
    rows = phase_summary(doc, registry)
    for r in rows:
        a = (optimized or {}).get(r["regime"])
        if r["traded"] and _is_optimized(snap, str(r["regime"]), a):
            r["metrics"] = (a or {}).get("metrics") or {}
            r["score"] = (a or {}).get("score")
    return rows


async def _record(db, doc: Dict, registry, reason: str, change: Optional[Dict],
                  optimized: Optional[Dict[int, Dict]] = None) -> Dict:
    last = await db[COLLECTION].find_one({"dynamic_id": doc["id"]}, {"version": 1},
                                         sort=[("version", -1)])
    entry = {"id": f"dv_{uuid.uuid4().hex[:10]}", "dynamic_id": doc["id"],
             "version": int((last or {}).get("version") or 0) + 1,
             "created_at": _now_iso(), "reason": reason, "change": change,
             "release_status": strategy_release.effective_status(doc),
             "summary": summary_with_metrics(doc, registry, optimized), "snapshot": snapshot_of(doc)}
    await db[COLLECTION].insert_one(dict(entry))
    return entry


async def ensure_initial(db, doc: Dict, registry, optimized: Optional[Dict[int, Dict]] = None) -> None:
    """Vor der ersten Änderung den Ausgangsstand als Version 1 sichern."""
    if not await db[COLLECTION].find_one({"dynamic_id": doc["id"]}, {"_id": 1}):
        await _record(db, doc, registry, "Ausgangsstand (vor der ersten Phasen-Anpassung)", None, optimized)


def _with_settings(doc: Dict, skipped: List[int], overrides: Dict) -> Dict:
    s = dict(doc.get("settings") or {})
    s["skipped_regimes"] = sorted({int(x) for x in skipped})
    if not s["skipped_regimes"]:
        s.pop("skipped_regimes", None)
        s.pop("skipped_reason", None)
    s["phase_overrides"] = overrides
    return s


async def change_phase(db, doc: Dict, registry, rid: int, action: str,
                       strategy_id: Optional[str] = None, optimized: Optional[Dict] = None,
                       reset_trade_params: bool = False, keep_release: bool = False,
                       variant: Optional[Dict] = None,
                       optimized_map: Optional[Dict[int, Dict]] = None) -> Dict:
    """Phase ändern -> neue Version speichern. Rückgabe: {doc, version}."""
    await ensure_initial(db, doc, registry, optimized_map)
    fields = apply_phase_change(doc, rid, action, registry, strategy_id, optimized, reset_trade_params,
                                variant)
    new_doc = {**doc, **fields}
    label = next((r.get("label") for r in (doc.get("model") or {}).get("regimes") or []
                  if int(r["id"]) == int(rid)), f"#{int(rid) + 1}")
    if action == "skip" or (action == "variant" and not (variant or {}).get("traded")):
        target = None
    elif (action == "optimized" and (optimized or {}).get("definition")) or \
            (action == "variant" and (variant or {}).get("sub_strategy")):
        target = OWN_RULES
    else:
        sub = registry.get(fields["regime_strategies"][str(int(rid))] or "")
        target = getattr(sub, "STRATEGY_NAME", None)
    reason = {"skip": f"Phase „{label}“: nicht handeln",
              "strategy": f"Phase „{label}“: Strategie → {target}",
              "optimized": f"Phase „{label}“: optimierte Strategie aktiviert ({target})",
              "variant": f"Phase „{label}“: frühere Variante zurückgeholt ({(variant or {}).get('from') or ''}"
                         f" → {target or 'nicht handeln'})"}[action]
    skipped = [x for x in ((doc.get("settings") or {}).get("skipped_regimes") or []) if int(x) != int(rid)]
    overrides = dict((doc.get("settings") or {}).get("phase_overrides") or {})
    overrides[str(int(rid))] = {"action": action, "strategy_id": strategy_id, "at": _now_iso()}
    new_doc["settings"] = _with_settings(doc, skipped, overrides)
    new_doc["release"] = next_release(doc, new_doc, keep_release, f"Phasen-Anpassung: {reason}")
    change = {"regime": int(rid), "label": label, "action": action, "strategy_id": strategy_id,
              "strategy_name": target, "reset_trade_params": bool(reset_trade_params),
              "keep_release": bool(keep_release)}
    await _save(db, new_doc)
    version = await _record(db, new_doc, registry, reason, change, optimized_map)
    return {"doc": new_doc, "version": version}


async def restore(db, doc: Dict, registry, version: int, keep_release: bool = False,
                  optimized_map: Optional[Dict[int, Dict]] = None) -> Dict:
    """Ältere Version wiederherstellen (wird selbst eine neue Version)."""
    v = await db[COLLECTION].find_one({"dynamic_id": doc["id"], "version": int(version)}, {"_id": 0})
    if not v:
        raise ValueError("Version nicht gefunden")
    await ensure_initial(db, doc, registry, optimized_map)
    snap = copy.deepcopy(v.get("snapshot") or {})
    new_doc = {**doc, **{k: snap.get(k) for k in strategy_release._SEMANTIC_FIELDS}}
    new_doc["verdict"] = snap.get("verdict") or doc.get("verdict")
    new_doc["settings"] = _with_settings(doc, snap.get("skipped_regimes") or [],
                                         snap.get("phase_overrides") or {})
    # Release der Version gilt wieder, wenn ihre Definition exakt zurückkommt
    old_rel = snap.get("release")
    if isinstance(old_rel, dict) and old_rel.get("fingerprint") == strategy_release.definition_fingerprint(new_doc):
        new_doc["release"] = old_rel
    else:
        new_doc["release"] = next_release(doc, new_doc, keep_release,
                                          f"Wiederherstellung von Version {version}")
    await _save(db, new_doc)
    entry = await _record(db, new_doc, registry, f"Version {version} wiederhergestellt",
                          {"action": "restore", "from_version": int(version)}, optimized_map)
    return {"doc": new_doc, "version": entry}


async def _save(db, doc: Dict) -> None:
    keys = strategy_release._SEMANTIC_FIELDS + ("release", "verdict", "settings")
    await db.dynamic_strategies.update_one({"id": doc["id"]},
                                           {"$set": {k: doc.get(k) for k in keys}})


def _is_optimized(snap: Dict, key: str, a: Optional[Dict]) -> bool:
    """Handelt die Phase in diesem Stand genau die optimierte Zuordnung? (rein)"""
    if not a:
        return False
    sub = (snap.get("sub_strategies") or {}).get(key) or {}
    if a.get("definition"):
        return bool(sub) and sub.get("definition") == a.get("definition")
    return not sub and (snap.get("regime_strategies") or {}).get(key) == a.get("strategy_id")


def compare(va: Dict, vb: Dict, optimized: Dict[int, Dict]) -> Dict:
    """Zwei Versionen je Phase nebeneinander (rein). Kennzahlen gibt es nur,
    wenn die Phase die optimierte Zuordnung handelt (Ergebnis der Optimierung)
    – sonst None (ehrlich: dafür einen Backtest starten)."""
    def side(v: Dict, rid: int) -> Dict:
        p = next((x for x in v.get("summary") or [] if int(x["regime"]) == rid), None) or {}
        a = optimized.get(rid)
        opt = bool(p.get("traded")) and _is_optimized(v.get("snapshot") or {}, str(rid), a)
        return {"traded": bool(p.get("traded")), "strategy_name": p.get("strategy_name"),
                "own_rules": bool(p.get("own_rules")), "trade_params": p.get("trade_params") or {},
                "strategy_params": p.get("strategy_params") or {}, "is_optimized": opt,
                "metrics": ((a or {}).get("metrics") or {}) if opt else None,
                "score": (a or {}).get("score") if opt else None}
    rids = sorted({int(x["regime"]) for v in (va, vb) for x in v.get("summary") or []})
    labels = {int(x["regime"]): x.get("label") for v in (va, vb) for x in v.get("summary") or []}
    rows = []
    for rid in rids:
        a, b = side(va, rid), side(vb, rid)
        keys = ("traded", "strategy_name", "own_rules", "trade_params", "strategy_params")
        rows.append({"regime": rid, "label": labels.get(rid), "a": a, "b": b,
                     "changed": any(a[k] != b[k] for k in keys)})
    head = lambda v: {k: v.get(k) for k in ("version", "created_at", "reason", "release_status")}  # noqa: E731
    return {"a": head(va), "b": head(vb), "phases": rows,
            "changed_count": sum(1 for r in rows if r["changed"])}


# ---------------- Varianten je Phase, Verlauf über Optimierungs-Läufe ----------------
def phase_variant(snap: Dict, rid: int) -> Dict:
    """Variante einer Phase in einem Stand (rein): gehandelt?, Strategie,
    eigene Regeln, Strategie- und Trade-Parameter."""
    key = str(int(rid))
    mapping = {str(k): v for k, v in (snap.get("regime_strategies") or {}).items()}
    sid = mapping.get(key) if mapping else snap.get("strategy_id")
    traded = bool(sid)
    return {"traded": traded, "strategy_id": sid if traded else None,
            "sub_strategy": copy.deepcopy((snap.get("sub_strategies") or {}).get(key)) if traded else None,
            "strategy_params": copy.deepcopy((snap.get("regime_params") or {}).get(key) or {}) if traded else {},
            "trade_params": copy.deepcopy((snap.get("configs") or {}).get(key) or {}) if traded else {}}


def _variant_key(v: Dict) -> str:
    return strategy_plan._canonical(v)


def carry_overrides(src: Dict, new: Dict, improved_rids: List[int]) -> Dict:
    """Nach „Bestehende optimieren“: deine Phasen-Anpassungen der Quelle bleiben
    in Phasen erhalten, die dieser Lauf NICHT verbessert hat (rein).
    Rückgabe: {fields, carried: [rid], swapped: bool}."""
    overrides = (src.get("settings") or {}).get("phase_overrides") or {}
    rids = [int(k) for k in overrides if int(k) not in {int(x) for x in improved_rids}]
    model_ids = {int(r["id"]) for r in (new.get("model") or {}).get("regimes") or []}
    rids = [r for r in rids if r in model_ids]
    if not rids:
        return {"fields": {}, "carried": [], "swapped": False}
    src_snap = snapshot_of(src)
    doc = copy.deepcopy(new)
    swapped = False
    for rid in rids:
        v = phase_variant(src_snap, rid)
        key = str(rid)
        mapping = _explicit_mapping(doc)
        mapping[key] = v["strategy_id"] if v["traded"] else None
        doc["regime_strategies"] = mapping
        for field, val in (("sub_strategies", v["sub_strategy"]), ("regime_params", v["strategy_params"]),
                           ("configs", v["trade_params"])):
            store = dict(doc.get(field) or {})
            if val:
                store[key] = val
            else:
                store.pop(key, None)
            doc[field] = store
        swapped = swapped or (v["traded"] and overrides.get(key, {}).get("action") != "skip")
    keep = {str(r): overrides[str(r)] for r in rids}
    s = dict(doc.get("settings") or {})
    s["phase_overrides"] = keep
    skipped = [x for x in (s.get("skipped_regimes") or []) if int(x) not in rids]
    s["skipped_regimes"] = skipped
    if not skipped:
        s.pop("skipped_regimes", None)
        s.pop("skipped_reason", None)
    fields = {k: doc.get(k) for k in ("regime_strategies", "sub_strategies", "regime_params", "configs")}
    fields["settings"] = s
    return {"fields": fields, "carried": rids, "swapped": swapped}


async def after_refine(db, registry, src_id: str, new_id: str, improved_rids: List[int],
                       optimized_map: Optional[Dict[int, Dict]] = None) -> Optional[Dict]:
    """Neue Version aus „Bestehende optimieren“: Herkunft merken, Phasen-
    Anpassungen nicht verbesserter Phasen übernehmen, Version 1 mit Herkunft."""
    src = await db.dynamic_strategies.find_one({"id": src_id}, {"_id": 0})
    new = await db.dynamic_strategies.find_one({"id": new_id}, {"_id": 0})
    if not src or not new:
        return None
    res = carry_overrides(src, new, improved_rids)
    new = {**new, **res["fields"]}
    s = dict(new.get("settings") or {})
    s["refined_from"] = src_id
    s["refined_from_name"] = src.get("name")
    s["refined_regimes"] = sorted(int(x) for x in improved_rids)
    new["settings"] = s
    if res["carried"]:
        # Übernommene Strategie-Tausche hat der Walk-Forward nicht geprüft -> Entwurf
        new["release"] = strategy_release.initial_release(new, new.get("verdict"),
                                                          verdict_stale=res["swapped"])
    await _save(db, new)
    labels = {int(r["id"]): r.get("label") for r in (new.get("model") or {}).get("regimes") or []}
    parts = [f"optimiert: {', '.join(labels.get(int(r), str(r)) for r in improved_rids) or 'keine Verbesserung'}"]
    if res["carried"]:
        parts.append(f"deine Anpassungen übernommen: {', '.join(labels.get(r, str(r)) for r in res['carried'])}")
    await _record(db, new, registry, f"Optimiert aus „{src.get('name')}“ ({'; '.join(parts)})",
                  {"action": "refine", "from_dynamic_id": src_id, "improved": sorted(improved_rids),
                   "carried": res["carried"]}, optimized_map)
    return {"carried": res["carried"]}


async def lineage(db, doc: Dict, depth: int = LINEAGE_DEPTH) -> List[Dict]:
    """Aktuelle Strategie + Vorgänger aus „Bestehende optimieren“ (neueste zuerst)."""
    out, seen = [doc], {doc["id"]}
    cur = doc
    while len(out) < depth:
        pid = (cur.get("settings") or {}).get("refined_from")
        if not pid or pid in seen:
            break
        cur = await db.dynamic_strategies.find_one({"id": pid}, {"_id": 0, "last_state": 0, "runtime_state": 0})
        if not cur:
            break
        out.append(cur)
        seen.add(pid)
    return out


async def phase_history(db, doc: Dict, rid: int, registry) -> List[Dict]:
    """Welche Varianten hatte diese Phase bisher – über Phasen-Anpassungen UND
    frühere Optimierungs-Läufe (Vorgänger-Strategien). Gleiche Varianten werden
    zusammengefasst (neueste zuerst)."""
    entries, seen = [], set()
    cur_key = _variant_key(phase_variant(snapshot_of(doc), rid))
    for d in await lineage(db, doc):
        rows = await db[COLLECTION].find({"dynamic_id": d["id"]}, {"_id": 0}).sort("version", -1).to_list(100)
        if not rows:
            rows = [{"version": None, "created_at": d.get("created_at"), "reason": "Stand bei Erstellung",
                     "snapshot": snapshot_of(d), "summary": phase_summary(d, registry)}]
        for v in rows:
            var = phase_variant(v.get("snapshot") or {}, rid)
            k = _variant_key(var)
            if k in seen:
                continue
            seen.add(k)
            p = next((x for x in v.get("summary") or [] if int(x["regime"]) == int(rid)), {})
            entries.append({"dynamic_id": d["id"], "dynamic_name": d.get("name"), "version": v.get("version"),
                            "created_at": v.get("created_at"), "reason": v.get("reason"),
                            "traded": var["traded"], "strategy_name": p.get("strategy_name"),
                            "own_rules": bool(var["sub_strategy"]),
                            "rules": (var["sub_strategy"] or {}).get("rules") or [],
                            "trade_params": var["trade_params"], "strategy_params": var["strategy_params"],
                            "metrics": p.get("metrics"), "score": p.get("score"),
                            "current": k == cur_key})
    return entries


async def variant_from(db, doc: Dict, src_id: str, version: Optional[int], rid: int) -> Dict:
    """Variante einer Phase aus einem Stand der Linie laden (für „zurückholen“)."""
    if src_id not in {d["id"] for d in await lineage(db, doc)}:
        raise ValueError("Stand gehört nicht zur Linie dieser Strategie")
    if version is not None:
        v = await db[COLLECTION].find_one({"dynamic_id": src_id, "version": int(version)}, {"_id": 0})
        if not v:
            raise ValueError("Version nicht gefunden")
        snap, label = v.get("snapshot") or {}, f"v{int(version)}"
    else:
        src = await db.dynamic_strategies.find_one({"id": src_id}, {"_id": 0})
        snap, label = snapshot_of(src or {}), "Erstellung"
    var = phase_variant(snap, rid)
    if src_id != doc["id"]:
        name = (await db.dynamic_strategies.find_one({"id": src_id}, {"name": 1}) or {}).get("name")
        label = f"{name} · {label}"
    var["from"] = label
    return var
