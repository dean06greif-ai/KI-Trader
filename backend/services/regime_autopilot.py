"""Regime-Autopilot: Endlos-Suche nach der besten Regime-Erkennung.

Analog zur Endlos-Suche im Strategie-Optimizer: der Autopilot verändert die
Detektor-Einstellungen (Grundgerüst) Runde für Runde, bewertet jede Variante
mit der bestehenden Live=Final-Kennzahl (services.regime_lab._symbol_payload)
und behält die beste. Auswahlbasis ist – wie bei Ablation/Kombi (AP07) – die
INNERE Validierung; der Holdout bleibt finaler Test und wird nur berichtet.

"Wirtschaftlich": zu kurze Live-Phasen (nicht handelbar) werden bestraft, so
dass nicht die flatterhafteste, sondern die handelbar-treffsicherste
Erkennung gewinnt. Läuft bis Zeitlimit / Ziel / Runden-Limit oder bis der
Nutzer sanft stoppt ("stop_explore": Bestes bleibt). Pause wird unterstützt.

Cloud und lokaler Worker nutzen exakt diesen Code (Worker-Paket >= 1.12.0).
"""
import asyncio
import copy
import logging
import random
import time
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import job_control
from services import regime as rg
from services import regime_engine as eng
from services import regime_lab as lab
from services import research_validation
from services.backtester import JobCancelled

logger = logging.getLogger(__name__)

DETECTORS = ("reactive", "ema", "kombi")
EPSILON = 0.2                 # Anteil rein zufälliger Varianten (Exploration)
DETECTOR_SWITCH_P = 0.15      # Wahrscheinlichkeit, das Grundgerüst zu wechseln
MIN_GAIN = 0.05               # Mindestverbesserung des Scores
MAX_HISTORY = 12
PHASE_PENALTY_MAX = 15.0      # Punkte Abzug bei viel zu kurzen Phasen

# Suchraum: (lo, hi, step) für Zahlen (int wenn alle int), Liste = Auswahl
COMMON_SPACE = {
    "confidence_min": (0.5, 0.8, 0.05),
    "min_phase_days": (0.0, 10.0, 0.5),
}
DETECTOR_SPACE = {
    "reactive": {
        "rev_atr_mult": (1.5, 6.0, 0.25), "persist_candles": (2, 6, 1),
        "side_leg_atr_mult": (1.0, 3.0, 0.1), "side_stall_days": (0.0, 10.0, 0.5),
        "mtf_confirm": [True, False], "mtf_mult": (1.5, 4.0, 0.25),
        "use_volume_confirm": [True, False], "volume_boost": (1.2, 2.5, 0.1),
        "use_ema_confirm": [True, False], "ema_slope_thr": (0.08, 0.4, 0.02),
        "ema_persist_days": (0.5, 3.0, 0.25), "ema_mid_days": (10.0, 34.0, 1.0),
    },
    "ema": {
        "ema_regime_days": (4.0, 30.0, 1.0), "ema_regime_thr": (0.06, 0.4, 0.02),
        "ema_regime_smooth_days": (0.5, 3.0, 0.25),
        "ema_regime_persist_days": (0.25, 3.0, 0.25),
    },
    "kombi": {
        "kombi_ema_days": (5.0, 30.0, 1.0), "kombi_thr": (0.06, 0.4, 0.02),
        "kombi_slope_days": (1.0, 10.0, 0.5), "kombi_persist_days": (0.25, 3.0, 0.25),
        "kombi_dominance_days": (1.0, 8.0, 0.5), "kombi_pivot_accel": [True, False],
    },
}


# ---------------- reine Hilfsfunktionen (testbar) ----------------
def detector_of(cfg: Dict) -> str:
    d = str((cfg or {}).get("detector") or "reactive").lower()
    return d if d in DETECTORS else "reactive"


def space_for(detector: str) -> Dict:
    return {**COMMON_SPACE, **DETECTOR_SPACE.get(detector, {})}


def _sample(spec, rng: random.Random):
    if isinstance(spec, list):
        return rng.choice(spec)
    lo, hi, step = spec
    n = int(round((hi - lo) / step))
    v = lo + rng.randint(0, n) * step
    if all(isinstance(x, int) for x in spec):
        return int(round(v))
    return round(float(v), 4)


def _current(cfg: Dict, key: str):
    return cfg.get(key, eng.DEFAULT_CONFIG.get(key))


def _neighbor(spec, cur, rng: random.Random):
    """Nachbarwert (lokale Suche): 1-3 Schritte vom aktuellen Wert entfernt."""
    if isinstance(spec, list):
        others = [x for x in spec if x != cur] or spec
        return rng.choice(others)
    lo, hi, step = spec
    try:
        base = float(cur)
    except (TypeError, ValueError):
        return _sample(spec, rng)
    v = base + rng.choice((-3, -2, -1, 1, 2, 3)) * step
    v = min(max(v, lo), hi)
    if all(isinstance(x, int) for x in spec):
        return int(round(v))
    return round(v, 4)


def mutate(best_cfg: Dict, rng: random.Random, search_detectors: bool,
           stale_rounds: int) -> Dict:
    """Neue Variante aus der besten Konfiguration ableiten. Je länger ohne
    Verbesserung, desto mehr Parameter ändern sich (breitere Suche)."""
    cfg = copy.deepcopy(best_cfg or {})
    det = detector_of(cfg)
    if search_detectors and rng.random() < DETECTOR_SWITCH_P:
        det = rng.choice([d for d in DETECTORS if d != det])
        cfg["detector"] = det
        space = space_for(det)
        for key in rng.sample(sorted(space), min(2, len(space))):
            cfg[key] = _sample(space[key], rng)
        return cfg
    cfg["detector"] = det
    space = space_for(det)
    keys = sorted(space)
    if rng.random() < EPSILON:
        for key in rng.sample(keys, min(3, len(keys))):
            cfg[key] = _sample(space[key], rng)
        return cfg
    k = min(1 + stale_rounds // 5, 3, len(keys))
    for key in rng.sample(keys, k):
        cfg[key] = _neighbor(space[key], _current(cfg, key), rng)
    return cfg


def config_diff(a: Dict, b: Dict) -> Dict:
    """Welche Schlüssel unterscheiden sich (für die Anzeige)?"""
    out = {}
    for key in sorted(set(a or {}) | set(b or {})):
        if (a or {}).get(key) != (b or {}).get(key):
            out[key] = (b or {}).get(key)
    return out


def score_metrics(m: Optional[Dict], target_min_days: float) -> Optional[float]:
    """Auswahl-Score: innere Validierung (AP07) minus Strafe für zu kurze
    Live-Phasen (nicht handelbar = nicht wirtschaftlich)."""
    if not m:
        return None
    inner = m.get("inner_direction_pct")
    train = m.get("train_direction_pct")
    if train is None:
        train = m.get("direction_pct")
    if inner is None and train is None:
        return None
    # Innere Validierung (AP07) UND gesamtes Trainingsfenster je zur Hälfte:
    # das innere Fenster allein ist bei kurzen Zeiträumen sehr klein (Zufall),
    # der Holdout bleibt in beiden Fällen unangetastet.
    base = train if inner is None else (inner if train is None else (inner + train) / 2.0)
    penalty = 0.0
    avg = m.get("avg_live_phase_days")
    if avg is not None and target_min_days > 0 and avg < target_min_days:
        penalty = (target_min_days - avg) / target_min_days * PHASE_PENALTY_MAX
    return round(float(base) - penalty, 3)


def _mean(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 1) if xs else None


def evaluate_config(cfg: Dict, histories: Dict, train_hist: Dict, bounds: Dict,
                    inner_anchor: Dict, timeframe: str, stop=None) -> Optional[Dict]:
    """Eine Detektor-Konfiguration bewerten (CPU-lastig, im Thread aufrufen).
    Nutzt exakt die Kennzahlen der gespeicherten Analysen (Live=Final)."""
    model = rg.detect_regimes(train_hist, timeframe, 5, 3.0, 5.0,
                              engine="v2", engine_config=cfg)
    if not model:
        return None
    conf_min = float(cfg.get("confidence_min") or 0.55)
    min_hold = float(cfg.get("min_hold_days") or 0)
    bpd = max(rg.bars_per_day(timeframe), 1e-9)
    agg = {"direction_pct": [], "holdout_direction_pct": [],
           "inner_direction_pct": [], "trend_hit_pct": [], "train_direction_pct": []}
    holdout_bars = switches = seg_bars = seg_n = 0
    for sym, candles in histories.items():
        if stop and stop():
            raise JobCancelled()
        _labels, entry = lab._symbol_payload(model, candles, timeframe, conf_min,
                                             min_hold, False, bounds.get(sym),
                                             inner_anchor.get(sym))
        la = entry.get("live_agreement") or {}
        for k in agg:
            if la.get(k) is not None:
                agg[k].append(float(la[k]))
        # Trefferquote NUR im Trainingsfenster (ohne Holdout) aus den Zählern
        bars, hbars = int(la.get("bars") or 0), int(la.get("holdout_bars") or 0)
        if la.get("direction_pct") is not None and bars > hbars:
            same = float(la["direction_pct"]) / 100.0 * bars
            hsame = float(la.get("holdout_direction_pct") or 0) / 100.0 * hbars
            agg["train_direction_pct"].append((same - hsame) / (bars - hbars) * 100.0)
        holdout_bars += int(la.get("holdout_bars") or 0)
        lsegs = entry.get("live_segments") or entry.get("segments") or []
        switches += max(len(lsegs) - 1, 0)
        seg_bars += sum(int(s.get("bars") or 0) for s in lsegs)
        seg_n += len(lsegs)
    return {"direction_pct": _mean(agg["direction_pct"]),
            "holdout_direction_pct": _mean(agg["holdout_direction_pct"]),
            "inner_direction_pct": _mean(agg["inner_direction_pct"]),
            "train_direction_pct": _mean(agg["train_direction_pct"]),
            "trend_hit_pct": _mean(agg["trend_hit_pct"]),
            "holdout_bars": holdout_bars, "switches_live": switches,
            "avg_live_phase_days": (round(seg_bars / seg_n / bpd, 2) if seg_n else None)}


def _public_best(entry: Dict, baseline: Dict) -> Dict:
    return {"engine_config": entry["engine_config"], "metrics": entry["metrics"],
            "score": entry["score"], "detector": detector_of(entry["engine_config"]),
            "baseline_score": baseline.get("score"),
            "changes": config_diff(baseline.get("engine_config") or {},
                                   entry["engine_config"])}


# ---------------- Job ----------------
async def run_autopilot(job_id: str, body: Dict, db):
    job = lab.JOBS[job_id]
    try:
        symbols = body.get("symbols") or []
        timeframe = body.get("timeframe") or "15m"
        days = int(min(max(int(body.get("days") or 360), 30), 5500))
        train_pct = float(min(max(float(body.get("train_pct") or 75), 50), 95))
        start_cfg = dict(body.get("engine_config") or {})
        max_minutes = min(max(float(body.get("max_minutes") or 0), 0.0), 1440.0)
        target_pct = min(max(float(body.get("target_pct") or 0), 0.0), 100.0)
        max_rounds = max(int(body.get("max_rounds") or 0), 0)
        search_detectors = bool(body.get("search_detectors", True))
        target_min_days = min(max(float(body.get("min_phase_days_target") or 3.0), 0.0), 30.0)
        rng = random.Random(body.get("seed") or time.time_ns())
        if detector_of(start_cfg) != str(start_cfg.get("detector") or "reactive").lower():
            # regression liefert keine Live=Final-Kennzahl -> reaktiv starten
            start_cfg["detector"] = "reactive"
        start_cfg["detector"] = detector_of(start_cfg)

        histories = await lab.fetch_histories(symbols, days, timeframe, job)
        if not histories:
            raise RuntimeError("Zu wenig Daten für diesen Timeframe/Zeitraum")
        bounds, inner_anchor, train_hist = {}, {}, {}
        for sym, candles in histories.items():
            cut = min(max(int(len(candles) * train_pct / 100.0), 100), len(candles))
            train_hist[sym] = candles[:cut]
            bounds[sym] = int(candles[cut - 1]["timestamp"]) if cut < len(candles) else None
            inner_anchor[sym] = research_validation.inner_anchor_ts(candles, cut)

        def stop():
            return bool(job.get("cancel"))

        async def ev(cfg):
            return await asyncio.to_thread(evaluate_config, cfg, histories, train_hist,
                                           bounds, inner_anchor, timeframe, stop)

        job["phase"] = "Autopilot · Ausgangslage bewerten..."
        job["progress"] = 5
        base_m = await ev(start_cfg)
        if base_m is None:
            raise RuntimeError("Ausgangs-Konfiguration liefert kein Modell")
        baseline = {"engine_config": start_cfg, "metrics": base_m,
                    "score": score_metrics(base_m, target_min_days)}
        best = dict(baseline)
        job["best"] = _public_best(best, baseline)
        history: List[Dict] = []
        tested = improved = stale = 0
        t0 = time.time()
        stop_reason = None

        def stopping():
            if job.get("stop_explore"):
                return "stopped_by_user"
            if max_minutes and time.time() - t0 > max_minutes * 60:
                return "time_limit"
            if target_pct and (best.get("score") or 0) >= target_pct:
                return "target_reached"
            if max_rounds and tested >= max_rounds:
                return "rounds_limit"
            return None

        def pct():
            if max_minutes:
                return min(10 + (time.time() - t0) / (max_minutes * 60) * 85, 95)
            if max_rounds:
                return min(10 + tested / max_rounds * 85, 95)
            return min(10 + tested * 0.5, 95)

        def phase_txt(extra=""):
            m = best["metrics"]
            return (f"Autopilot · {tested} Varianten · {improved} Verbesserungen · "
                    f"Best {best['score']:.1f} ({detector_of(best['engine_config'])} · "
                    f"innere Val. {m.get('inner_direction_pct')}% · "
                    f"Holdout {m.get('holdout_direction_pct')}% · "
                    f"Ø Phase {m.get('avg_live_phase_days')}d)" + extra)

        while True:
            stop_reason = stopping()
            if stop_reason:
                break
            await job_control.wait_if_paused(job)
            if stop():
                raise JobCancelled()
            cand = mutate(best["engine_config"], rng, search_detectors, stale)
            changes = config_diff(best["engine_config"], cand)
            desc = ", ".join(f"{k}={v}" for k, v in list(changes.items())[:3])
            job["phase"] = phase_txt(f" · teste {desc}")[:200]
            job["progress"] = round(pct())
            m = await ev(cand)
            tested += 1
            if m is None:
                stale += 1
                continue
            sc = score_metrics(m, target_min_days)
            if sc is None:
                stale += 1
                continue
            history.append({"engine_config": cand, "metrics": m, "score": sc,
                            "changes": changes, "detector": detector_of(cand),
                            "round": tested})
            history.sort(key=lambda x: -x["score"])
            del history[MAX_HISTORY:]
            if sc > (best.get("score") or -1e9) + MIN_GAIN:
                best = {"engine_config": cand, "metrics": m, "score": sc}
                improved += 1
                stale = 0
                job["best"] = _public_best(best, baseline)
            else:
                stale += 1

        attempt_no = None
        if db is not None:
            try:
                attempt_no = await research_validation.register_attempt(
                    db, f"autopilot:{timeframe}:{','.join(sorted(histories.keys()))}",
                    "autopilot")
            except Exception:  # noqa: BLE001
                attempt_no = None
        result = {"kind": "autopilot", "best": _public_best(best, baseline),
                  "best_engine_config": best["engine_config"],
                  "baseline": {"engine_config": start_cfg, "metrics": base_m,
                               "score": baseline["score"]},
                  "improved": improved > 0, "improvements": improved, "tested": tested,
                  "history": history, "stop_reason": stop_reason,
                  "elapsed_seconds": round(time.time() - t0, 1),
                  "selection_basis": ("inner_validation+train"
                                      if base_m.get("inner_direction_pct") is not None
                                      else "train_only"),
                  "holdout_role": "final_test",
                  "evidence": research_validation.evidence_verdict(
                      best["metrics"].get("holdout_bars")),
                  "attempt_no": attempt_no,
                  "settings": {"max_minutes": max_minutes, "target_pct": target_pct,
                               "max_rounds": max_rounds, "search_detectors": search_detectors,
                               "min_phase_days_target": target_min_days},
                  "symbols": list(histories.keys()), "timeframe": timeframe,
                  "days": days, "train_pct": train_pct,
                  "created_at": datetime.now(timezone.utc).isoformat()}
        if db is not None:
            try:
                await db.regime_lab_runs.replace_one(
                    {"id": job_id}, {"id": job_id, "result": result,
                                     "created_at": result["created_at"]}, upsert=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"autopilot persist failed: {e}")
        job["result"] = result
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
    except JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
    except Exception as e:  # noqa: BLE001
        logger.exception(f"regime autopilot {job_id} failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
        job["phase"] = "Fehler"
