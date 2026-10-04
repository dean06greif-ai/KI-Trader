"""AP13 (Zielbild §4): Unsicherheitskalibrierung heuristischer Regime-Scores.

Bestehende Confidence-Felder der Detektoren sind heuristische Scores – KEINE
kalibrierten Wahrscheinlichkeiten. Dieses Modul misst empirisch, wie oft die
kausale Live-Richtung bei gegebenem Score tatsächlich der finalen Richtung
entspricht (Binning), und benennt zu dünne Datenlagen ehrlich. Bei zu wenig
Punkten je Symbol wird über die Anlageklasse gepoolt (research_pooling-Idee).
Reine Funktionen, keine DB/IO.
"""
from typing import Dict, List, Optional, Tuple

from services.setup_asset_class import asset_class_of

BIN_EDGES = (0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0000001)
MIN_POINTS = 300  # darunter: Symbol allein nicht belastbar -> Pooling


def hit_points(live_labels: List, final_labels: List, live_conf: List,
               mode) -> List[Tuple[float, int]]:
    """(Score, Treffer)-Punkte je Kerze: Treffer = Live-RICHTUNG == Final-
    Richtung (split_id, keine Label-Substrings)."""
    if not live_conf:
        return []
    from services import regime_engine as eng
    m = eng.norm_mode(mode)
    pts = []
    for lv, fn, cf in zip(live_labels or [], final_labels or [], live_conf):
        if lv is None or fn is None or cf is None:
            continue
        a = eng.split_id(int(lv), m)[0]
        b = eng.split_id(int(fn), m)[0]
        pts.append((max(min(float(cf), 1.0), 0.0), int(a == b)))
    return pts


def bin_rows(points: List[Tuple[float, int]]) -> List[Dict]:
    """Punkte in Score-Bins zählen. Rohsummen (n/conf_sum/hits) bleiben
    erhalten, damit Bins über Symbole hinweg gemergt werden können."""
    rows = [{"lo": BIN_EDGES[i], "hi": BIN_EDGES[i + 1],
             "n": 0, "conf_sum": 0.0, "hits": 0}
            for i in range(len(BIN_EDGES) - 1)]
    for cf, hit in points or []:
        for r in rows:
            if r["lo"] <= cf < r["hi"]:
                r["n"] += 1
                r["conf_sum"] += cf
                r["hits"] += int(hit)
                break
    return [_derive(r) for r in rows]


def _derive(r: Dict) -> Dict:
    n = int(r.get("n") or 0)
    out = dict(r)
    out["avg_conf_pct"] = round(r["conf_sum"] / n * 100.0, 1) if n else None
    out["hit_pct"] = round(r["hits"] / n * 100.0, 1) if n else None
    out["gap_pp"] = (round(out["avg_conf_pct"] - out["hit_pct"], 1)
                     if n else None)
    return out


def merge_bin_rows(rows_list: List[List[Dict]]) -> List[Dict]:
    """Bins mehrerer Symbole zusammenführen (Pooling) – Rohsummen addieren,
    abgeleitete Felder neu berechnen."""
    merged = [{"lo": BIN_EDGES[i], "hi": BIN_EDGES[i + 1],
               "n": 0, "conf_sum": 0.0, "hits": 0}
              for i in range(len(BIN_EDGES) - 1)]
    for rows in rows_list or []:
        for r in rows or []:
            for m in merged:
                if abs(m["lo"] - float(r.get("lo", -1))) < 1e-9:
                    m["n"] += int(r.get("n") or 0)
                    m["conf_sum"] += float(r.get("conf_sum") or 0.0)
                    m["hits"] += int(r.get("hits") or 0)
                    break
    return [_derive(m) for m in merged]


def weighted_gap(rows: List[Dict]) -> Optional[float]:
    """Punkt-gewichteter mittlerer |Score − Trefferquote|-Abstand in pp."""
    tot = sum(int(r.get("n") or 0) for r in rows or [])
    if not tot:
        return None
    s = sum(abs(float(r["gap_pp"])) * int(r["n"]) for r in rows
            if r.get("gap_pp") is not None and r.get("n"))
    return round(s / tot, 1)


def symbol_bins(points: List[Tuple[float, int]]) -> Dict:
    """Kompakter Kalibrierungs-Eintrag EINES Symbols (für Analyse-Dokumente)."""
    rows = bin_rows(points)
    return {"bins": rows, "n": len(points or []), "gap_pp": weighted_gap(rows),
            "score_is_calibrated_probability": False}


def calibration_report(entries_by_symbol: Dict[str, Dict],
                       min_points: int = MIN_POINTS) -> Dict:
    """Bericht über alle Symbole: assetspezifisch nur bei Datenstärke
    (n >= min_points), sonst Pooling über die Anlageklasse. Zu dünne Pools
    werden ehrlich als `insufficient` benannt, nicht als kalibriert verkauft."""
    by_class: Dict[str, List[str]] = {}
    for sym in (entries_by_symbol or {}):
        by_class.setdefault(asset_class_of(sym), []).append(sym)
    pooled = {cls: merge_bin_rows([(entries_by_symbol.get(s) or {}).get("bins") or []
                                   for s in syms])
              for cls, syms in by_class.items()}
    per_symbol = {}
    for sym, entry in (entries_by_symbol or {}).items():
        n = int((entry or {}).get("n") or 0)
        cls = asset_class_of(sym)
        if n >= min_points:
            rows = (entry or {}).get("bins") or []
            basis = "symbol"
            n_used = n
        else:
            rows = pooled.get(cls) or []
            n_used = sum(int(r.get("n") or 0) for r in rows)
            basis = f"pooled:{cls}"
        verdict = "ok" if n_used >= min_points else "insufficient"
        per_symbol[sym] = {"basis": basis, "n": n_used, "own_n": n,
                           "gap_pp": weighted_gap(rows), "verdict": verdict,
                           "bins": rows}
    return {"score_is_calibrated_probability": False,
            "min_points": min_points, "per_symbol": per_symbol}
