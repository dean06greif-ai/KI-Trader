"""Referenz-Qualität der Regime-Erkennung (rein, ohne IO).

Problem, das dieses Modul löst: Die Kennzahl „Live=Final“ misst nur die
SELBST-Übereinstimmung eines Detektors (kausale Sicht vs. dessen eigene
Rückschau). Beim Detektor 'ema' unterscheiden sich beide nur um wenige
Kerzen -> 98 % Live=Final, obwohl der Detektor ganze Trends verpasst.
Die Kennzahl ist deshalb zwischen Detektoren NICHT vergleichbar.

Hier wird die kausale Live-Sicht gegen eine detektor-UNABHÄNGIGE Referenz
gemessen (zentrierte Rückblick-Labels aus `regime_truth.centered_labels`,
dieselbe Referenz wie in der wissenschaftlichen Kalibrierung):
gesamt, im Holdout (finaler Test), in der inneren Validierung, plus
Erkennungs-Verzögerung und verpasste Referenz-Phasen.
"""
from typing import Dict, List, Optional

from services import regime_truth as rt

REF_GOOD = 65.0   # Richtungs-Treffer gegen die Referenz: ab hier "gut"
REF_OK = 55.0     # dazwischen "mittel" – darunter "schwach"

# ---- Referenz v2 (Prüfung 24.09.2026) ----
# v1 nahm Fenster/Mindestlänge aus dem GEPRÜFTEN Modell (mittlerer Horizont,
# 0,8 % des Zeitraums): 1h-"fein" wurde gegen ~43-Tage-Phasen gemessen, 4h gegen
# ~22 Tage, 15m gegen ~58 Tage – nicht vergleichbar und viel träger als das
# Daytrading-Ziel (Ø 4–14 Tage). Zudem ist die Referenz zu 66–90 % "seitwärts":
# ein Detektor, der IMMER seitwärts sagt, erreichte schon ~70 % Roh-Treffer.
# v2: festes Fenster (Standard 7 Tage -> Ø Referenz-Phase ~10 Tage bei BTC 1h),
# feste Mindestlänge 2 Tage, und die Note basiert auf dem SKILL über der
# trivialen Mehrheits-Baseline (+ klassen-balancierter Treffer).
REFERENCE_VERSION = 2
DEFAULT_WINDOW_DAYS = 7.0
DEFAULT_MIN_DAYS = 2.0
SKILL_GOOD = 40.0   # % der möglichen Verbesserung über "immer Mehrheitsklasse"
SKILL_OK = 20.0


def reference_cfg(model_cfg: Dict) -> Dict:
    """Detektor-unabhängige Referenz-Konfiguration (rein). Nur Fenster/Mindest-
    länge sind fix; die Vola-Unterachse (9er-Modus) bleibt modellbezogen."""
    cfg = dict(model_cfg or {})
    cfg["reference_window_days"] = float(cfg.get("reference_window_days") or DEFAULT_WINDOW_DAYS)
    cfg["reference_min_days"] = float(cfg.get("reference_min_days") or DEFAULT_MIN_DAYS)
    return cfg


def _baseline_pct(truth: List, mode: int) -> Optional[float]:
    """Treffer eines Detektors, der immer die häufigste Referenz-Richtung sagt."""
    tt = [t for t in rt._trend_arr(truth, mode).tolist() if t >= 0]
    if not tt:
        return None
    return round(max(tt.count(k) for k in (0, 1, 2)) / len(tt) * 100.0, 1)


def skill_pct(direction_pct: Optional[float], baseline_pct: Optional[float]) -> Optional[float]:
    """Anteil der möglichen Verbesserung über die triviale Baseline (Kappa-artig)."""
    if direction_pct is None or baseline_pct is None or baseline_pct >= 100.0:
        return None
    return round((float(direction_pct) - float(baseline_pct)) / (100.0 - float(baseline_pct)) * 100.0, 1)


BAL_GOOD = 60.0    # klassen-balancierter Holdout-Treffer (konstant = 33 %)
BAL_OK = 50.0


def balanced_grade(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    return "gut" if pct >= BAL_GOOD else ("mittel" if pct >= BAL_OK else "schwach")


F1_GOOD = 55.0     # Macro-F1 Holdout (konstant „seitwärts“ ≈ 28 %)
F1_OK = 45.0


def f1_grade(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    return "gut" if pct >= F1_GOOD else ("mittel" if pct >= F1_OK else "schwach")


def skill_grade(skill: Optional[float]) -> Optional[str]:
    if skill is None:
        return None
    return "gut" if skill >= SKILL_GOOD else ("mittel" if skill >= SKILL_OK else "schwach")


def _split_index(candles, ts: Optional[int]) -> Optional[int]:
    """Erster Index mit timestamp > ts (None, wenn kein Anker)."""
    if ts is None:
        return None
    ts = int(ts)
    for i, c in enumerate(candles):
        if int(c["timestamp"]) > ts:
            return i
    return len(candles)


def compare(candles, live_labels: List, truth_labels: List, mode: int, bpd: float,
            train_end_ts: Optional[int] = None,
            inner_start_ts: Optional[int] = None) -> Dict:
    """Live-Sicht vs. Referenz: Richtungs-Treffer (roh + klassen-balanciert),
    getrennt nach gesamt / innerer Validierung / Holdout, plus Lag."""
    n = min(len(candles), len(live_labels), len(truth_labels))
    live, truth = list(live_labels[:n]), list(truth_labels[:n])
    total = rt.agreement(live, truth, mode)
    lag = rt.detection_lag_days(live, truth, mode, bpd)
    out = {"bars": total["bars"],
           "direction_pct": total["direction_pct"],
           "balanced_direction_pct": total["balanced_direction_pct"],
           "switches_live": total["switches_live"],
           "switches_truth": total["switches_truth"],
           "mean_lag_days": lag["mean_lag_days"],
           "missed_pct": lag["missed_pct"],
           "holdout_direction_pct": None, "holdout_bars": 0,
           "inner_direction_pct": None, "inner_bars": 0,
           "source": "centered_reference",
           "baseline_pct": _baseline_pct(truth, mode)}
    out["skill_pct"] = skill_pct(out["direction_pct"], out["baseline_pct"])
    # Ø RICHTUNGS-Phase (nur auf/seit/ab – ohne Vola-Unterstufen des 9er-Modus)
    days = total["bars"] / max(bpd, 1e-9)
    out["live_direction_phase_days"] = round(days / (total["switches_live"] + 1), 2) if total["bars"] else None
    out["truth_phase_days"] = round(days / (total["switches_truth"] + 1), 2) if total["bars"] else None
    h0 = _split_index(candles[:n], train_end_ts)
    if h0 is not None and h0 < n:
        m = rt.agreement(live[h0:], truth[h0:], mode)
        out["holdout_direction_pct"] = m["direction_pct"] if m["bars"] else None
        out["holdout_bars"] = m["bars"]
        if m["bars"]:
            out["holdout_balanced_pct"] = m["balanced_direction_pct"]
            out["holdout_f1_pct"] = m["macro_f1_pct"]
            out["holdout_kappa_pct"] = m["kappa_pct"]
            out["holdout_baseline_pct"] = _baseline_pct(truth[h0:], mode)
            out["holdout_skill_pct"] = skill_pct(m["direction_pct"], out["holdout_baseline_pct"])
        if h0 > 0:
            mt = rt.agreement(live[:h0], truth[:h0], mode)
            out["train_balanced_pct"] = mt["balanced_direction_pct"] if mt["bars"] else None
            out["train_f1_pct"] = mt["macro_f1_pct"] if mt["bars"] else None
    i0 = _split_index(candles[:n], inner_start_ts)
    if i0 is not None:
        i1 = h0 if h0 is not None else n
        if i0 < i1:
            m = rt.agreement(live[i0:i1], truth[i0:i1], mode)
            out["inner_direction_pct"] = m["direction_pct"] if m["bars"] else None
            out["inner_bars"] = m["bars"]
            if m["bars"]:
                out["inner_balanced_pct"] = m["balanced_direction_pct"]
                out["inner_f1_pct"] = m["macro_f1_pct"]
                out["inner_skill_pct"] = skill_pct(m["direction_pct"],
                                                   _baseline_pct(truth[i0:i1], mode))
    return out


def grade(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    return "gut" if pct >= REF_GOOD else ("mittel" if pct >= REF_OK else "schwach")
