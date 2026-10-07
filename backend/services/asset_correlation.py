"""Asset-Korrelation für die Gruppenwahl der gemeinsamen Regime-Erkennung.

Je Asset-Paar zwei Maße auf demselben Timeframe/Zeitraum wie das Regime-Lab:
  * ret_corr  – Pearson-Korrelation der Log-Renditen je Kerze
  * agree     – Anteil gemeinsamer Kerzen, in denen beide dieselbe Richtung
                (auf/seitwärts/ab, reaktiver Detektor, 3 Regime) haben
  * score     – Mittel aus ret_corr und zufallsbereinigter Übereinstimmung
Gruppen: Average-Linkage-Clustering auf score (Schwelle GROUP_MIN_SCORE).
Rechenteil (`pair_stats`, `group_assets`) ist rein & testbar.
"""
import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

MIN_OVERLAP = 200
GROUP_MIN_SCORE = 0.55
DOC_ID = "latest"
FETCH_TIMEOUT_S = 1200     # hängende Datenquelle (z.B. Rohstoffe) blockiert nicht ewig
JOB: Dict = {"running": False, "error": None, "progress": 0}


def _chance_adjusted(agree: float, chance: float) -> float:
    return max(0.0, min(1.0, (agree - chance) / (1 - chance))) if chance < 1 else 0.0


def pair_stats(returns: Dict[str, Dict[int, float]], dirs: Dict[str, Dict[int, int]]) -> List[Dict]:
    """Alle Paare mit Rendite-Korrelation, Richtungs-Übereinstimmung und Score (rein)."""
    syms = sorted(set(returns) | set(dirs))
    out = []
    for i, a in enumerate(syms):
        for b in syms[i + 1:]:
            ra, rb = returns.get(a) or {}, returns.get(b) or {}
            common = sorted(set(ra) & set(rb))
            rc = None
            if len(common) >= MIN_OVERLAP:
                x, y = np.array([ra[t] for t in common]), np.array([rb[t] for t in common])
                if x.std() > 0 and y.std() > 0:
                    rc = float(np.corrcoef(x, y)[0, 1])
            da, db = dirs.get(a) or {}, dirs.get(b) or {}
            cd = sorted(set(da) & set(db))
            ag = adj = None
            if len(cd) >= MIN_OVERLAP:
                va, vb = np.array([da[t] for t in cd]), np.array([db[t] for t in cd])
                ag = float(np.mean(va == vb))
                chance = float(sum(np.mean(va == k) * np.mean(vb == k) for k in (0, 1, 2)))
                adj = _chance_adjusted(ag, chance)
            parts = [v for v in (rc, adj) if v is not None]
            if not parts:
                continue
            out.append({"a": a, "b": b, "ret_corr": None if rc is None else round(rc, 3),
                        "agree_pct": None if ag is None else round(ag * 100, 1),
                        "score": round(sum(parts) / len(parts), 3), "n": max(len(common), len(cd))})
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


def _series(candles: List[Dict], timeframe: str):
    from services import regime_engine as eng
    from services import regime_reactive as rx
    ts = [int(c["timestamp"]) for c in candles]
    cl = [float(c["close"]) for c in candles]
    rets = {ts[i]: math.log(cl[i] / cl[i - 1]) for i in range(1, len(cl)) if cl[i] > 0 and cl[i - 1] > 0}
    cfg = eng.resolve_config({"regime_mode": 3, "detector": "reactive"}, timeframe, len(candles))
    ids, _c, _d = rx.classify(eng.compute_matrix(candles, cfg), cfg)
    dirs = {ts[i]: int(ids[i]) for i in range(len(ids)) if ids[i] >= 0}
    return rets, dirs


async def run(db, symbols: List[str], timeframe: str, days: int) -> None:
    from services import regime_lab as lab
    JOB.update(running=True, error=None, progress=5)
    try:
        hist = await asyncio.wait_for(
            lab.fetch_histories(symbols, int(days), timeframe, job=JOB, progress_span=(5, 60)), FETCH_TIMEOUT_S)
        returns, dirs = {}, {}
        for k, (s, c) in enumerate(hist.items()):
            returns[s], dirs[s] = await asyncio.to_thread(_series, c, timeframe)
            JOB["progress"] = 60 + int((k + 1) / max(len(hist), 1) * 35)
        pairs = await asyncio.to_thread(pair_stats, returns, dirs)
        doc = {"_id": DOC_ID, "symbols": sorted(hist), "timeframe": timeframe, "days": int(days),
               "pairs": pairs, "groups": group_assets(list(hist), pairs),
               "missing": sorted(set(symbols) - set(hist)),
               "created_at": datetime.now(timezone.utc).isoformat()}
        await db.regime_correlation.replace_one({"_id": DOC_ID}, doc, upsert=True)
    except Exception as e:  # noqa: BLE001 – Fehler in der UI anzeigen statt Absturz
        logger.warning(f"asset_correlation: {e}")
        JOB["error"] = "Zeitlimit beim Laden der Kerzen" if isinstance(e, asyncio.TimeoutError) else str(e)
    finally:
        JOB.update(running=False, progress=100)


async def latest(db) -> Optional[Dict]:
    doc = await db.regime_correlation.find_one({"_id": DOC_ID})
    if doc:
        doc.pop("_id", None)
    return doc


def copilot_block(doc: Optional[Dict]) -> str:
    """Kompakter Kontext für den Regime-Copilot (rein)."""
    if not doc:
        return ""
    top = "; ".join(f"{p['a']}/{p['b']} r={p['ret_corr']} gleiche Richtung {p['agree_pct']}% score={p['score']}"
                    for p in (doc.get("pairs") or [])[:12])
    groups = "; ".join(f"[{', '.join(g['symbols'])}] Ø{g['avg_score']}"
                       for g in (doc.get("groups") or []) if len(g["symbols"]) > 1)
    return (f"ASSET-KORRELATION ({doc.get('timeframe')}, {doc.get('days')} Tage, Stand {doc.get('created_at', '')[:16]}): "
            f"Top-Paare: {top or '–'}. Gruppen-Vorschlag (Score >= {GROUP_MIN_SCORE}): {groups or 'keine'}. "
            "Score = Mittel aus Rendite-Korrelation und zufallsbereinigter Richtungs-Übereinstimmung.")
