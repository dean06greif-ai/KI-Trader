"""Kurze Feinsuche des Regime-Autopiloten: kurz und dicht um die BESTE
Analyse suchen statt stundenlang breit.

Hintergrund: Lange Suchen testen tausende Varianten. Der beste Trainings-Score
steigt dabei schon durch reinen Zufall (Auswahl-Effekt). Der Abschlusstest
(Holdout) fällt dann oft – das ist Überanpassung. Die Feinsuche testet nur
Nachbarn der besten bekannten Erkennung (±1 Schritt, 1-2 Parameter), mit
festem Zeit- und Plateau-Limit und ohne Grundgerüst-Wechsel.

Startpunkt (rein, `pick_start`), in dieser Reihenfolge:
  1. Referenz-Lauf (falls im UI gesetzt) – das macht der Router wie bisher.
  2. Gespeicherte Analyse mit der besten Erkennungs-Note („sehr gut“ > „gut“ …).
     Vorrang haben gleiche Coins und gleicher Timeframe, danach die neueste.
  3. Bester Autopilot-Lauf derselben Coins/Timeframe-Gruppe.
  4. Sonst die aktuelle Einstellung.
"""
from typing import Dict, List, Optional

from services import regime_quality

FINE_MAX_MINUTES = 15.0
FINE_MAX_MINUTES_CAP = 30.0
FINE_PLATEAU_ROUNDS = 60
FINE_MAX_ROUNDS = 400
ANALYSIS_LIMIT = 30


def _overlap(a: List[str], b: List[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def fine_settings(body: Dict) -> Dict:
    """Feinsuche-Grenzen (rein). Läuft auch auf älteren Workern kurz, weil
    nur Felder genutzt werden, die sie kennen. `fine_mode` (±1 Schritt)
    verstehen neue Worker zusätzlich."""
    mm = float(body.get("max_minutes") or 0)
    mr = int(body.get("max_rounds") or 0)
    pr = int(body.get("plateau_rounds") or 0)
    return {"max_minutes": min(mm, FINE_MAX_MINUTES_CAP) if mm > 0 else FINE_MAX_MINUTES,
            "max_rounds": min(mr, FINE_MAX_ROUNDS) if mr > 0 else FINE_MAX_ROUNDS,
            "plateau_rounds": min(pr, FINE_PLATEAU_ROUNDS) if pr > 0 else FINE_PLATEAU_ROUNDS,
            "search_detectors": False, "tf_chain": False, "warm_start": False,
            "fine_mode": True}


def pick_start(analyses: List[Dict], runs: List[Dict], symbols: List[str],
               timeframe: str) -> Optional[Dict]:
    """Beste Ausgangslage (rein): {engine_config, source, grade?, score?} oder None.
    analyses: [{id, name, timeframe, symbols, created_at, engine_config, grade}]
    runs:     [{id, created_at, timeframe, symbols, engine_config, score}]"""
    order = regime_quality.GRADE_ORDER
    best_a = None
    for a in analyses or []:
        if not a.get("engine_config") or a.get("grade") not in order:
            continue
        if _overlap(a.get("symbols"), symbols) <= 0:
            continue
        key = (order[a["grade"]], round(_overlap(a.get("symbols"), symbols), 2),
               (a.get("timeframe") or "") == timeframe, str(a.get("created_at") or ""))
        if best_a is None or key > best_a[0]:
            best_a = (key, a)
    if best_a and best_a[0][0] >= order["gut"]:
        a = best_a[1]
        return {"engine_config": dict(a["engine_config"]), "grade": a["grade"],
                "analysis_id": a.get("id"), "timeframe": a.get("timeframe"),
                "source": f"Analyse „{a.get('name') or a.get('id')}“ ({a['grade']}, {a.get('timeframe')})"}
    same = [r for r in runs or [] if r.get("engine_config")
            and sorted(r.get("symbols") or []) == sorted(symbols or [])
            and (r.get("timeframe") or "") == timeframe]
    if same:
        r = max(same, key=lambda x: float(x.get("score") or 0))
        return {"engine_config": dict(r["engine_config"]), "score": r.get("score"),
                "run_id": r.get("id"), "timeframe": r.get("timeframe"),
                "source": f"Autopilot {str(r.get('created_at') or '')[:10]} · Score {float(r.get('score') or 0):.1f}"}
    if best_a:  # nur „mittel“/„schwach“ vorhanden – trotzdem besser als nichts
        a = best_a[1]
        return {"engine_config": dict(a["engine_config"]), "grade": a["grade"],
                "analysis_id": a.get("id"), "timeframe": a.get("timeframe"),
                "source": f"Analyse „{a.get('name') or a.get('id')}“ ({a['grade']}, {a.get('timeframe')})"}
    return None


async def collect_start(db, symbols: List[str], timeframe: str) -> Optional[Dict]:
    """Kandidaten aus der DB laden und die beste Ausgangslage wählen (fail-open)."""
    if db is None:
        return None
    try:
        docs = await db.regime_analyses.find(
            {"settings.engine": "v2", "symbols": {"$in": list(symbols or [])}},
            {"_id": 0, "id": 1, "name": 1, "timeframe": 1, "symbols": 1, "created_at": 1,
             "settings.engine_config": 1, "combined.per_symbol": 1, "per_coin": 1}
        ).sort("created_at", -1).limit(ANALYSIS_LIMIT).to_list(ANALYSIS_LIMIT)
        analyses = [{"id": d.get("id"), "name": d.get("name"), "timeframe": d.get("timeframe"),
                     "symbols": d.get("symbols") or [], "created_at": d.get("created_at"),
                     "engine_config": (d.get("settings") or {}).get("engine_config"),
                     "grade": regime_quality.grade_for_classes(d)} for d in docs]
        rows = await db.regime_lab_runs.find(
            {"result.kind": "autopilot", "result.timeframe": timeframe},
            {"_id": 0, "id": 1, "created_at": 1, "result.best": 1, "result.best_engine_config": 1,
             "result.timeframe": 1, "result.symbols": 1}).sort("created_at", -1).limit(60).to_list(60)
        runs = [{"id": r.get("id"), "created_at": r.get("created_at"),
                 "timeframe": (r.get("result") or {}).get("timeframe"),
                 "symbols": (r.get("result") or {}).get("symbols") or [],
                 "score": ((r.get("result") or {}).get("best") or {}).get("score"),
                 "engine_config": (r.get("result") or {}).get("best_engine_config")
                 or ((r.get("result") or {}).get("best") or {}).get("engine_config")} for r in rows]
    except Exception:  # noqa: BLE001 – Startpunkt ist optional
        return None
    return pick_start(analyses, runs, symbols, timeframe)
