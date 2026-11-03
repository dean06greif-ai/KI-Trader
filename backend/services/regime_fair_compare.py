"""Fairer Regime-Vergleich: alle Kandidaten eines Assets auf EXAKT demselben
Zeitraum und derselben Referenz-Zeitachse messen.

Warum: Die gespeicherten Holdout-Werte jeder Analyse stammen aus ihrem eigenen
Zeitraum (andere Tage, anderes Erstellungsdatum) und ihre Referenz-„Wahrheit“
wird auf dem eigenen Timeframe berechnet (4h-Wahrheit ≠ 1h-Wahrheit). Ein 4h-
Modell, dessen Holdout zufällig in einer klaren Trendphase lag, sah dadurch
besser aus als ein 1h-Modell aus einer Seitwärtsphase – unfair.

Regeln (rein, testbar):
  * Fenster = nach dem SPÄTESTEN Trainingsende der Kandidaten (für jeden echtes
    Out-of-Sample, auch kein Leck über andere Coins des Kombi-Modells) bis heute,
    minus halbes Referenz-Fenster am Ende (zentrierte Referenz braucht Zukunft).
    Analysen mit weniger als MIN_WINDOW_DAYS ungesehenen Tagen (zu frisch) oder
    ohne Trainings-Grenze werden ausgeschlossen statt den Vergleich zu blockieren.
  * Gemeinsame Zeitachse = feinster Timeframe der Kandidaten; gröbere Labels
    gelten kausal erst ab Schluss ihrer Kerze (kein Vorteil durch Vorwissen).
  * Eine Referenz für alle: zentrierte Richtungs-Labels auf der gemeinsamen
    Achse, festes 7-Tage-Fenster (regime_reference v2).
  * Metrik: Richtungs-Macro-F1 gesamt + je Teilfenster (3 gleich lange Stücke)
    -> „in jedem Fenster besser“ vergleicht wirklich gleiche Zeitstücke.
  * Klassifiziert wird exakt wie live (structural_regime): Standard-Parameter.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

DOC_ID = "regime_fair_compare"
N_WINDOWS = 3
MIN_WINDOW_DAYS = 21.0
MAX_WINDOW_DAYS = 180.0
FRESH_DAYS = 7.0
RETRY_BLOCKED_H = 24.0
DAY_MS = 86_400_000
TF_MS = {"5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000,
         "2h": 7_200_000, "4h": 14_400_000, "6h": 21_600_000, "12h": 43_200_000, "1d": 86_400_000}

# Laufender Hintergrund-Job (manuell oder Hintergrund-Refresh) – nur 1 gleichzeitig
_job: Dict = {"running": False, "total": 0, "done": 0, "current": None, "results": {},
              "started_at": None, "finished_at": None}
_task: Optional[asyncio.Task] = None


def tf_ms(tf: str) -> int:
    from services.timeframes import tf_minutes
    tf = str(tf or "1h")
    if tf in TF_MS:
        return TF_MS[tf]
    mins = tf_minutes(tf)
    return mins * 60_000 if mins > 0 else 3_600_000


def cand_key(aid, scope) -> str:
    return f"{aid}|{scope}"


def unseen_start_ts(doc: Dict) -> Optional[int]:
    """Spätestes Trainingsende über ALLE Coins der Analyse (rein)."""
    ends = [int(b.get("train_end_ts")) for b in (doc.get("bounds") or {}).values()
            if isinstance(b, dict) and b.get("train_end_ts")]
    return max(ends) if ends else None


def common_window(docs: List[Dict], now_ms: int,
                  ref_window_days: float) -> Tuple[Optional[int], Optional[int], str, Dict[str, str]]:
    """(start_ms, end_ms, Hinweis, ausgeschlossen{aid: Grund}) des gemeinsamen
    OOS-Fensters (rein). Zu frische / grenzlose Analysen fallen raus."""
    end = int(now_ms - ref_window_days / 2 * DAY_MS)
    excluded: Dict[str, str] = {}
    starts: Dict[str, int] = {}
    for d in docs:
        s = unseen_start_ts(d)
        if s is None:
            excluded[d.get("id")] = "ohne Trainings-Grenze (alte Analyse)"
            continue
        days = (end - s) / DAY_MS
        if days < MIN_WINDOW_DAYS:
            excluded[d.get("id")] = (f"zu frisch: erst {max(days, 0):.0f} Tage Out-of-Sample "
                                     f"(< {MIN_WINDOW_DAYS:g})")
            continue
        starts[d.get("id")] = s
    if not starts:
        return None, None, (f"kein Kandidat mit ≥ {MIN_WINDOW_DAYS:g} Tagen gemeinsamem "
                            f"Out-of-Sample"), excluded
    start = max(max(starts.values()), int(end - MAX_WINDOW_DAYS * DAY_MS))
    return start, end, f"{(end - start) / DAY_MS:.0f} Tage gemeinsames Out-of-Sample", excluded


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
    """CPU-Teil: alle Kandidaten auf derselben Basis-Achse bewerten.
    cands: [{aid, name, scope, timeframe, regime_mode, model}]."""
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
        row["base_timeframe"] = base_tf
        out.append(row)
    return out


def candidates_of(docs: List[Dict], symbol: str, band: str) -> Tuple[List[Dict], List[str]]:
    """Kandidaten (mit Modell) eines Symbol × Band und ALLE geprüften Schlüssel
    (auch ohne Modell) – deckungsgleich mit regime_selection.candidate_rows (rein)."""
    from services import regime_lab as lab
    from services import regime_release
    cands, seen = [], []
    for d in docs:
        if regime_release.band_of_timeframe(d.get("timeframe")) != band:
            continue
        for scope in ("combined", "per_coin"):
            if scope == "combined" and symbol not in (((d.get("combined") or {}).get("per_symbol")) or {}):
                continue
            coin = (d.get("per_coin") or {}).get(symbol)
            if scope == "per_coin" and (not coin or coin.get("error")):
                continue
            seen.append(cand_key(d.get("id"), scope))
            model = lab.model_for(d, scope, symbol)
            if model:
                cands.append({"aid": d.get("id"), "name": d.get("name"), "scope": scope,
                              "timeframe": str(d.get("timeframe") or "1h"),
                              "regime_mode": (d.get("settings") or {}).get("regime_mode"), "model": model})
    return cands, seen


async def run(db, symbol: str, band: str) -> Dict:
    """Fairen Vergleich für Symbol × Band rechnen und speichern (DB-Schicht)."""
    from services import regime_engine as eng
    from services import regime_reference as rref
    from services.backtester import fetch_history
    from services.timeframes import aggregate_candles
    import aiohttp
    docs = await db.regime_analyses.find(
        {"settings.engine": "v2"}, {"_id": 0, "chart": 0, "chart_emas": 0}).sort(
        "created_at", -1).limit(60).to_list(60)
    cands, seen = candidates_of(docs, symbol, band)
    if not cands:
        return await _save(db, symbol, band, {"error": "keine Kandidaten", "seen": seen})
    by_id = {d.get("id"): d for d in docs}
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start, end, note, excluded = common_window(
        [by_id[a] for a in dict.fromkeys(c["aid"] for c in cands)], now_ms, rref.DEFAULT_WINDOW_DAYS)
    base = {"seen": seen, "excluded": excluded, "candidates": len(cands)}
    if start is None:
        return await _save(db, symbol, band, {**base, "error": note})
    cands = [c for c in cands if c["aid"] not in excluded]
    warm = max(eng.required_history_days(c["model"].get("config") or {}, 30) for c in cands)
    days = int((now_ms - start) / DAY_MS) + int(warm) + 2
    async with aiohttp.ClientSession() as session:
        raw = await fetch_history(session, symbol, days)
    by_tf = {tf: aggregate_candles(raw, tf, drop_partial=True) for tf in {c["timeframe"] for c in cands}}
    del raw
    rows = await asyncio.to_thread(evaluate_symbol, symbol, cands, by_tf, start, end)
    return await _save(db, symbol, band, {**base, "rows": rows,
                                          "window": {"start_ts": start, "end_ts": end, "note": note}})


async def _save(db, symbol: str, band: str, res: Dict) -> Dict:
    key = f"{symbol}|{band}"
    res = {**res, "symbol": symbol, "band": band, "computed_at": datetime.now(timezone.utc).isoformat()}
    await db.settings.update_one({"_id": DOC_ID}, {"$set": {f"results.{key}": res}}, upsert=True)
    return res


async def load(db) -> Dict[str, Dict]:
    doc = await db.settings.find_one({"_id": DOC_ID}, {"_id": 0}) if db is not None else None
    return dict((doc or {}).get("results") or {})


def _age_h(res: Optional[Dict], now: Optional[datetime] = None) -> Optional[float]:
    try:
        at = datetime.fromisoformat(str((res or {}).get("computed_at")))
    except ValueError:
        return None
    return ((now or datetime.now(timezone.utc)) - at).total_seconds() / 3600


def is_fresh(res: Optional[Dict], now: Optional[datetime] = None) -> bool:
    if not res or res.get("error") or not res.get("rows"):
        return False
    age = _age_h(res, now)
    return age is not None and age <= FRESH_DAYS * 24


def status_for(res: Optional[Dict], row_keys: Iterable[str], incumbent: Optional[Dict],
               now: Optional[datetime] = None) -> Dict:
    """Darf der faire Vergleich für die Champion-Entscheidung genutzt werden? (rein)
    Nur wenn frisch, ALLE aktuellen Kandidaten geprüft wurden (keine neue Analyse
    übersehen) und der Amtsinhaber fair messbar ist – sonst gespeicherte Werte
    (bisheriges Verhalten) und `reason` sagt warum."""
    info = {"used": False, "computed_at": (res or {}).get("computed_at"),
            "excluded": dict((res or {}).get("excluded") or {}), "window": (res or {}).get("window")}
    if not res:
        return {**info, "reason": "missing", "note": "noch nicht gerechnet"}
    if res.get("error"):
        return {**info, "reason": "blocked", "note": res["error"]}
    if not is_fresh(res, now):
        return {**info, "reason": "old", "note": f"älter als {FRESH_DAYS:g} Tage"}
    seen = set(res.get("seen") or [])
    if any(k not in seen for k in row_keys):
        return {**info, "reason": "uncovered", "note": "neue Analyse seit dem letzten fairen Vergleich"}
    measured = {cand_key(r.get("aid"), r.get("scope")) for r in res.get("rows") or [] if r.get("f1") is not None}
    if incumbent and cand_key(incumbent.get("aid"), incumbent.get("scope") or "combined") not in measured:
        return {**info, "reason": "blocked",
                "note": "aktuelle Erkennung im gemeinsamen Zeitraum nicht messbar – gespeicherte Werte gelten"}
    return {**info, "used": True, "reason": "ok", "note": (res.get("window") or {}).get("note"),
            "base_timeframe": (res.get("rows") or [{}])[0].get("base_timeframe")}


def needs_refresh(info: Optional[Dict], now: Optional[datetime] = None) -> bool:
    """Hintergrund-Refresh fällig? Blockierte Paare nur 1×/Tag erneut (rein)."""
    reason = (info or {}).get("reason") or "missing"
    if reason in ("missing", "old", "uncovered"):
        return True
    if reason == "blocked":
        age = _age_h(info, now)
        return age is None or age >= RETRY_BLOCKED_H
    return False


def job_status() -> Dict:
    return dict(_job)


async def run_many(db, keys: List[str]) -> Dict:
    """Mehrere Symbol|Band nacheinander rechnen (Job-Status für die UI)."""
    _job.update({"running": True, "total": len(keys), "done": 0, "current": None, "results": {},
                 "started_at": datetime.now(timezone.utc).isoformat(), "finished_at": None})
    try:
        for key in keys:
            sym, _, band = key.partition("|")
            _job["current"] = key
            try:
                res = await run(db, sym, band)
                _job["results"][key] = res.get("error") or res.get("window", {}).get("note") or "ok"
            except Exception as e:  # noqa: BLE001 – ein Paar darf den Rest nicht abbrechen
                logger.warning(f"Fair-Vergleich {key}: {e}")
                _job["results"][key] = f"Fehler: {str(e)[:120]}"
            _job["done"] += 1
    finally:
        _job.update({"running": False, "current": None,
                     "finished_at": datetime.now(timezone.utc).isoformat()})
    return job_status()


def start_job(db, keys: List[str]) -> bool:
    """Job im Hintergrund starten; False wenn schon einer läuft."""
    global _task
    if _job["running"] or not keys:
        return False
    _job["running"] = True
    _task = asyncio.create_task(run_many(db, keys))
    return True
