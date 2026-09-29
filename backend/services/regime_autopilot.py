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
from services import regime_advice
from services import research_validation
from services.backtester import JobCancelled

logger = logging.getLogger(__name__)

DETECTORS = ("reactive", "ema", "kombi", "jump")
EPSILON = 0.2                 # Anteil rein zufälliger Varianten (Exploration)
DETECTOR_SWITCH_P = 0.15      # Wahrscheinlichkeit, das Grundgerüst zu wechseln
MIN_GAIN = 0.05               # Mindestverbesserung des Scores
MAX_HISTORY = 12
PHASE_PENALTY_MAX = 15.0      # Punkte Abzug bei viel zu kurzen/langen Phasen
# Referenz-Anteil am Score: Live=Final allein belohnt träge Detektoren (lange
# Phasen = Selbst-Übereinstimmung, Prüfbericht 23.09 Befund 2) -> die
# detektor-unabhängige Referenz zählt zur Hälfte mit, sobald vorhanden.
REFERENCE_WEIGHT = 0.5
PLATEAU_ROUNDS_DEFAULT = 300   # Plan 1.5b: so viele Runden ohne Verbesserung -> Stopp
RESTART_AFTER_STALE = 120      # Plan 1.5d: dann aus einer Top-10-Variante weitersuchen


def config_key(cfg: Dict) -> str:
    """Stabiler Schlüssel einer Konfiguration (Plan 1.5a Duplikat-Cache)."""
    import json
    return json.dumps(cfg or {}, sort_keys=True, default=str)
REFERENCE_WEIGHT_V2 = 0.75
UTILITY_WEIGHT = 0.5

# Suchraum: (lo, hi, step) für Zahlen (int wenn alle int), Liste = Auswahl
COMMON_SPACE = {
    "confidence_min": (0.5, 0.8, 0.05),
    "min_phase_days": (0.0, 10.0, 0.5),
    # Plan 2.1 Höherer-TF-Filter (für alle Detektoren)
    "htf_confirm": [True, False],
    "htf_days": (1.0, 10.0, 0.5),
    "htf_thr": (0.0, 1.5, 0.1),
    "htf_promote_thr": [0.0, 0.8, 1.2, 1.6, 2.0, 3.0],
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
        "ema_regime_days": (2.0, 30.0, 1.0), "ema_regime_thr": (0.06, 0.4, 0.02),
        "ema_regime_smooth_days": (0.5, 3.0, 0.25),
        "ema_regime_persist_days": (0.25, 3.0, 0.25),
    },
    "kombi": {
        # Plan 1.5c: kürzere Untergrenzen – Mini-Suche 24.09.: beste Varianten bei
        # kombi_ema_days 8 / dominance 5 (Richtungs-Phase 7,5 d statt 23 d)
        "kombi_ema_days": (3.0, 30.0, 1.0), "kombi_thr": (0.06, 0.4, 0.02),
        "kombi_slope_days": (0.5, 10.0, 0.5), "kombi_persist_days": (0.25, 3.0, 0.25),
        "kombi_dominance_days": (0.5, 8.0, 0.5), "kombi_pivot_accel": [True, False],
    },
    # Statistisches Jump-Modell (services/regime_jump.py)
    "jump": {
        "jump_fast_days": (0.5, 5.0, 0.25), "jump_slow_days": (2.0, 21.0, 1.0),
        "jump_center": (0.1, 1.5, 0.05), "jump_penalty_days": (0.0, 4.0, 0.25),
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


def _half_mix(inner: Optional[float], train: Optional[float]) -> Optional[float]:
    # Innere Validierung (AP07) UND gesamtes Trainingsfenster je zur Hälfte:
    # das innere Fenster allein ist bei kurzen Zeiträumen sehr klein (Zufall),
    # der Holdout bleibt in beiden Fällen unangetastet.
    if inner is None and train is None:
        return None
    return train if inner is None else (inner if train is None else (inner + train) / 2.0)


def phase_penalty(avg: Optional[float], target_min_days: float,
                  target_max_days: float = 0.0) -> float:
    """Strafe außerhalb des Sweet Spots [min, max] der Ø Live-Phase (rein).
    Zu kurz = nicht handelbar, zu lang = träge (verpasst Phasen, Lag)."""
    if avg is None:
        return 0.0
    if target_min_days > 0 and avg < target_min_days:
        return (target_min_days - avg) / target_min_days * PHASE_PENALTY_MAX
    if target_max_days > 0 and avg > target_max_days:
        return min((avg - target_max_days) / target_max_days, 1.0) * PHASE_PENALTY_MAX
    return 0.0


def score_metrics(m: Optional[Dict], target_min_days: float,
                  target_max_days: float = 0.0) -> Optional[float]:
    """Auswahl-Score: innere Validierung + Training (Live=Final, gemischt mit
    der detektor-unabhängigen Referenz, falls vorhanden) minus Strafe für
    Ø Live-Phasen außerhalb des Sweet Spots."""
    if not m:
        return None
    train = m.get("train_direction_pct")
    if train is None:
        train = m.get("direction_pct")
    base = _half_mix(m.get("inner_direction_pct"), train)
    if base is None:
        return None
    # Referenz v2 (klassen-balanciert, ohne Holdout) bevorzugt – dann zählt sie
    # stärker, denn Live=Final ist bei allen Detektoren ~93–98 % (kaum Trennkraft).
    # Macro-F1 (bestraft verpasste UND falsche Trends) vor balanciertem Treffer
    ref = _half_mix(m.get("inner_reference_f1_pct"), m.get("train_reference_f1_pct"))
    if ref is None:
        ref = _half_mix(m.get("inner_reference_bal_pct"), m.get("train_reference_bal_pct"))
    weight = REFERENCE_WEIGHT_V2
    if ref is None:
        ref_train = m.get("train_reference_pct")
        if ref_train is None:
            ref_train = m.get("reference_pct")
        ref = _half_mix(m.get("inner_reference_pct"), ref_train)
        weight = REFERENCE_WEIGHT
    if ref is not None:
        base = (1.0 - weight) * base + weight * ref
    # Plan 2.5: Regime-Nutzen (NUR Trainingsteil, kein Holdout-Leck): bestätigt
    # sich die Live-Richtung danach öfter als ein Münzwurf? ±0,5 Punkte je %-Punkt
    util = m.get("utility_train_sign_hit_pct")
    if util is not None:
        base += UTILITY_WEIGHT * max(min(float(util) - 50.0, 10.0), -10.0)
    phase = m.get("live_direction_phase_days") or m.get("avg_live_phase_days")
    penalty = phase_penalty(phase, target_min_days, target_max_days)
    return round(float(base) - penalty, 3)


def robustness_key(m: Optional[Dict], band: Optional[tuple] = None) -> tuple:
    """Robustheits-Rang bei praktisch gleichem Score (höher = robuster).
    NUR Trainings-Kennzahlen: der Holdout ist finaler Test und darf die Auswahl
    nicht beeinflussen (vorher entschied er Gleichstände -> Holdout-Leck bei
    tausenden Varianten). Reihenfolge: Referenz-F1 im Training, Nähe der
    Richtungs-Phase zum Sweet Spot, weniger Umschaltungen."""
    if not m:
        return (-1e9, -1e9, -1e9)
    train_ref = m.get("train_reference_f1_pct")
    if train_ref is None:
        train_ref = m.get("train_direction_pct")
    phase = m.get("live_direction_phase_days") or m.get("avg_live_phase_days")
    switches = m.get("switches_live")
    if phase is None:
        phase_term = -1.0 if not band else -1e6
    elif band:
        lo, hi = band
        phase_term = -(max(lo - phase, 0.0) + (max(phase - hi, 0.0) if hi else 0.0))
    else:
        phase_term = float(phase)
    return (float(train_ref) if train_ref is not None else -1.0, phase_term,
            -float(switches) if switches is not None else 0.0)


def _mean(xs: List[float]) -> Optional[float]:
    return round(sum(xs) / len(xs), 1) if xs else None


def fallback_start_config(cfg: Dict) -> Dict:
    """Ausgangslage auf die Standard-Feinwerte des Grundgerüsts reduzieren
    (rein): nur Detektor, Regime-Anzahl und Glättung 'auto' bleiben – alle
    Fenster/Horizonte werden wieder an die Datenmenge angepasst."""
    out = {"detector": detector_of(cfg), "auto_adapt": True, "adapt_profile": "auto"}
    if cfg.get("regime_mode") is not None:
        out["regime_mode"] = cfg["regime_mode"]
    if cfg.get("version"):
        out["version"] = cfg["version"]
    return out


def no_model_reason(histories: Dict[str, List[Dict]], train_hist: Dict[str, List[Dict]],
                    timeframe: str, cfg: Dict) -> str:
    """Erklärt, WARUM kein Regime-Modell entstand (rein & testbar): je Symbol
    Kerzen gesamt / Training / mindestens nötig (Warmup + 20) und Anteil
    bewegungsloser Kerzen (High == Low), dazu ein konkreter Tipp."""
    parts, tips = [], set()
    for sym, candles in histories.items():
        n, tr = len(candles), len(train_hist.get(sym) or [])
        try:
            need = int(eng.resolve_config(cfg, timeframe, tr)["warmup_bars"]) + 20
        except Exception:  # noqa: BLE001
            need = 60
        flat = sum(1 for c in (train_hist.get(sym) or [])
                   if float(c.get("high", 0)) <= float(c.get("low", 0)))
        s = f"{sym}: {n} Kerzen ({timeframe}), Training {tr}, nötig ≥ {max(need, 60)}"
        if flat:
            s += f", {flat} ohne Bewegung"
            tips.add("Kerzen-Cache dieses Symbols neu laden (Daten-Ordner des Workers)")
        if tr < max(need, 60):
            tips.add("kleineren Timeframe wählen (z.B. 4h) oder Zeitraum verlängern")
        parts.append(s)
    msg = "Ausgangs-Konfiguration liefert kein Modell – " + " · ".join(parts[:4])
    if tips:
        msg += ". Tipp: " + "; ".join(sorted(tips))
    return msg[:300]


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
    ragg = {"reference_pct": [], "inner_reference_pct": [], "holdout_reference_pct": [],
            "train_reference_pct": [], "reference_lag_days": [],
            "inner_reference_bal_pct": [], "train_reference_bal_pct": [],
            "inner_reference_f1_pct": [], "train_reference_f1_pct": [],
            "holdout_reference_f1_pct": [], "holdout_kappa_pct": [],
            "utility_separation_pct": [], "utility_sign_hit_pct": [],
            "utility_train_sign_hit_pct": [],
            "holdout_reference_bal_pct": [], "holdout_skill_pct": [],
            "live_direction_phase_days": []}
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
        _collect_reference(entry.get("reference") or {}, ragg)
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
            "avg_live_phase_days": (round(seg_bars / seg_n / bpd, 2) if seg_n else None),
            **{k: _mean(v) for k, v in ragg.items()}}


def _collect_reference(ref: Dict, ragg: Dict) -> None:
    """Referenz-Kennzahlen eines Symbols sammeln; Training = gesamt ohne Holdout."""
    for src, dst in (("direction_pct", "reference_pct"), ("inner_direction_pct", "inner_reference_pct"),
                     ("holdout_direction_pct", "holdout_reference_pct"),
                     ("mean_lag_days", "reference_lag_days"),
                     ("inner_balanced_pct", "inner_reference_bal_pct"),
                     ("inner_f1_pct", "inner_reference_f1_pct"),
                     ("train_f1_pct", "train_reference_f1_pct"),
                     ("holdout_f1_pct", "holdout_reference_f1_pct"),
                     ("holdout_kappa_pct", "holdout_kappa_pct"),
                     ("utility_separation_pct", "utility_separation_pct"),
                     ("utility_sign_hit_pct", "utility_sign_hit_pct"),
                     ("utility_train_sign_hit_pct", "utility_train_sign_hit_pct"),
                     ("train_balanced_pct", "train_reference_bal_pct"),
                     ("holdout_balanced_pct", "holdout_reference_bal_pct"),
                     ("holdout_skill_pct", "holdout_skill_pct"),
                     ("live_direction_phase_days", "live_direction_phase_days")):
        if ref.get(src) is not None:
            ragg[dst].append(float(ref[src]))
    bars, hbars = int(ref.get("bars") or 0), int(ref.get("holdout_bars") or 0)
    if ref.get("direction_pct") is not None and bars > hbars:
        same = float(ref["direction_pct"]) / 100.0 * bars
        hsame = float(ref.get("holdout_direction_pct") or 0) / 100.0 * hbars
        ragg["train_reference_pct"].append((same - hsame) / (bars - hbars) * 100.0)


def _public_best(entry: Dict, baseline: Dict) -> Dict:
    return {"engine_config": entry["engine_config"], "metrics": entry["metrics"],
            "score": entry["score"], "detector": detector_of(entry["engine_config"]),
            "baseline_score": baseline.get("score"),
            "changes": config_diff(baseline.get("engine_config") or {},
                                   entry["engine_config"])}


def holdout_metric(best_m: Optional[Dict], base_m: Optional[Dict]) -> str:
    """Vergleichs-Kennzahl des finalen Tests: Macro-F1 gegen die unabhängige
    Referenz v2, wenn beide Seiten sie haben – sonst (Alt-Läufe) Live=Final.
    Live=Final misst nur Selbst-Übereinstimmung und blockierte sonst genau die
    besseren (reaktionsschnelleren) Detektoren."""
    key = "holdout_reference_f1_pct"
    if (best_m or {}).get(key) is not None and (base_m or {}).get(key) is not None:
        return key
    return "holdout_direction_pct"


def holdout_regressed(best_m: Optional[Dict], base_m: Optional[Dict]) -> bool:
    """Ist der Holdout (finaler Test) der besten Variante SCHLECHTER als der der
    Ausgangslage? Der Autopilot wählt auf innerer Validierung + Training; eine
    Variante mit höherem Score aber schlechterem Holdout darf nicht automatisch
    die aktive Kalibrierung überschreiben (manuell weiterhin möglich)."""
    key = holdout_metric(best_m, base_m)
    try:
        b = (best_m or {}).get(key)
        a = (base_m or {}).get(key)
        if a is None or b is None:
            return False
        return float(b) + 1e-9 < float(a)
    except (TypeError, ValueError):
        return False


def adopt_recommended(result: Dict) -> bool:
    """Automatische Übernahme nur, wenn der Score verbessert wurde UND der
    Holdout nicht gefallen ist (nie ein schlechteres Ergebnis übernehmen)."""
    return bool(result.get("improved")) and not holdout_regressed(
        (result.get("best") or {}).get("metrics"), (result.get("baseline") or {}).get("metrics"))


# ---------------- Vollautomatik: Autopilot -> Regime-Analyse ----------------
def followup_analysis_body(result: Dict, params: Dict) -> Optional[Dict]:
    """Request-Body für die automatische „Regime suchen & speichern“-Analyse
    mit der besten Autopilot-Erkennung (rein). None, wenn nichts zu tun ist
    (keine Verbesserung / Holdout gefallen, Vollautomatik aus oder keine Konfiguration)."""
    params = params or {}
    if params.get("auto_chain") is False or not result.get("adopt_recommended", result.get("improved")):
        return None
    cfg = dict(result.get("best_engine_config") or (result.get("best") or {}).get("engine_config") or {})
    if not cfg:
        return None
    symbols = list(result.get("symbols") or params.get("symbols") or [])
    if not symbols:
        return None
    try:
        min_hold = float(cfg.get("min_phase_days") or 0)
    except (TypeError, ValueError):
        min_hold = 0.0
    min_hold = min(max(min_hold, 0.25), 60.0)
    try:
        conf = float(cfg.get("confidence_min") or 0.55) * 100.0
    except (TypeError, ValueError):
        conf = 55.0
    det = detector_of(cfg)
    stamp = datetime.now(timezone.utc).strftime("%d.%m. %H:%M")
    return {"symbols": symbols,
            "timeframe": result.get("timeframe") or params.get("timeframe") or "15m",
            "days": int(result.get("days") or params.get("days") or 360),
            "train_pct": float(result.get("train_pct") or params.get("train_pct") or 75),
            "scope": "both", "engine": "v2",
            "engine_config": {**cfg, "min_phase_days": min_hold},
            "confidence_min": min(max(conf, 50.0), 95.0), "min_hold_days": min_hold,
            "name": f"Autopilot · {det} · {stamp}",
            "execution": params.get("execution") or "cloud",
            "autopilot_job_id": result.get("job_id")}


async def schedule_followup(db, job_id: str, result: Dict, params: Dict) -> Optional[Dict]:
    """Nach einem verbesserten Autopilot-Lauf die Analyse mit der besten
    Erkennung automatisch in die Job-Warteschlange stellen (services/job_series):
    die Regime werden damit ohne Klick gesucht & gespeichert – auch nachts,
    ohne offenen Browser. Läuft für Cloud- und Worker-Ergebnisse."""
    if db is None:
        return None
    body = followup_analysis_body({**result, "job_id": job_id}, params)
    if not body:
        return None
    try:
        from services import job_series
        item = await job_series.add_item(
            db, "regime_analysis", body,
            label=f"Autopilot-Analyse ({detector_of(body['engine_config'])}, "
                  f"{', '.join(s.replace('USDT', '') for s in body['symbols'][:4])})")
        await db.regime_lab_runs.update_one(
            {"id": job_id}, {"$set": {"result.followup": {"series_item_id": item["id"],
                                                          "kind": "regime_analysis",
                                                          "name": body["name"]}}})
        logger.info(f"autopilot {job_id}: Folge-Analyse eingereiht ({item['id']})")
        return item
    except Exception as e:  # noqa: BLE001
        logger.warning(f"autopilot {job_id}: Folge-Analyse konnte nicht eingereiht werden: {e}")
        return None


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
        plateau_rounds = max(int(body.get("plateau_rounds", PLATEAU_ROUNDS_DEFAULT) or 0), 0)
        target_min_days = min(max(float(body.get("min_phase_days_target") or 3.0), 0.0), 30.0)
        # Sweet Spot nach oben (0 = aus, Rückwärtskompatibilität für alte Aufrufer)
        target_max_days = min(max(float(body.get("max_phase_days_target") or 0.0), 0.0), 120.0)
        if target_max_days and target_max_days < target_min_days:
            target_max_days = target_min_days
        band = (target_min_days, target_max_days) if target_max_days else None
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
            # Feinwerte der Ausgangslage passen nicht zur Datenmenge (z.B. OIL/QQQ:
            # Bitunix-Historie erst ab Listing -> wenige Tageskerzen). Statt
            # abzubrechen: mit den Standard-Feinwerten desselben Grundgerüsts starten.
            fallback = fallback_start_config(start_cfg)
            base_m = await ev(fallback) if fallback != start_cfg else None
            if base_m is None:
                raise RuntimeError(no_model_reason(histories, train_hist, timeframe, start_cfg))
            job["start_fallback"] = ("Ausgangs-Feinwerte liefern auf diesen Daten kein Modell – "
                                     "Start mit Standard-Feinwerten des Grundgerüsts")
            logger.warning(f"autopilot {job_id}: {job['start_fallback']}")
            start_cfg = fallback
        baseline = {"engine_config": start_cfg, "metrics": base_m,
                    "score": score_metrics(base_m, target_min_days, target_max_days)}
        best = dict(baseline)
        job["best"] = _public_best(best, baseline)
        history: List[Dict] = []
        tested = improved = stale = duplicates = restarts = 0
        seen = {config_key(start_cfg)}
        dup_run = 0
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
            if plateau_rounds and stale >= plateau_rounds:
                return "plateau"
            return None

        def pct():
            if max_minutes:
                return min(10 + (time.time() - t0) / (max_minutes * 60) * 85, 95)
            if max_rounds:
                return min(10 + tested / max_rounds * 85, 95)
            # Endlos-Suche: der Balken spiegelt den aktuellen Bestwert-Score
            # (0..100) wider – so zeigt er die tatsächliche Güte statt einer
            # künstlichen 95%-Deckelung. Bei 100 läuft die Suche weiter und
            # feilt nur noch an der Robustheit (Holdout / Ø Phase).
            s = best.get("score")
            return min(max(float(s), 0.0), 100.0) if s is not None else 10.0

        def phase_txt(extra=""):
            m = best["metrics"]
            maxed = (best.get("score") or 0) >= 100 - MIN_GAIN and not max_minutes and not max_rounds
            robust = " · Bestwert erreicht – suche robustere Varianten" if maxed else ""
            return (f"Autopilot · {tested} Varianten · {improved} Verbesserungen · "
                    f"Best {best['score']:.1f} ({detector_of(best['engine_config'])} · "
                    f"innere Val. {m.get('inner_direction_pct')}% · "
                    f"Holdout-F1 {m.get('holdout_reference_f1_pct')}% · "
                    f"Referenz-F1 Training {m.get('train_reference_f1_pct')}% · "
                    f"Ø Phase {m.get('avg_live_phase_days')}d)" + robust + extra)

        while True:
            stop_reason = stopping()
            if stop_reason:
                break
            await job_control.wait_if_paused(job)
            if stop():
                raise JobCancelled()
            parent = best["engine_config"]
            if stale and stale % RESTART_AFTER_STALE == 0 and len(history) > 3:
                parent = rng.choice(history[:10])["engine_config"]
                restarts += 1
            cand = mutate(parent, rng, search_detectors, stale)
            ck = config_key(cand)
            if ck in seen:
                duplicates += 1
                dup_run += 1
                stale += 1
                if dup_run >= 2000:
                    stop_reason = "space_exhausted"
                    break
                if duplicates % 50 == 0:
                    await asyncio.sleep(0)
                continue
            seen.add(ck)
            dup_run = 0
            changes = config_diff(best["engine_config"], cand)
            desc = ", ".join(f"{k}={v}" for k, v in list(changes.items())[:3])
            job["phase"] = phase_txt(f" · teste {desc}")[:200]
            job["progress"] = round(pct(), 1)
            m = await ev(cand)
            tested += 1
            if m is None:
                stale += 1
                continue
            sc = score_metrics(m, target_min_days, target_max_days)
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
            elif abs(sc - (best.get("score") if best.get("score") is not None else -1e9)) <= MIN_GAIN \
                    and robustness_key(m, band) > robustness_key(best.get("metrics"), band):
                # Score-Gleichstand (z.B. beide ~100): robustere Variante
                # übernehmen – so wird die Endlos-Suche bei 100 sinnvoll
                # (besserer Holdout / längere Phasen / weniger Umschaltungen).
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
                  "holdout_regressed": holdout_regressed(best["metrics"], base_m),
                  "holdout_metric": holdout_metric(best["metrics"], base_m),
                  "history": history, "stop_reason": stop_reason,
                  "duplicates_skipped": duplicates, "restarts": restarts,
                  "plateau_rounds": plateau_rounds,
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
                               "min_phase_days_target": target_min_days,
                               "max_phase_days_target": target_max_days},
                  "symbols": list(histories.keys()), "timeframe": timeframe,
                  "days": days, "train_pct": train_pct,
                  # Plausibilität (services/regime_advice.py): fehlende Referenz,
                  # gesättigter Score, Phase außerhalb des Sweet Spots, Eingaben
                  "warnings": regime_advice.result_warnings(
                      best["metrics"], target_min_days, target_max_days),
                  "settings_advice": regime_advice.settings_advice(
                      timeframe, days, len(histories), target_min_days, target_max_days),
                  "created_at": datetime.now(timezone.utc).isoformat()}
        if job.get("start_fallback"):
            result["start_fallback"] = job["start_fallback"]
        result["adopt_recommended"] = adopt_recommended(result)
        if db is not None:
            try:
                await db.regime_lab_runs.replace_one(
                    {"id": job_id}, {"id": job_id, "result": result,
                                     "created_at": result["created_at"]}, upsert=True)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"autopilot persist failed: {e}")
            followup = await schedule_followup(db, job_id, result, job.get("params") or body)
            if followup:
                result["followup"] = {"series_item_id": followup["id"],
                                      "kind": "regime_analysis", "name": followup.get("label")}
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
