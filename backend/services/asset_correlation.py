"""Asset-Korrelation für die Gruppenwahl der gemeinsamen Regime-Erkennung.

Je Asset-Paar zwei Maße auf demselben Timeframe/Zeitraum wie das Regime-Lab:
  * ret_corr  – Pearson-Korrelation der Log-Renditen je Kerze
  * agree     – Anteil gemeinsamer Kerzen, in denen beide dieselbe Richtung
                (ab/seitwärts/auf, Detektor der Lab-Einstellung, 3 Regime) haben
  * score     – Mittel aus ret_corr und zufallsbereinigter Übereinstimmung
Zusätzlich dieselben Werte nur im Holdout (letzte 100-Training-% der Kerzen),
damit sichtbar ist, ob der Gleichlauf auch zuletzt noch hält.
Gruppen: Average-Linkage-Clustering auf score (Schwelle GROUP_MIN_SCORE).
Läuft als normaler Regime-Lab-Job (lab.JOBS: Balken, Abbruch, Pause, RAM-Queue).
Kerzen werden je Coin geladen und sofort verdichtet (RAM-schonend für die ganze
Watchlist). Rechenteil (`pair_stats`, `group_assets`, `copilot_block`) ist rein.
Jeder Lauf wird als eigener Eintrag gespeichert (Verlauf, löschbar); „latest“
= jüngster Eintrag (Copilot, Rückwärtskompatibilität mit dem alten Einzel-Dokument).
"""
import asyncio
import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

MIN_OVERLAP = 200
MIN_HOLDOUT = 100
GROUP_MIN_SCORE = 0.55
DOC_ID = "latest"          # Alt-Dokument (vor dem Verlauf) – bleibt lesbar
JOB_KIND = "correlation"
DETECTORS = ("reactive", "ema", "kombi", "jump")
# Kerzen-Laden je Asset: abgebrochen wird nur, wenn der Download STEHT (kein
# Fortschritt) – lange Erst-Downloads (z.B. 540 Tage Bitunix/Dukascopy) laufen
# durch und liegen danach im Kerzen-Cache. Vorher: harte 300 s je Asset ->
# Metalle/Indizes/Forex/HYPE landeten bei langen Zeiträumen immer in „ohne Daten“.
STALL_TIMEOUT_S = 300
MAX_FETCH_S = 3 * 3600
STALL_POLL_S = 5
PERFECT_ABS = 0.95         # |r| bzw. |Score| ab hier = (nahezu) perfekte Korrelation
MAX_RUNS = 40


def _chance_adjusted(agree: float, chance: float) -> float:
    return max(0.0, min(1.0, (agree - chance) / (1 - chance))) if chance < 1 else 0.0


def _corr(ra: Dict[int, float], rb: Dict[int, float], ts: List[int]) -> Optional[float]:
    x, y = np.array([ra[t] for t in ts]), np.array([rb[t] for t in ts])
    if x.std() > 0 and y.std() > 0:
        return float(np.corrcoef(x, y)[0, 1])
    return None


def _agree(da: Dict[int, int], db: Dict[int, int], ts: List[int]):
    va, vb = np.array([da[t] for t in ts]), np.array([db[t] for t in ts])
    ag = float(np.mean(va == vb))
    chance = float(sum(np.mean(va == k) * np.mean(vb == k) for k in (0, 1, 2)))
    return ag, _chance_adjusted(ag, chance)


def _r(v: Optional[float], nd: int = 3) -> Optional[float]:
    return None if v is None else round(v, nd)


def pair_stats(returns: Dict[str, Dict[int, float]], dirs: Dict[str, Dict[int, int]],
               holdout_start: Optional[Dict[str, int]] = None) -> List[Dict]:
    """Alle Paare mit Rendite-Korrelation, Richtungs-Übereinstimmung, Score und
    Holdout-Werten (ab dem späteren der beiden Holdout-Starts) – rein."""
    hs = holdout_start or {}
    syms = sorted(set(returns) | set(dirs))
    out = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            ra, rb = returns.get(a) or {}, returns.get(b) or {}
            da, db = dirs.get(a) or {}, dirs.get(b) or {}
            common = sorted(set(ra) & set(rb))
            cd = sorted(set(da) & set(db))
            rc = _corr(ra, rb, common) if len(common) >= MIN_OVERLAP else None
            ag = adj = None
            if len(cd) >= MIN_OVERLAP:
                ag, adj = _agree(da, db, cd)
            parts = [v for v in (rc, adj) if v is not None]
            if not parts:
                continue
            h0 = max(hs.get(a, 0), hs.get(b, 0)) if (a in hs and b in hs) else None
            rc_h = ag_h = None
            n_h = 0
            if h0 is not None:
                ch = [t for t in common if t >= h0]
                dh = [t for t in cd if t >= h0]
                n_h = max(len(ch), len(dh))
                if len(ch) >= MIN_HOLDOUT:
                    rc_h = _corr(ra, rb, ch)
                if len(dh) >= MIN_HOLDOUT:
                    ag_h = _agree(da, db, dh)[0]
            out.append({"a": a, "b": b, "ret_corr": _r(rc),
                        "agree_pct": None if ag is None else round(ag * 100, 1),
                        "score": round(sum(parts) / len(parts), 3), "n": max(len(common), len(cd)),
                        "ret_corr_holdout": _r(rc_h),
                        "agree_pct_holdout": None if ag_h is None else round(ag_h * 100, 1),
                        "n_holdout": n_h})
    out.sort(key=lambda p: p["score"], reverse=True)
    return out


def group_assets(symbols: List[str], pairs: List[Dict], min_score: float = GROUP_MIN_SCORE) -> List[Dict]:
    """Average-Linkage: Cluster verschmelzen, solange ihr Ø Paar-Score >= min_score (rein)."""
    sc = {frozenset((p["a"], p["b"])): p["score"] for p in pairs}
    clusters = [[s] for s in sorted(symbols)]

    def link(c1, c2):
        v = [sc.get(frozenset((a, b))) for a in c1 for b in c2]
        v = [x for x in v if x is not None]
        return sum(v) / len(v) if v and len(v) == len(c1) * len(c2) else None

    while True:
        best, bi, bj = None, -1, -1
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                s = link(clusters[i], clusters[j])
                if s is not None and s >= min_score and (best is None or s > best):
                    best, bi, bj = s, i, j
        if best is None:
            break
        clusters[bi] = clusters[bi] + clusters[bj]
        del clusters[bj]
    out = []
    for c in clusters:
        inner = [sc[frozenset((a, b))] for i, a in enumerate(c) for b in c[i + 1:] if frozenset((a, b)) in sc]
        out.append({"symbols": sorted(c), "avg_score": round(sum(inner) / len(inner), 3) if inner else None})
    out.sort(key=lambda g: (len(g["symbols"]), g["avg_score"] or 0), reverse=True)
    return out


def detector_config(engine: Optional[str], engine_config: Optional[Dict]) -> Dict:
    """Erkennungs-Konfiguration der Lab-Einstellung, immer 3 Regime (ab/seitwärts/auf)."""
    cfg = dict(engine_config or {}) if (engine or "v2") != "kmeans" else {}
    if cfg.get("detector") not in DETECTORS:
        cfg["detector"] = "reactive"
    cfg["regime_mode"] = 3
    return cfg


def _series(candles: List[Dict], timeframe: str, det_cfg: Optional[Dict] = None):
    from services import regime_engine as eng
    from services import regime_reactive as rx
    ts = [int(c["timestamp"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    rets = {ts[i]: math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl)) if cl[i] > 0 and cl[i - 1] > 0}
    cfg = eng.resolve_config(det_cfg or detector_config(None, None), timeframe, len(candles))
    ids, _c, _d = rx.classify(eng.compute_matrix(candles, cfg), cfg)
    dirs = {ts[i]: int(ids[i]) for i in range(len(ids)) if ids[i] >= 0}
    return rets, dirs


def _holdout_ts(candles: List[Dict], train_pct: float) -> int:
    idx = min(max(int(len(candles) * train_pct / 100.0), 0), len(candles) - 1)
    return int(candles[idx]["timestamp"])


async def _fetch_one(lab, sym: str, days: int, tf: str, job: Dict, skipped: Dict):
    """Kerzen eines Assets laden; TimeoutError nur bei Stillstand (kein neuer
    Lade-Fortschritt seit STALL_TIMEOUT_S) oder nach MAX_FETCH_S. Pause zählt nicht."""
    from services import job_control
    task = asyncio.ensure_future(lab.fetch_histories(
        [sym], days, tf, job=job, progress_span=(job.get("progress") or 0,) * 2, skipped=skipped))
    last, idle, total = None, 0.0, 0.0
    while True:
        done, _ = await asyncio.wait({task}, timeout=STALL_POLL_S)
        if done:
            return task.result()
        total += STALL_POLL_S
        mark = (job.get("phase"), job.get("progress"))
        idle = 0.0 if (mark != last or job_control.pause_requested(job)) else idle + STALL_POLL_S
        last = mark
        if idle >= STALL_TIMEOUT_S or total >= MAX_FETCH_S:
            task.cancel()
            raise asyncio.TimeoutError()


async def run(job_id: str, body: Dict, db) -> None:
    """Regime-Lab-Job: Watchlist laden, je Asset verdichten, Paare + Gruppen speichern."""
    from services import job_control
    from services import regime_lab as lab
    job = lab.JOBS[job_id]
    symbols = list(body.get("symbols") or [])
    tf, days = body.get("timeframe") or "1h", int(body.get("days") or 360)
    train_pct = float(body.get("train_pct") or 75)
    det_cfg = detector_config(body.get("engine"), body.get("engine_config"))
    returns, dirs, hstart, skipped, failed = {}, {}, {}, {}, {}
    try:
        for i, sym in enumerate(symbols):
            await job_control.wait_if_paused(job)
            if job.get("cancel"):
                raise lab.JobCancelled()
            job["progress"] = 2 + round(i / max(len(symbols), 1) * 90)
            job["phase"] = f"Korrelation: {sym} ({i + 1}/{len(symbols)})"
            try:
                hist = await _fetch_one(lab, sym, days, tf, job, skipped)
            except asyncio.TimeoutError:
                failed[sym] = "Datenquelle antwortet nicht (Download stand still)"
                continue
            except lab.JobCancelled:
                raise
            except Exception as e:  # noqa: BLE001 – ein Asset darf den Lauf nicht kippen
                failed[sym] = f"Laden fehlgeschlagen: {str(e)[:120]}"
                continue
            candles = hist.get(sym)
            if not candles:
                continue
            returns[sym], dirs[sym] = await asyncio.to_thread(_series, candles, tf, det_cfg)
            hstart[sym] = _holdout_ts(candles, train_pct)
            del hist, candles
        if len(returns) < 2:
            raise RuntimeError("Zu wenig Assets mit Daten für eine Korrelation (mind. 2)")
        job["phase"] = "Paare & Gruppen berechnen"
        job["progress"] = 94
        pairs = await asyncio.to_thread(pair_stats, returns, dirs, hstart)
        missing = {**{s: v.get("reason", "keine Daten") for s, v in skipped.items()}, **failed}
        missing.update({s: "keine Daten" for s in symbols if s not in returns and s not in missing})
        doc = {"_id": uuid.uuid4().hex[:12], "symbols": sorted(returns), "requested": symbols,
               "timeframe": tf, "days": days,
               "train_pct": train_pct, "detector": det_cfg["detector"],
               "pairs": pairs, "groups": group_assets(list(returns), pairs),
               "perfect": perfect_pairs(pairs),
               "missing": sorted(missing), "missing_reasons": missing,
               "created_at": datetime.now(timezone.utc).isoformat()}
        if db is not None:
            await db.regime_correlation.insert_one(doc)
            await _prune(db)
        job["result"] = {"kind": JOB_KIND, "id": doc["_id"], "symbols": len(returns), "pairs": len(pairs),
                         "groups": sum(1 for g in doc["groups"] if len(g["symbols"]) > 1)}
        job.update(status="done", progress=100, phase="Fertig")
    except lab.JobCancelled:
        job.update(status="cancelled", phase="Abgebrochen")
    except Exception as e:  # noqa: BLE001 – Fehler in der UI anzeigen statt Absturz
        logger.warning(f"asset_correlation {job_id}: {e}")
        job.update(status="error", error=str(e)[:300], phase="Fehler")


def perfect_pairs(pairs: List[Dict], thr: float = PERFECT_ABS) -> List[Dict]:
    """Paare mit (nahezu) perfekter Korrelation, +1 wie -1 (rein)."""
    out = []
    for p in pairs:
        vals = [v for v in (p.get("ret_corr"), p.get("score")) if v is not None]
        if any(abs(v) >= thr for v in vals):
            out.append({"a": p["a"], "b": p["b"], "ret_corr": p.get("ret_corr"), "score": p.get("score"),
                        "sign": -1 if (p.get("ret_corr") or p.get("score") or 0) < 0 else 1})
    return out


async def _prune(db) -> None:
    old = await db.regime_correlation.find({}, {"_id": 1}).sort("created_at", -1) \
        .skip(MAX_RUNS).to_list(500)
    if old:
        await db.regime_correlation.delete_many({"_id": {"$in": [d["_id"] for d in old]}})


def _public(doc: Dict) -> Dict:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    if "perfect" not in doc:
        doc["perfect"] = perfect_pairs(doc.get("pairs") or [])
    return doc


def job_state(jobs: Dict) -> Dict:
    """Letzter Korrelations-Job (kompatibel zur alten Form: running/progress/error)."""
    j = next((x for x in reversed(list(jobs.values())) if x.get("kind") == JOB_KIND), None)
    if not j:
        return {"running": False, "error": None, "progress": 0}
    return {"id": j["id"], "status": j["status"], "running": j["status"] == "running",
            "progress": j.get("progress", 0), "phase": j.get("phase"), "error": j.get("error")}


async def latest(db) -> Optional[Dict]:
    doc = await db.regime_correlation.find_one({}, sort=[("created_at", -1)])
    return _public(doc) if doc else None


async def list_runs(db) -> List[Dict]:
    """Gespeicherte Läufe kompakt (ohne Paare) – neueste zuerst."""
    rows = await db.regime_correlation.find(
        {}, {"_id": 1, "created_at": 1, "timeframe": 1, "days": 1, "detector": 1,
             "symbols": 1, "requested": 1, "missing": 1, "perfect": 1}
    ).sort("created_at", -1).to_list(MAX_RUNS)
    return [{"id": str(r["_id"]), "created_at": r.get("created_at"), "timeframe": r.get("timeframe"),
             "days": r.get("days"), "detector": r.get("detector"),
             "assets": len(r.get("requested") or r.get("symbols") or []),
             "with_data": len(r.get("symbols") or []), "missing": len(r.get("missing") or []),
             "perfect": len(r.get("perfect") or [])} for r in rows]


async def get_run(db, run_id: str) -> Optional[Dict]:
    doc = await db.regime_correlation.find_one({"_id": run_id})
    return _public(doc) if doc else None


async def delete_run(db, run_id: str) -> bool:
    return (await db.regime_correlation.delete_one({"_id": run_id})).deleted_count > 0


def _pair_txt(p: Dict) -> str:
    hold = ""
    if p.get("ret_corr_holdout") is not None or p.get("agree_pct_holdout") is not None:
        hold = f" (Holdout r={p.get('ret_corr_holdout')}, {p.get('agree_pct_holdout')}%, n={p.get('n_holdout')})"
    return f"{p['a']}/{p['b']} r={p['ret_corr']} gleiche Richtung {p['agree_pct']}% score={p['score']}{hold}"


def copilot_block(doc: Optional[Dict], settings: Optional[Dict] = None) -> str:
    """Kompakter Kontext für den Regime-Copilot (rein). Ohne Ergebnis: Hinweis
    auf das Werkzeug, damit der Copilot es empfiehlt statt anderswo hinzuschicken."""
    if not doc:
        return ("ASSET-KORRELATION: noch nicht berechnet. Werkzeug vorhanden: im Regime-Lab "
                "unter der Coin-Auswahl Knopf „Korrelation berechnen“ (Watchlist oder Auswahl, "
                "aktueller Timeframe/Zeitraum) → Matrix, Gruppen-Vorschläge, „Gruppe für "
                "Regime-Suche übernehmen“. Empfiehl genau diesen Knopf.")
    top = "; ".join(_pair_txt(p) for p in (doc.get("pairs") or [])[:12])
    groups = "; ".join(f"[{', '.join(g['symbols'])}] Ø{g['avg_score']}"
                       for g in (doc.get("groups") or []) if len(g["symbols"]) > 1)
    stale = ""
    st = settings or {}
    if st.get("timeframe") and (str(st.get("timeframe")) != str(doc.get("timeframe"))
                                or int(st.get("days") or 0) != int(doc.get("days") or 0)):
        stale = (f" ACHTUNG: berechnet auf {doc.get('timeframe')}/{doc.get('days')} Tage, aktuelle "
                 f"Lab-Einstellung {st.get('timeframe')}/{st.get('days')} Tage – ggf. neu berechnen.")
    miss = f" Ohne Daten: {', '.join(doc.get('missing') or [])}." if doc.get("missing") else ""
    return (f"ASSET-KORRELATION ({doc.get('timeframe')}, {doc.get('days')} Tage, Detektor "
            f"{doc.get('detector', 'reactive')}, {len(doc.get('symbols') or [])} Assets, Stand "
            f"{doc.get('created_at', '')[:16]}): Top-Paare: {top or '–'}. Gruppen-Vorschlag "
            f"(Score >= {GROUP_MIN_SCORE}): {groups or 'keine'}. Score = Mittel aus Rendite-Korrelation "
            "und zufallsbereinigter Richtungs-Übereinstimmung; Holdout = letzte (100-Training)% der "
            f"Kerzen.{miss}{stale}")
