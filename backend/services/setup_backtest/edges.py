"""Edge-Register des Setup-Backtests (09/2026).

Problem vorher: `settings.setup_backtest_state` hielt je Klasse × Setup nur den
ZULETZT getesteten Stand. Fiel ein bereits bestätigter Edge in einem späteren
Lauf durch (anderes Datenfenster, verkürzte Historie, Marktphase), wurde er
überschrieben (Status exhausted, `tuned` gelöscht, OOS-Trades gelöscht) – und
ein knapp bestandener, schmaler Satz konnte einen breit bestätigten ersetzen.

Jetzt:
  * Jeder bestandene Parameter-Satz landet dauerhaft in `setup_backtest_edges`
    (ein Dokument je Klasse × Setup × Parameter-Fingerprint, mit Verlauf).
  * Je Klasse × Setup ist genau EIN Edge "aktiv" – der wird gehandelt/geprompt
    und in jedem Lauf ZUERST erneut geprüft.
  * Ersetzt wird der aktive Edge nur durch einen nachweislich robusteren
    Kandidaten (`better`): Robustheits-Score >= +REPLACE_MARGIN, mind.
    MIN_TRADES_RATIO der OOS-Trades, keine harten Overfitting-Signale.
  * Fällt der aktive Edge auf frischen Daten durch, bleibt er (Zähler `stale`);
    erst nach STALE_MAX aufeinanderfolgenden Fehlläufen gilt das Setup als
    "stale" (kein Edge fürs Trading), der Edge selbst bleibt reaktivierbar.
  * Rollback: `activate()` setzt einen älteren Edge wieder aktiv (inkl. seiner
    gespeicherten OOS-Trades fürs Reife-Gate). `recover_from_history()` holt
    früher bestandene Sätze aus dem Verlauf zurück (rückwirkend).

Reine Funktionen (testbar): ppt, overfit_flags, robust_score, summarize,
fingerprint, better, decide.
"""
import hashlib
import json
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from services.setup_backtest import analysis, weights

logger = logging.getLogger(__name__)

COLLECTION = "setup_backtest_edges"
STALE_MAX = 3               # Fehlläufe in Folge, bis der aktive Edge als 'stale' gilt
REPLACE_MARGIN = 0.10       # Kandidat muss >= 10 % robuster sein als der aktive Edge
MIN_TRADES_RATIO = 0.7      # ... und mind. 70 % der OOS-Trades des aktiven Edge liefern
OOS_IS_CONSISTENCY = 0.35   # OOS-PnL/Trade < 35 % des IS-Werts = Overfitting-Signal
MIN_PF = 1.15               # Profit-Faktor darunter = Edge zu dünn
WR_CRV_MARGIN = 3.0         # Winrate muss Break-even-WR (aus Payoff) um >= 3 Pp. schlagen
MAX_TRADES_STORED = 150
KEEP_PER_SETUP = 20
TRADES_KEEP_TOP = 3         # nicht-aktive Edges, die ihre OOS-Trades (für Rollback) behalten
CHALLENGERS = 2             # gespeicherte Herausforderer, die je Schleifen-Lauf mitgeprüft werden
ACTIVE_STATES = ("passed", "tuned")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --------------------------------------------------------------------------
# Reine Bewertung
# --------------------------------------------------------------------------
def ppt(stats: Optional[Dict]) -> float:
    """PnL je Trade (rein)."""
    n = int((stats or {}).get("trades") or 0)
    return float((stats or {}).get("pnl") or 0) / n if n else 0.0


def fingerprint(params: Optional[Dict]) -> str:
    """Stabiler Schlüssel eines Parameter-Satzes (ohne `name`), rein."""
    clean = {k: v for k, v in (params or {}).items() if k != "name"}
    raw = json.dumps(clean, sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def overfit_flags(res: Dict) -> Dict[str, List[str]]:
    """Overfitting-/Robustheits-Signale eines Backtest-Ergebnisses (rein).
    'hard' blockiert die Übernahme als neuer aktiver Edge, 'soft' nur Hinweis."""
    hard: List[str] = []
    soft: List[str] = []
    is_st, oos_st = res.get("is") or {}, res.get("oos") or {}
    is_ppt, oos_ppt = ppt(is_st), ppt(oos_st)
    if is_ppt > 0 and oos_ppt < is_ppt * OOS_IS_CONSISTENCY:
        hard.append(f"OOS-Abfall: {oos_ppt:+.3f}/Trade vs. IS {is_ppt:+.3f} "
                    f"(<{int(OOS_IS_CONSISTENCY * 100)} %)")
    windows = oos_st.get("windows")
    if windows:
        pos = int(oos_st.get("windows_pos") or 0)
        if pos < len(windows):
            soft.append(f"nur {pos}/{len(windows)} Walk-Forward-Fenster positiv")
    req = res.get("min_trades") or {}
    n_oos = int(oos_st.get("trades") or 0)
    min_oos = int(req.get("oos") or 0)
    if min_oos and n_oos < min_oos * 1.5:
        soft.append(f"knapp über Mindest-Trades ({n_oos} vs. min {min_oos})")
    d = ((res.get("diag") or {}).get("oos")) or {}
    pf = d.get("pf")
    if isinstance(pf, (int, float)) and 0 < float(pf) < MIN_PF:
        hard.append(f"Profit-Faktor OOS {float(pf):.2f} < {MIN_PF}")
    payoff = d.get("payoff")
    if isinstance(payoff, (int, float)) and float(payoff) > 0 and n_oos:
        be = 100.0 / (1.0 + float(payoff))
        wr = float(oos_st.get("winrate") or 0)
        if wr < be + WR_CRV_MARGIN:
            hard.append(f"Winrate {wr:.0f} % deckt CRV kaum (Break-even {be:.0f} % bei Payoff {float(payoff):.2f})")
    return {"hard": hard, "soft": soft}


def robust_score(res: Dict) -> float:
    """Robustheit eines Ergebnisses (rein): analysis.score (PnL/Trade, OOS-gewichtet,
    Drawdown-bestraft) × Evidenz (√ Trades/Minimum, max 2.5 – viele Trades zählen,
    nicht nur hoher PnL je Trade) × Walk-Forward-Anteil × IS/OOS-Konsistenz
    × 0.8 je hartem Overfitting-Signal. Ein Fehllauf bleibt <= 0 (kein Edge)."""
    is_st, oos_st = res.get("is") or {}, res.get("oos") or {}
    sc = analysis.score(is_st, oos_st, res.get("diag"))
    if not res.get("passed") or sc <= 0:
        return round(min(sc, 0.0) + min(0.0, ppt(oos_st)), 4)
    n_oos = int(oos_st.get("trades") or 0)
    min_oos = max(1, int((res.get("min_trades") or {}).get("oos") or 10))
    n_fac = min(2.5, math.sqrt(n_oos / min_oos)) if n_oos else 0.0
    windows = oos_st.get("windows")
    wf = 0.5 + 0.5 * int(oos_st.get("windows_pos") or 0) / len(windows) if windows else 1.0
    is_ppt, oos_ppt = ppt(is_st), ppt(oos_st)
    cons = max(0.5, min(1.0, oos_ppt / is_ppt)) if is_ppt > 0 else 0.75
    hard = len(overfit_flags(res)["hard"])
    return round(sc * n_fac * wf * cons * (0.8 ** hard), 4)


def _compact(d: Optional[Dict]) -> Optional[Dict]:
    """analysis.compact – toleriert bereits kompakte Diagnosen (Historie/Register)."""
    if not d:
        return None
    exits = d.get("exits") or {}
    if exits and all(not isinstance(v, dict) for v in exits.values()):
        return dict(d)
    return analysis.compact(d)


def summarize(res: Dict, source: str, variant_idx: int) -> Dict:
    """Bewertungs-Kern eines Ergebnisses für Register/Vergleich (rein)."""
    params = dict(res.get("params") or res.get("effective_params") or {})
    params.pop("name", None)
    flags = overfit_flags(res)
    return {"params": params, "fingerprint": fingerprint(params),
            "name": res.get("variant_name") or res.get("name") or "?",
            "variant": int(variant_idx), "source": source,
            "is": res.get("is") or {}, "oos": res.get("oos") or {},
            "min_trades": res.get("min_trades"), "passed": bool(res.get("passed")),
            "diag": {"is": _compact((res.get("diag") or {}).get("is")),
                     "oos": _compact((res.get("diag") or {}).get("oos"))},
            "robust": robust_score(res), "flags": flags}


def better(cand: Dict, active: Dict) -> Tuple[bool, str]:
    """Darf `cand` den aktiven Edge ersetzen? (rein) Beide = summarize()-Dicts."""
    if not cand.get("passed"):
        return False, "Kandidat ohne Edge"
    hard = (cand.get("flags") or {}).get("hard") or []
    if hard and not ((active.get("flags") or {}).get("hard")):
        return False, "Overfitting-Verdacht: " + "; ".join(hard[:2])
    c_n, a_n = int((cand.get("oos") or {}).get("trades") or 0), int((active.get("oos") or {}).get("trades") or 0)
    if a_n and c_n < a_n * MIN_TRADES_RATIO:
        return False, f"zu wenig OOS-Trades ({c_n} vs. {a_n} beim aktiven Edge)"
    c_r, a_r = float(cand.get("robust") or 0), float(active.get("robust") or 0)
    need = a_r * (1 + REPLACE_MARGIN) if a_r > 0 else 0.0
    if c_r <= need + 1e-9:
        return False, f"nicht robuster (Score {c_r:+.3f} vs. {a_r:+.3f}, nötig > {need:+.3f})"
    return True, f"robuster (Score {c_r:+.3f} vs. {a_r:+.3f}, OOS {c_n}T vs. {a_n}T)"


def decide(active: Optional[Dict], active_res: Optional[Dict], cand: Optional[Dict],
           stale: int = 0) -> Dict:
    """Entscheidung eines Laufs (rein).
    active     = gespeicherter aktiver Edge (summarize-Felder) oder None
    active_res = summarize() des aktiven Satzes auf den AKTUELLEN Daten (oder None)
    cand       = summarize() des besten anderen bestandenen Satzes dieses Laufs (oder None)
    Rückgabe {'action': adopt|confirm|replace|keep|stale_keep|stale|none, 'why', 'stale'}."""
    if active is None:
        if cand and cand.get("passed"):
            hard = (cand.get("flags") or {}).get("hard") or []
            return {"action": "adopt", "stale": 0,
                    "why": "erster Edge" + (f" (Hinweis: {hard[0]})" if hard else "")}
        return {"action": "none", "stale": 0, "why": "kein Edge gefunden"}
    active_ok = bool(active_res and active_res.get("passed"))
    if cand and cand.get("passed") and cand.get("fingerprint") != active.get("fingerprint"):
        ref = active_res if active_ok else active
        ok, why = better(cand, ref)
        if ok:
            return {"action": "replace", "stale": 0, "why": why}
        if not active_ok and stale + 1 >= STALE_MAX and not ((cand.get("flags") or {}).get("hard")):
            return {"action": "replace", "stale": 0,
                    "why": f"aktiver Edge {STALE_MAX}× in Folge ohne Edge – Kandidat übernimmt"}
        if active_ok:
            return {"action": "confirm", "stale": 0, "why": f"aktiver Edge bestätigt; Kandidat {why}"}
        return {"action": "stale_keep" if stale + 1 < STALE_MAX else "stale", "stale": stale + 1,
                "why": f"aktiver Edge diesmal ohne Edge ({stale + 1}/{STALE_MAX}); Kandidat {why}"}
    if active_ok:
        return {"action": "confirm", "stale": 0, "why": "aktiver Edge erneut bestätigt"}
    nxt = stale + 1
    if nxt >= STALE_MAX:
        return {"action": "stale", "stale": nxt,
                "why": f"aktiver Edge {nxt}× in Folge ohne Edge – kein Edge fürs Trading (bleibt reaktivierbar)"}
    return {"action": "stale_keep", "stale": nxt,
            "why": f"aktiver Edge diesmal ohne Edge ({nxt}/{STALE_MAX}) – bleibt gültig"}


def public(doc: Dict) -> Dict:
    """Edge-Dokument fürs UI (ohne Trades/_id)."""
    return {k: v for k, v in doc.items() if k not in ("_id", "oos_trades")}


# --------------------------------------------------------------------------
# Persistenz
# --------------------------------------------------------------------------
async def challengers(db, cls: str, sid: str, active: Optional[Dict], limit: int = CHALLENGERS) -> List[Dict]:
    """Robusteste NICHT-aktive Register-Edges eines Setups – werden in Schleifen-
    Läufen auf den aktuellen Daten mitgeprüft und können den aktiven Edge über
    `better()` ablösen (z. B. ein früher breit bestätigter, später abgelöster Satz)."""
    q: Dict = {"asset_class": cls, "setup": sid, "status": {"$ne": "active"}}
    if active:
        q["fingerprint"] = {"$ne": active.get("fingerprint")}
    docs = await db[COLLECTION].find(q, {"oos_trades": 0}).to_list(200)
    docs.sort(key=lambda d: -float(d.get("best_robust") or 0))
    return [d for d in docs if float(d.get("best_robust") or 0) > 0][:limit]


async def get_active(db, cls: str, sid: str) -> Optional[Dict]:
    return await db[COLLECTION].find_one({"asset_class": cls, "setup": sid, "status": "active"})


async def record(db, cls: str, sid: str, summary: Dict, *, days: int, job_id: str,
                 oos_trades: Optional[List[Dict]] = None, status: str = "candidate") -> Dict:
    """Ergebnis eines bestandenen Satzes registrieren (Upsert je Fingerprint):
    Statistik/Zeitstempel aktualisieren, Bestätigungen zählen, Trades sichern."""
    now = _now_iso()
    q = {"asset_class": cls, "setup": sid, "fingerprint": summary["fingerprint"]}
    cur = await db[COLLECTION].find_one(q)
    run = {"at": now, "days": int(days), "job_id": job_id, "is": summary["is"], "oos": summary["oos"],
           "robust": summary["robust"], "passed": bool(summary.get("passed"))}
    runs = (list((cur or {}).get("runs") or []) + [run])[-KEEP_PER_SETUP:]
    best_robust = max(float((cur or {}).get("best_robust") or -1e9), float(summary["robust"]))
    upd = {"params": summary["params"], "name": summary["name"], "variant": summary["variant"],
           "source": (cur or {}).get("source") or summary["source"],
           "is": summary["is"], "oos": summary["oos"], "min_trades": summary.get("min_trades"),
           "diag": summary["diag"], "robust": summary["robust"], "best_robust": round(best_robust, 4),
           "flags": summary["flags"], "last_run_at": now, "runs": runs, "days": int(days)}
    if summary.get("passed"):
        upd["last_confirmed_at"] = now
        if oos_trades:
            upd["oos_trades"] = [dict(t) for t in oos_trades[:MAX_TRADES_STORED]]
    if cur is None:
        doc = {"id": uuid.uuid4().hex[:12], **q, "status": status, "created_at": now,
               "first_found_at": now, "confirmations": 1 if summary.get("passed") else 0,
               "stale": 0, **upd}
        await db[COLLECTION].insert_one(doc)
        return doc
    ops: Dict = {"$set": upd}
    if summary.get("passed"):
        ops["$inc"] = {"confirmations": 1}
    await db[COLLECTION].update_one(q, ops)
    return {**cur, **upd, "confirmations": int(cur.get("confirmations") or 0) + (1 if summary.get("passed") else 0)}


async def set_active(db, cls: str, sid: str, edge_id: str, *, reason: str) -> None:
    """Genau einen Edge je Klasse × Setup aktiv setzen; bisher aktiver -> 'retired'."""
    now = _now_iso()
    await db[COLLECTION].update_many(
        {"asset_class": cls, "setup": sid, "status": "active", "id": {"$ne": edge_id}},
        {"$set": {"status": "retired", "retired_at": now, "retired_reason": reason}})
    await db[COLLECTION].update_one(
        {"asset_class": cls, "setup": sid, "id": edge_id},
        {"$set": {"status": "active", "activated_at": now, "activated_reason": reason, "stale": 0}})


async def mark_stale(db, cls: str, sid: str, stale: int) -> None:
    await db[COLLECTION].update_one({"asset_class": cls, "setup": sid, "status": "active"},
                                    {"$set": {"stale": int(stale), "last_run_at": _now_iso()}})


async def prune(db, cls: str, sid: str) -> None:
    """Höchstens KEEP_PER_SETUP Edges je Klasse × Setup (älteste/schwächste
    nicht-aktive weg); gespeicherte OOS-Trades behalten nur der aktive Edge und
    die TRADES_KEEP_TOP robustesten übrigen (Speicher-Quota)."""
    docs = await db[COLLECTION].find({"asset_class": cls, "setup": sid, "status": {"$ne": "active"}},
                                     {"_id": 1, "best_robust": 1, "last_run_at": 1}).to_list(500)
    docs.sort(key=lambda d: (-float(d.get("best_robust") or 0), str(d.get("last_run_at") or "")))
    if len(docs) > KEEP_PER_SETUP - 1:
        drop = [d["_id"] for d in docs[KEEP_PER_SETUP - 1:]]
        await db[COLLECTION].delete_many({"_id": {"$in": drop}})
        docs = docs[:KEEP_PER_SETUP - 1]
    strip = [d["_id"] for d in docs[TRADES_KEEP_TOP:]]
    if strip:
        await db[COLLECTION].update_many({"_id": {"$in": strip}, "oos_trades": {"$exists": True}},
                                         {"$unset": {"oos_trades": ""}})


async def list_edges(db, cls: Optional[str] = None, sid: Optional[str] = None) -> List[Dict]:
    q: Dict = {}
    if cls:
        q["asset_class"] = cls
    if sid:
        q["setup"] = sid
    docs = await db[COLLECTION].find(q, {"oos_trades": 0}).to_list(2000)
    order = {"active": 0, "candidate": 1, "retired": 2}
    docs.sort(key=lambda d: (d["asset_class"], d["setup"], order.get(d.get("status"), 3),
                             -float(d.get("best_robust") or 0)))
    return [public(d) for d in docs]


def entry_from_edge(entry: Dict, edge: Dict, *, status: str = "tuned") -> Dict:
    """State-Eintrag (settings.setup_backtest_state) auf einen Edge setzen (rein)."""
    out = dict(entry or {})
    out.update({"status": status, "variant": int(edge.get("variant") or 0), "tuned": dict(edge.get("params") or {}),
                "name": edge.get("name"), "is": edge.get("is") or {}, "oos": edge.get("oos") or {},
                "min_trades": edge.get("min_trades"), "edge_id": edge.get("id"),
                "robust": edge.get("robust"), "flags": edge.get("flags"), "stale": 0,
                "updated_at": _now_iso()})
    out["tuned"]["name"] = edge.get("name") or "Edge"
    for k in ("ai_proposal", "ai_proposal_meta"):
        out.pop(k, None)
    return out


async def activate(db, cls: str, sid: str, edge_id: str, *, reason: str = "manuell") -> Dict:
    """Rollback/Reaktivierung: Edge aktiv setzen, State-Eintrag übernehmen,
    gespeicherte OOS-Trades fürs Reife-Gate wiederherstellen."""
    from services.setup_backtest import runner
    edge = await db[COLLECTION].find_one({"asset_class": cls, "setup": sid, "id": edge_id})
    if not edge:
        raise ValueError("Edge nicht gefunden")
    await set_active(db, cls, sid, edge_id, reason=reason)
    state = await runner.load_state(db)
    classes = dict(state.get("classes") or {})
    cls_state = dict(classes.get(cls) or {})
    cls_state[sid] = entry_from_edge(cls_state.get(sid) or {}, edge)
    cls_state[sid]["edge_action"] = "rollback"
    cls_state[sid]["edge_note"] = f"reaktiviert ({reason})"
    classes[cls] = cls_state
    state["classes"] = classes
    await runner.save_state(db, state)
    trades = list(edge.get("oos_trades") or [])
    await db[weights.COLLECTION].delete_many({"asset_class": cls, "setup": sid})
    if trades:
        now = _now_iso()
        await db[weights.COLLECTION].insert_many([{**t, "run_at": now, "edge_id": edge_id} for t in trades])
    return {"edge": public({**edge, "status": "active"}), "entry": cls_state[sid], "restored_trades": len(trades)}


def _history_edges(sid: str, entry: Dict) -> List[Dict]:
    """Bestandene Historien-Einträge MIT Parametern als summarize()-Dicts (rein)."""
    out = []
    for h in entry.get("history") or []:
        if not (h.get("passed") and isinstance(h.get("params"), dict)):
            continue
        res = {"params": h["params"], "variant_name": h.get("name"), "is": h.get("is"), "oos": h.get("oos"),
               "passed": True, "diag": h.get("diag"), "min_trades": entry.get("min_trades")}
        out.append({**summarize(res, "recovered", int(h.get("variant") or 0)), "at": h.get("at")})
    return out


async def recover_from_history(db, state: Optional[Dict] = None, *, activate_best: bool = True) -> Dict:
    """Rückwirkend: früher bestandene Sätze aus dem State-Verlauf ins Register
    holen. Idempotent (Upsert je Fingerprint). Ohne aktiven Edge wird – wenn
    gewünscht – der robusteste wiederhergestellte Satz aktiv gesetzt, damit der
    nächste Lauf ihn zuerst prüft und die KI ihn wieder kennt."""
    from services.setup_backtest import runner
    state = state or await runner.load_state(db)
    classes = dict(state.get("classes") or {})
    imported, activated = 0, []
    changed = False
    for cls, setups in classes.items():
        for sid, entry in list((setups or {}).items()):
            if not isinstance(entry, dict):
                continue
            cands = _history_edges(sid, entry)
            if not cands:
                continue
            current_fp = fingerprint(runner.effective_params_of(sid, entry) or {}) \
                if entry.get("status") in ACTIVE_STATES else None
            for c in cands:
                exists = await db[COLLECTION].find_one({"asset_class": cls, "setup": sid, "fingerprint": c["fingerprint"]}, {"_id": 1})
                if exists:
                    continue
                status = "active" if (current_fp and c["fingerprint"] == current_fp
                                      and not await get_active(db, cls, sid)) else "candidate"
                await record(db, cls, sid, c, days=int(entry.get("days") or runner.DEFAULT_DAYS),
                             job_id="recovered", status=status)
                imported += 1
            if activate_best and not await get_active(db, cls, sid) and entry.get("status") not in ACTIVE_STATES:
                best = max(cands, key=lambda c: float(c.get("robust") or 0))
                if float(best.get("robust") or 0) > 0 and not best["flags"]["hard"]:
                    doc = await db[COLLECTION].find_one({"asset_class": cls, "setup": sid, "fingerprint": best["fingerprint"]})
                    if doc:
                        await set_active(db, cls, sid, doc["id"], reason="rückwirkend wiederhergestellt")
                        new_entry = entry_from_edge(entry, doc)
                        new_entry.update({"edge_action": "recovered", "recovered_at": _now_iso(),
                                          "edge_note": f"rückwirkend aus Verlauf ({best.get('at', '')[:10]}) wiederhergestellt – nächster Lauf prüft zuerst"})
                        setups[sid] = new_entry
                        activated.append(f"{sid}@{cls}")
                        changed = True
    if changed:
        state["classes"] = classes
        await runner.save_state(db, state)
    return {"imported": imported, "activated": activated}
