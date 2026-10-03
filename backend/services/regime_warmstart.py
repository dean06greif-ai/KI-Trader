"""Warmstart für den Regime-Autopilot: bewährte Erkennungen zuerst testen.

Idee: Wer schon 8 h Autopilot für Krypto auf 1h gerechnet hat, soll auf 4h (oder
für eine andere Anlageklasse) nicht bei null anfangen. Die besten Feinwerte
früherer Autopilot-Läufe und gespeicherter/freigegebener Analysen werden als
ERSTE Kandidaten auf den AKTUELLEN Daten bewertet – mit exakt derselben
Bewertung (innere Validierung + Training, Holdout bleibt Test). Passt ein
Seed nicht, kostet er nur eine Runde; passt er, startet die Suche von dort.

Die Seeds werden serverseitig gesammelt (der lokale Worker hat keine DB) und
als `seed_configs` im Job-Body übergeben – ältere Worker ignorieren das Feld.
"""
from typing import Dict, List, Optional

MAX_SEEDS = 6
RUN_LIMIT = 60
ANALYSIS_LIMIT = 40


def _overlap(a: List[str], b: List[str]) -> float:
    sa, sb = set(a or []), set(b or [])
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def rank_seeds(candidates: List[Dict], timeframe: str, symbols: List[str],
               start_key: Optional[str] = None, max_n: int = MAX_SEEDS) -> List[Dict]:
    """Kandidaten {engine_config, score?, timeframe?, symbols?, source, released?}
    -> sortierte, deduplizierte Seeds (rein). Reihenfolge: gleiche Coins zuerst
    (anderer Timeframe = die Idee), dann freigegeben, dann Score."""
    from services.regime_autopilot import config_key
    seen = {start_key} if start_key else set()
    scored = []
    for c in candidates or []:
        cfg = c.get("engine_config")
        if not isinstance(cfg, dict) or not cfg:
            continue
        key = config_key(cfg)
        if key in seen:
            continue
        seen.add(key)
        same_tf = (c.get("timeframe") or "") == timeframe
        scored.append(((round(_overlap(c.get("symbols"), symbols), 2), bool(c.get("released")),
                        float(c.get("score") or 0), not same_tf), c))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [{"engine_config": c["engine_config"], "source": c.get("source") or "gespeichert"}
            for _k, c in scored[:max_n]]


def _run_candidate(doc: Dict) -> Optional[Dict]:
    res = doc.get("result") or {}
    cfg = res.get("best_engine_config") or (res.get("best") or {}).get("engine_config")
    if not cfg:
        return None
    tf = res.get("timeframe") or ""
    return {"engine_config": cfg, "score": (res.get("best") or {}).get("score"),
            "timeframe": tf, "symbols": res.get("symbols") or [],
            "source": f"Autopilot {str(doc.get('created_at') or '')[:10]}"
                      + (f" · {tf}" if tf else "")}


def _analysis_candidate(doc: Dict) -> Optional[Dict]:
    cfg = (doc.get("settings") or {}).get("engine_config")
    if not cfg:
        return None
    stage = (doc.get("release") or {}).get("stage")
    return {"engine_config": cfg, "timeframe": doc.get("timeframe"), "symbols": doc.get("symbols") or [],
            "released": stage in ("shadow", "active"), "score": 0,
            "source": f"Analyse „{doc.get('name') or doc.get('id')}“ · {doc.get('timeframe')}"
                      + (f" · {stage}" if stage in ("shadow", "active") else "")}


async def collect_seeds(db, timeframe: str, symbols: List[str],
                        start_cfg: Optional[Dict] = None) -> List[Dict]:
    """Seeds aus früheren Autopilot-Läufen + gespeicherten Analysen (fail-open)."""
    if db is None:
        return []
    from services.regime_autopilot import config_key
    cands: List[Dict] = []
    try:
        runs = await db.regime_lab_runs.find(
            {"result.kind": "autopilot"}, {"_id": 0, "created_at": 1, "result.best": 1,
                                           "result.best_engine_config": 1, "result.timeframe": 1,
                                           "result.symbols": 1}
        ).sort("created_at", -1).limit(RUN_LIMIT).to_list(RUN_LIMIT)
        cands += [c for c in map(_run_candidate, runs) if c]
        docs = await db.regime_analyses.find(
            {"settings.engine": "v2"}, {"_id": 0, "id": 1, "name": 1, "timeframe": 1, "symbols": 1,
                                        "release.stage": 1, "settings.engine_config": 1}
        ).sort("created_at", -1).limit(ANALYSIS_LIMIT).to_list(ANALYSIS_LIMIT)
        cands += [c for c in map(_analysis_candidate, docs) if c]
    except Exception:  # noqa: BLE001 – Warmstart ist optional
        return []
    return rank_seeds(cands, timeframe, symbols,
                      config_key(start_cfg) if start_cfg else None)
