"""Fairer Regime-Vergleich: alle Kandidaten eines Assets auf EXAKT demselben
Zeitraum und derselben Referenz-Zeitachse messen.

Warum: Die gespeicherten Holdout-Werte jeder Analyse stammen aus ihrem eigenen
Zeitraum (andere Tage, anderes Erstellungsdatum) und ihre Referenz-„Wahrheit“
wird auf dem eigenen Timeframe berechnet (4h-Wahrheit ≠ 1h-Wahrheit). Ein 4h-
Modell, dessen Holdout zufällig in einer klaren Trendphase lag, sah dadurch
besser aus als ein 1h-Modell aus einer Seitwärtsphase – unfair.

Regeln (rein, testbar):
  * Fenster = nach dem SPÄTESTEN Trainingsende aller Kandidaten (für jeden echtes
    Out-of-Sample, auch kein Leck über andere Coins des Kombi-Modells) bis heute,
    minus halbes Referenz-Fenster am Ende (zentrierte Referenz braucht Zukunft).
  * Gemeinsame Zeitachse = feinster Timeframe der Kandidaten; gröbere Labels
    gelten kausal erst ab Schluss ihrer Kerze (kein Vorteil durch Vorwissen).
  * Eine Referenz für alle: zentrierte Richtungs-Labels auf der gemeinsamen
    Achse, festes 7-Tage-Fenster (regime_reference v2).
  * Metrik: Richtungs-Macro-F1 gesamt + je Teilfenster (3 gleich lange Stücke)
    -> „in jedem Fenster besser“ vergleicht wirklich gleiche Zeitstücke.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DOC_ID = "regime_fair_compare"
N_WINDOWS = 3
MIN_WINDOW_DAYS = 21.0
MAX_WINDOW_DAYS = 180.0
FRESH_DAYS = 7.0
TF_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}


def tf_ms(tf: str) -> int:
    return TF_MS.get(str(tf or "1h"), 3_600_000)


def unseen_start_ts(doc: Dict) -> Optional[int]:
    """Spätestes Trainingsende über ALLE Coins der Analyse (rein)."""
    ends = [int(b.get("train_end_ts")) for b in (doc.get("bounds") or {}).values()
            if isinstance(b, dict) and b.get("train_end_ts")]
    return max(ends) if ends else None


def common_window(docs: List[Dict], now_ms: int, ref_window_days: float) -> Tuple[Optional[int], Optional[int], str]:
    """(start_ms, end_ms, Hinweis) des gemeinsamen OOS-Fensters (rein)."""
    starts = [unseen_start_ts(d) for d in docs]
    if not starts or any(s is None for s in starts):
        return None, None, "Kandidat ohne Trainings-Grenze (alte Analyse) – kein fairer Vergleich möglich"
    start = max(starts)
    end = int(now_ms - ref_window_days / 2 * 86_400_000)
    start = max(start, int(end - MAX_WINDOW_DAYS * 86_400_000))
    days = (end - start) / 86_400_000
    if days < MIN_WINDOW_DAYS:
        return None, None, (f"gemeinsames Out-of-Sample erst {max(days, 0):.0f} Tage "
                            f"(< {MIN_WINDOW_DAYS:g}) – neueste Analyse zu frisch")
    return start, end, f"{days:.0f} Tage gemeinsames Out-of-Sample"


def project_causal(src_ts: np.ndarray, src_tf_ms: int, src_vals: np.ndarray,
                   base_ts: np.ndarray, base_tf_ms: int) -> np.ndarray:
    """Werte einer (gröberen) Zeitachse kausal auf die Basis-Achse legen: an der
    Basis-Kerze gilt der Wert der letzten Quell-Kerze, die bis zum SCHLUSS der
    Basis-Kerze geschlossen war; davor -1 (rein)."""
    close_src = src_ts.astype(np.int64) + int(src_tf_ms)
    close_base = base_ts.astype(np.int64) + int(base_tf_ms)
    idx = np.searchsorted(close_src, close_base, side="right") - 1
    out = np.full(len(base_ts), -1, dtype=int)
    ok = idx >= 0
    out[ok] = src_vals[idx[ok]]
    return out


def macro_f1(pred: np.ndarray, truth: np.ndarray) -> Optional[float]:
    """Macro-F1 über die Richtungen 0/1/2, nur wo beide gültig sind (rein)."""
    m = (pred >= 0) & (truth >= 0)
    if m.sum() < 10:
        return None
    p, t = pred[m], truth[m]
    f1s = []
    for k in (0, 1, 2):
        tp = int(np.sum((p == k) & (t == k)))
        fp = int(np.sum((p == k) & (t != k)))
        fn = int(np.sum((p != k) & (t == k)))
        if tp + fp + fn == 0:
            continue
        f1s.append(2 * tp / max(2 * tp + fp + fn, 1))
    return round(float(np.mean(f1s)) * 100.0, 1) if f1s else None


def window_scores(pred: np.ndarray, truth: np.ndarray, mask: np.ndarray) -> Dict:
    """Gesamt- und Teilfenster-F1 auf den maskierten Basis-Kerzen (rein)."""
    idx = np.where(mask)[0]
    if len(idx) < N_WINDOWS * 10:
        return {"f1": None, "windows": [], "bars": int(len(idx))}
    parts = np.array_split(idx, N_WINDOWS)
    return {"f1": macro_f1(pred[idx], truth[idx]),
            "windows": [macro_f1(pred[p], truth[p]) for p in parts],
            "bars": int(len(idx))}


def _directions(labels: List, mode: int) -> np.ndarray:
    from services import regime_truth as rt
    return rt._trend_arr(labels, mode)


def evaluate_symbol(symbol: str, cands: List[Dict], candles_by_tf: Dict[str, List[Dict]],
                    start_ms: int, end_ms: int) -> List[Dict]:
    """CPU-Teil: alle Kandidaten auf derselben Basis-Achse bewerten (rein bis
    auf die Modell-Auswertung). cands: [{aid, name, scope, timeframe, model}]."""
    from services import regime as rg
    from services import regime_engine as eng
    from services import regime_reference as rref
    from services import regime_truth as rt
    base_tf = min({c["timeframe"] for c in cands}, key=tf_ms)
    base = candles_by_tf[base_tf]
    base_ts = np.array([int(c["timestamp"]) for c in base], dtype=np.int64)
    ref_cfg = rref.reference_cfg({"bars_per_day": rg.bars_per_day(base_tf)})
    truth = _directions(rt.centered_labels(base, ref_cfg, 3), 3)
    mask = (base_ts > start_ms) & (base_ts <= end_ms)
    out = []
    for c in cands:
        row = {k: c.get(k) for k in ("aid", "name", "scope", "timeframe", "regime_mode")}
        try:
            cs = candles_by_tf[c["timeframe"]]
            mode = eng.norm_mode((c["model"].get("config") or {}).get("regime_mode", c.get("regime_mode") or 9))
            labels = rg.classify_series(c["model"], cs, c["timeframe"])
            dirs = _directions(labels, mode)
            src_ts = np.array([int(x["timestamp"]) for x in cs], dtype=np.int64)
            pred = project_causal(src_ts, tf_ms(c["timeframe"]), dirs, base_ts, tf_ms(base_tf))
            row.update(window_scores(pred, truth, mask))
        except Exception as e:  # noqa: BLE001 – ein defekter Kandidat darf den Vergleich nicht kippen
            logger.warning(f"Fair-Vergleich {symbol} {c.get('aid')}: {e}")
            row.update({"f1": None, "windows": [], "bars": 0, "error": str(e)[:160]})
        out.append(row)
    row_base = {"base_timeframe": base_tf}
    return [{**r, **row_base} for r in out]


async def run(db, symbol: str, band: str) -> Dict:
    """Fairen Vergleich für Symbol × Band rechnen und speichern (DB-Schicht)."""
    from services import regime_engine as eng
    from services import regime_lab as lab
    from services import regime_reference as rref
    from services import regime_release
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    import aiohttp
    docs = await db.regime_analyses.find(
        {"settings.engine": "v2"}, {"_id": 0, "chart": 0, "chart_emas": 0}).sort(
        "created_at", -1).limit(60).to_list(60)
    cands, used_docs = [], {}
    for d in docs:
        if regime_release.band_of_timeframe(d.get("timeframe")) != band:
            continue
        for scope in ("combined", "per_coin"):
            if scope == "combined" and symbol not in (((d.get("combined") or {}).get("per_symbol")) or {}):
                continue
            model = lab.model_for(d, scope, symbol)
            if not model:
                continue
            cands.append({"aid": d["id"], "name": d.get("name"), "scope": scope,
                          "timeframe": str(d.get("timeframe") or "1h"),
                          "regime_mode": (d.get("settings") or {}).get("regime_mode"), "model": model})
            used_docs[d["id"]] = d
    if not cands:
        return await _save(db, symbol, band, {"error": "keine Kandidaten"})
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start, end, note = common_window(list(used_docs.values()), now_ms, rref.DEFAULT_WINDOW_DAYS)
    if start is None:
        return await _save(db, symbol, band, {"error": note, "candidates": len(cands)})
    warm = max(eng.required_history_days(c["model"].get("config") or {}, 30) for c in cands)
    days = int((now_ms - start) / 86_400_000) + int(warm) + 2
    async with aiohttp.ClientSession() as session:
        raw = await fetch_history(session, symbol, days)
    by_tf = {tf: aggregate_candles(raw, tf, drop_partial=True) for tf in {c["timeframe"] for c in cands}}
    del raw
    rows = await asyncio.to_thread(evaluate_symbol, symbol, cands, by_tf, start, end)
    return await _save(db, symbol, band, {"window": {"start_ts": start, "end_ts": end, "note": note},
                                          "rows": rows, "candidates": len(cands)})


async def _save(db, symbol: str, band: str, res: Dict) -> Dict:
    key = f"{symbol}|{band}"
    res = {**res, "symbol": symbol, "band": band, "computed_at": datetime.now(timezone.utc).isoformat()}
    await db.settings.update_one({"_id": DOC_ID}, {"$set": {f"results.{key}": res}}, upsert=True)
    return res


async def load(db) -> Dict[str, Dict]:
    doc = await db.settings.find_one({"_id": DOC_ID}, {"_id": 0}) if db is not None else None
    return dict((doc or {}).get("results") or {})


def is_fresh(res: Optional[Dict], now: Optional[datetime] = None) -> bool:
    if not res or res.get("error") or not res.get("rows"):
        return False
    try:
        at = datetime.fromisoformat(str(res.get("computed_at")))
    except ValueError:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - at).total_seconds() <= FRESH_DAYS * 86400
