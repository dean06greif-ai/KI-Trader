"""Partial Pooling der Regime-Erkennung: Gruppen-Struktur + je Coin nur ein
vorsichtig geschrumpfter Skalen-Faktor (Anleitung: REGIME_ANLEITUNG_1010.md).

Bisher gab es nur zwei Extreme: „kombiniert“ (ein Modell für alle Coins, robust,
aber blind für Eigenheiten) oder „je Coin“ (eigene Einstellung, überangepasst).
Partial Pooling liegt dazwischen:

  * Struktur gemeinsam: Detektor, EMA-Längen, Glättungs-Profil kommen 1:1 aus
    dem Gruppen-Modell (kombinierte Analyse mit >= 3 Coins).
  * Skala je Coin: EIN Faktor k auf die Schwellen des Detektors (z.B. Umkehr-
    Schwelle), gesucht NUR auf den Trainingsdaten des Coins (Holdout bleibt unberührt).
  * Shrinkage: k wird Richtung 1,0 (= Gruppe) gezogen – umso stärker, je weniger
    Phasen der Coin im Training hat: Gewicht w = Phasen / (Phasen + Prior),
    k_pooled = k_best ** w. Wenige Phasen -> fast Gruppen-Modell.

Ergebnis ist eine normale Analyse mit Umfang „je Coin“ (settings.pooling =
Bericht). Champion je Asset, fairer Vergleich, Freigabe und Werkbank nutzen sie
ohne Sonderweg; der Coin-Modell-Abschlag wird mit w gewichtet.
"""
import asyncio
import logging
import math
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

SCALE_KEYS = {"reactive": ("rev_atr_mult", "side_leg_atr_mult"),
              "ema": ("ema_regime_thr",),
              "kombi": ("kombi_thr",),
              "jump": ("jump_center", "jump_penalty_days")}
FACTORS = (0.7, 0.85, 1.0, 1.2, 1.4)
PRIOR_PHASES = 40.0          # bei 40 Trainings-Phasen zählt der Coin halb
PRIOR_CHOICES = (20.0, 40.0, 80.0)
MIN_GAIN_PP = 1.0            # weniger Vorsprung als 1 Pkt. = kein Beleg -> k = 1
MIN_SYMBOLS = 3
RECOMMENDED_DAYS = 540
SMALL_TFS = ("1m", "3m", "5m", "15m")
VERSION = 1


def detector_of(doc: Dict) -> str:
    cfg = (((doc.get("combined") or {}).get("model") or {}).get("config") or {})
    return str(cfg.get("detector") or ((doc.get("settings") or {}).get("engine_config") or {})
               .get("detector") or "reactive")


def source_check(doc: Optional[Dict]) -> Dict:
    """Taugt die Analyse als Gruppen-Modell? (rein) -> {ok, reasons, warnings}"""
    if not doc:
        return {"ok": False, "reasons": ["Analyse nicht gefunden"], "warnings": []}
    st = doc.get("settings") or {}
    reasons, warnings = [], []
    if st.get("engine") != "v2":
        reasons.append("Nur Engine v2 wird unterstützt")
    if st.get("pooling"):
        reasons.append("Ist selbst schon ein Pooling-Ergebnis – die zugrunde liegende Gruppen-Analyse wählen")
    if not ((doc.get("combined") or {}).get("model")):
        reasons.append("Kein Gruppen-Modell: Analyse mit Umfang „beide“ oder „kombiniert“ rechnen")
    n = len(doc.get("symbols") or [])
    if n < MIN_SYMBOLS:
        reasons.append(f"Nur {n} Coin(s) – eine Gruppe braucht mindestens {MIN_SYMBOLS}")
    det = detector_of(doc)
    if det not in SCALE_KEYS:
        reasons.append(f"Detektor „{det}“ hat keine Skalen-Schwelle – Umkehrpunkte, EMA, Kombi oder Jump nutzen")
    if float(st.get("train_pct") or 100) >= 100:
        reasons.append("Training 100 % = kein Holdout: ohne ungesehene Daten kann niemand prüfen, ob Pooling hilft")
    if int(doc.get("days") or 0) < RECOMMENDED_DAYS:
        warnings.append(f"Zeitraum {doc.get('days')} Tage – empfohlen ≥ {RECOMMENDED_DAYS} (genug Phasen je Coin)")
    if str(doc.get("timeframe")) in SMALL_TFS:
        warnings.append(f"Timeframe {doc.get('timeframe')} ist sehr verrauscht – 1h oder 4h sind robuster")
    return {"ok": not reasons, "reasons": reasons, "warnings": warnings}


def group_scales(doc: Dict) -> Dict[str, float]:
    cfg = ((doc.get("combined") or {}).get("model") or {}).get("config") or {}
    return {k: float(cfg[k]) for k in SCALE_KEYS.get(detector_of(doc), ()) if cfg.get(k) is not None}


def shrink_weight(n_phases: float, prior: float = PRIOR_PHASES) -> float:
    n = max(float(n_phases or 0), 0.0)
    return n / (n + max(float(prior), 1e-9))


def pooled_factor(k_best: float, weight: float) -> float:
    return round(math.exp(weight * math.log(max(float(k_best), 1e-9))), 3)


def choose_factor(scores: Dict[float, Optional[float]]) -> Tuple[float, float]:
    """Bester Faktor + Vorsprung ggü. Gruppe (k=1). Ohne klaren Vorsprung: 1,0."""
    base = scores.get(1.0)
    valid = {k: s for k, s in scores.items() if s is not None}
    if base is None or not valid:
        return 1.0, 0.0
    k = max(valid, key=lambda x: (valid[x], -abs(math.log(x))))
    gain = valid[k] - base
    return (k, round(gain, 2)) if gain >= MIN_GAIN_PP else (1.0, round(max(gain, 0.0), 2))


def scaled(group: Dict[str, float], k: float) -> Dict[str, float]:
    return {key: round(v * k, 4) for key, v in group.items()}


def base_config(doc: Dict) -> Dict:
    """Engine-Einstellung der Gruppe; das gewählte Glättungs-Profil wird fixiert
    (Struktur gemeinsam – sonst wählte jeder Coin sein eigenes Profil)."""
    st = doc.get("settings") or {}
    cfg = dict(st.get("engine_config") or {})
    applied = (((doc.get("combined") or {}).get("model") or {}).get("config") or {}).get("adapt_applied")
    if applied:
        cfg["adapt_profile"] = applied
    return cfg


def plan_coin(symbol: str, scores: Dict[float, Optional[float]], n_phases: int,
              group: Dict[str, float], prior: float) -> Dict:
    k_best, gain = choose_factor(scores)
    w = shrink_weight(n_phases, prior)
    k_pool = pooled_factor(k_best, w)
    return {"symbol": symbol, "n_phases": int(n_phases), "weight": round(w, 3),
            "k_best": k_best, "gain_pp": gain, "k_pooled": k_pool,
            "scores": {f"{k:g}": (None if s is None else round(s, 2)) for k, s in scores.items()},
            "overrides": scaled(group, k_pool), "verdict": verdict(k_best, k_pool, w)}


def verdict(k_best: float, k_pool: float, w: float) -> str:
    if k_best == 1.0:
        return "Gruppe passt – kein Beleg für eine eigene Skala"
    side = "empfindlicher (niedrigere Schwelle)" if k_best < 1 else "träger (höhere Schwelle)"
    if w < 0.34:
        return f"Coin will {side}, aber wenig Phasen – nur leicht angepasst"
    return f"Coin {side} – Anpassung {abs(k_pool - 1) * 100:.0f} %"


def report(source: Dict, coins: Dict[str, Dict], prior: float) -> Dict:
    return {"version": VERSION, "source_id": source.get("id"), "source_name": source.get("name"),
            "detector": detector_of(source), "prior_phases": prior, "factors": list(FACTORS),
            "scale_keys": list(SCALE_KEYS.get(detector_of(source), ())),
            "group_scales": group_scales(source), "coins": coins}


def evaluate_factor(candles_train: List[Dict], timeframe: str, cfg: Dict, conf_min: float,
                    min_hold_days: float, inner_ts) -> Tuple[Optional[float], int]:
    """Score eines Faktors NUR auf dem Training: Mittel aus balancierter
    Richtungs-Treffer (gesamt) und Macro-F1 der inneren Validierung."""
    from services import regime as rg
    from services import regime_lab as lab
    model = rg.detect_regimes({"_": candles_train}, timeframe, engine="v2", engine_config=cfg)
    if not model:
        return None, 0
    _, entry = lab._symbol_payload(model, candles_train, timeframe, conf_min, min_hold_days,
                                   False, None, inner_ts)
    ref = entry.get("reference") or {}
    vals = [float(v) for v in (ref.get("balanced_direction_pct"), ref.get("inner_f1_pct")) if v is not None]
    return (sum(vals) / len(vals) if vals else None), int(ref.get("switches_truth") or 0) + 1


def analysis_body(doc: Dict, rep: Dict) -> Dict:
    st = doc.get("settings") or {}
    return {"symbols": list(rep["coins"].keys()), "timeframe": doc.get("timeframe"),
            "days": doc.get("days"), "scope": "per_coin", "engine": "v2",
            "engine_config": base_config(doc), "train_pct": st.get("train_pct"),
            "max_regimes": st.get("max_regimes"), "lookback_days": st.get("lookback_days"),
            "min_share_pct": st.get("min_share_pct"), "confidence_min": st.get("confidence_min"),
            "min_hold_days": st.get("min_hold_days"),
            "name": f"{doc.get('name') or doc.get('id')} · Pooling",
            "per_symbol_config": {s: c["overrides"] for s, c in rep["coins"].items()},
            "pooling": rep}


async def run_pooling(job_id: str, body: Dict, db):
    """Job: Skalen je Coin suchen (Training), schrumpfen, Pooling-Analyse speichern."""
    from services import regime_lab as lab
    from services import research_validation
    job = lab.JOBS[job_id]
    try:
        doc = await db.regime_analyses.find_one({"id": body.get("analysis_id")}, lab.NO_CHART)
        chk = source_check(doc)
        if not chk["ok"]:
            raise RuntimeError("; ".join(chk["reasons"]))
        prior = float(body.get("prior_phases") or PRIOR_PHASES)
        st = doc.get("settings") or {}
        tf, group, cfg0 = doc.get("timeframe"), group_scales(doc), base_config(doc)
        conf_min = float(st.get("confidence_min") or 70) / 100.0
        hold = float(st.get("min_hold_days") or 2)
        histories = await lab.fetch_histories(doc["symbols"], int(doc.get("days") or 180), tf, job)
        coins: Dict[str, Dict] = {}
        for i, (sym, candles) in enumerate(histories.items()):
            cut = min(max(int(len(candles) * float(st.get("train_pct") or 100) / 100.0), 100), len(candles))
            inner_ts = research_validation.inner_anchor_ts(candles, cut)
            scores, n_ph = {}, 0
            for j, k in enumerate(FACTORS):
                if job.get("cancel"):
                    raise lab.JobCancelled()
                job["phase"] = f"Skala je Coin suchen (nur Training): {sym} · Faktor {k:g}"
                job["progress"] = 5 + round((i * len(FACTORS) + j) / max(len(histories) * len(FACTORS), 1) * 50)
                s, n = await asyncio.to_thread(evaluate_factor, candles[:cut], tf, {**cfg0, **scaled(group, k)},
                                               conf_min, hold, inner_ts)
                scores[k], n_ph = s, max(n_ph, n)
            coins[sym] = plan_coin(sym, scores, n_ph, group, prior)
        if not coins:
            raise RuntimeError("Keine Kursdaten für die Coins der Gruppen-Analyse")
        rep = report(doc, coins, prior)
        await lab.run_analysis(job_id, analysis_body(doc, rep), db)
        if job.get("status") == "done" and isinstance(job.get("result"), dict):
            job["result"]["pooling"] = rep
    except lab.JobCancelled:
        job["status"], job["phase"] = "cancelled", "Abgebrochen"
    except Exception as e:  # noqa: BLE001 – Job-Fehler sauber melden
        logger.exception(f"regime pooling {job_id} failed")
        job.update(status="error", error=str(e)[:300], phase="Fehler")


def summary_row(doc: Dict) -> Dict:
    st = doc.get("settings") or {}
    pool = st.get("pooling") or {}
    return {"id": doc.get("id"), "name": doc.get("name"), "timeframe": doc.get("timeframe"),
            "days": doc.get("days"), "symbols": doc.get("symbols") or [],
            "train_pct": st.get("train_pct"), "detector": detector_of(doc),
            "created_at": doc.get("created_at"), "check": source_check(doc),
            "pooling": pool or None}


async def overview(db) -> Dict:
    docs = await db.regime_analyses.find(
        {"settings.engine": "v2"},
        {"_id": 0, "id": 1, "name": 1, "timeframe": 1, "days": 1, "symbols": 1, "created_at": 1,
         "settings": 1, "combined.model.config": 1}).sort("created_at", -1).limit(40).to_list(40)
    rows = [summary_row(d) for d in docs]
    return {"sources": [r for r in rows if not r["pooling"]],
            "pooled": [r for r in rows if r["pooling"]],
            "rules": {"factors": list(FACTORS), "prior_phases": PRIOR_PHASES,
                      "prior_choices": list(PRIOR_CHOICES), "min_gain_pp": MIN_GAIN_PP,
                      "min_symbols": MIN_SYMBOLS, "recommended_days": RECOMMENDED_DAYS,
                      "scale_keys": {k: list(v) for k, v in SCALE_KEYS.items()}}}
