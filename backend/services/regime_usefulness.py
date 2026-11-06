"""Nutzen-Note einer Regime-Erkennung (rein, testbar – ANALYSE_DYNAMIK_REGIME_0626 E4).

Bewertet eine LIVE-Richtung (-1 ab, 0 seitwärts, +1 auf; None/-9 = unbekannt)
danach, was sie für den Handel taugt – nicht nach Übereinstimmung mit einer
Rückblick-Referenz:

1. Trennschärfe: Ø Rendite danach (auf) − Ø (ab), in Einheiten der
   Horizont-Vola (z-Wert) – je Horizont (1 und 3 Tage)
2. Vorzeichen-Treffer: Anteil Trend-Kerzen, deren Rendite danach in
   Regime-Richtung lief
3. Stabilität: Ø Phasendauer (Tage) und Ping-Pong-Quote (A→B→A in < 3 Tagen)

Note 0–100: >= 60 gut (grün), >= 45 mittel (gelb), sonst schwach (rot).
"""
from typing import Dict, List, Optional, Sequence

import numpy as np

HORIZONS_DAYS = (1.0, 3.0)
MIN_BARS_DIR = 30
PINGPONG_DAYS = 3.0
TARGET_PHASE_DAYS = 7.0
GRADE_GOOD = 60.0
GRADE_MID = 45.0
MIN_PHASES = 8   # darunter höchstens „mittel“ – zu wenige Phasen für ein Urteil


def _dir_array(direction: Sequence) -> np.ndarray:
    out = np.full(len(direction), -9, dtype=int)
    for i, d in enumerate(direction):
        if d is not None and int(d) in (-1, 0, 1):
            out[i] = int(d)
    return out


def phases(d: np.ndarray) -> List[tuple]:
    """(start, end_exkl, richtung) für zusammenhängende bekannte Richtungen."""
    out, s = [], None
    for i in range(len(d) + 1):
        cur = d[i] if i < len(d) else None
        if s is not None and (cur is None or cur != d[s]):
            if d[s] != -9:
                out.append((s, i, int(d[s])))
            s = None
        if cur is not None and s is None:
            s = i
    return out


def stability(d: np.ndarray, bpd: float) -> Dict:
    ph = phases(d)
    if not ph:
        return {"phases": 0, "avg_phase_days": None, "pingpong_pct": None, "switches_per_30d": None}
    lens = [(e - s) / bpd for s, e, _ in ph]
    pp, sw = 0, max(len(ph) - 1, 0)
    for a, b, c in zip(ph, ph[1:], ph[2:]):
        if a[2] == c[2] and (b[1] - b[0]) / bpd < PINGPONG_DAYS:
            pp += 1
    known_days = sum(lens)
    return {"phases": len(ph), "avg_phase_days": round(float(np.mean(lens)), 2),
            "pingpong_pct": round(pp / sw * 100, 1) if sw else 0.0,
            "switches_per_30d": round(sw / known_days * 30, 2) if known_days > 0 else None}


def horizon(close: np.ndarray, d: np.ndarray, k: int, start: int = 0) -> Dict:
    n = len(close)
    if k <= 0 or n - start <= k + MIN_BARS_DIR:
        return {"ok": False}
    lr = np.log(np.maximum(close, 1e-12))
    fwd = lr[k:] - lr[:-k]
    dd = d[:-k]
    idx = np.arange(len(fwd))
    sel = idx >= start
    sigma = float(np.std(fwd[sel])) or 1e-9
    out = {"ok": True, "bars": int(sel.sum())}
    means = {}
    for name, val in (("down", -1), ("side", 0), ("up", 1)):
        m = sel & (dd == val)
        means[name] = float(np.mean(fwd[m])) if m.sum() >= MIN_BARS_DIR else None
        out[f"n_{name}"] = int(m.sum())
    out["fwd_pct"] = {k_: (round(v * 100, 3) if v is not None else None) for k_, v in means.items()}
    if means["up"] is not None and means["down"] is not None:
        out["separation_z"] = round((means["up"] - means["down"]) / sigma, 3)
    else:
        out["separation_z"] = None
    trend = sel & (dd != 0) & (dd != -9)
    out["sign_hit_pct"] = (round(float(np.mean(np.sign(fwd[trend]) == dd[trend])) * 100, 1)
                           if trend.sum() >= MIN_BARS_DIR else None)
    return out


def score(hz: List[Dict], stab: Dict) -> Optional[float]:
    seps = [h["separation_z"] for h in hz if h.get("separation_z") is not None]
    hits = [h["sign_hit_pct"] for h in hz if h.get("sign_hit_pct") is not None]
    if not seps or stab.get("avg_phase_days") is None:
        return None
    s_sep = float(np.clip(np.mean(seps) / 0.3, 0.0, 1.0)) * 45.0
    s_hit = float(np.clip((np.mean(hits) - 50.0) / 8.0, 0.0, 1.0)) * 20.0 if hits else 0.0
    s_dur = float(np.clip(stab["avg_phase_days"] / TARGET_PHASE_DAYS, 0.0, 1.0)) * 20.0
    s_pp = (1.0 - float(np.clip((stab.get("pingpong_pct") or 0) / 30.0, 0.0, 1.0))) * 15.0
    return round(s_sep + s_hit + s_dur + s_pp, 1)


def grade(sc: Optional[float]) -> str:
    if sc is None:
        return "zu wenig Daten"
    return "gut" if sc >= GRADE_GOOD else "mittel" if sc >= GRADE_MID else "schwach"


def light(sc: Optional[float]) -> str:
    if sc is None:
        return "grau"
    return "grün" if sc >= GRADE_GOOD else "gelb" if sc >= GRADE_MID else "rot"


def evaluate(close: Sequence, direction: Sequence, bpd: float, start: int = 0) -> Dict:
    c = np.asarray(close, dtype=float)
    d = _dir_array(direction)
    hz = []
    for h in HORIZONS_DAYS:
        r = horizon(c, d, max(int(round(h * bpd)), 1), start)
        r["days"] = h
        hz.append(r)
    stab = stability(d[start:], bpd)
    sc = score(hz, stab)
    few = bool(sc is not None and (stab.get("phases") or 0) < MIN_PHASES)
    if few:
        sc = min(sc, GRADE_GOOD - 0.1)
    known = d[start:] != -9
    share = {name: round(float(np.mean(d[start:][known] == v)) * 100, 1) if known.any() else None
             for name, v in (("down", -1), ("side", 0), ("up", 1))}
    return {"score": sc, "grade": grade(sc), "light": light(sc), "horizons": hz,
            "stability": stab, "share_pct": share, "few_phases": few}
