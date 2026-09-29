"""Detektor "jump": Statistisches Jump-Modell (Regime-Wechsel mit Sprungkosten).

Stand der Forschung für Regime-Erkennung auf Finanzzeitreihen (Nystrup et al.
2020/2021, Aydınhan/Kolm/Mulvey/Shu 2024): statt Schwellen + Stabilitäts-
Filtern wird je Kerze der Zustand gewählt, der die Summe aus
  Abstand(Features, Zustands-Zentrum) + Sprungkosten je Wechsel
minimiert. Die Sprungkosten ersetzen alle Ad-hoc-Persistenz-Regeln durch EINE
optimale Abwägung "Beleg für neuen Zustand" vs. "Kosten des Umschaltens".

Features (kausal, vola-normiert, über Coins/Timeframes vergleichbar):
  x1 = EWMA der Log-Renditen (Halbwertszeit jump_fast_days) je Tag / Tagesvola
  x2 = EWMA der Log-Renditen (Halbwertszeit jump_slow_days) je Tag / Tagesvola
Zentren (semantisch fest -> kein Fit, kein Holdout-Leck): auf = (+c, +c),
seitwärts = (0, 0), ab = (−c, −c) mit c = jump_center.

LIVE  = Online-Filter (Vorwärts-Werte der dynamischen Programmierung) – nur
        Vergangenheit, Wechsel sobald der Beleg die Sprungkosten übersteigt.
FINAL = Viterbi-Rückverfolgung über die ganze Serie mit zentrierten
        (phasenfreien) Features – die optimale nachträgliche Segmentierung.
"""
from typing import Dict

import numpy as np

from services.regime_reactive import _absorb_short

STATES = 3  # 0 = ab, 1 = seitwärts, 2 = auf


def _alpha(halflife_bars: float) -> float:
    return 1.0 - 0.5 ** (1.0 / max(float(halflife_bars), 1.0))


def ewma(x: np.ndarray, halflife_bars: float) -> np.ndarray:
    """Kausaler EWMA (adjust=False), Start beim ersten Wert (rein). pandas statt
    scipy: der lokale Worker hat nur numpy/pandas (local_worker/requirements.txt)."""
    import pandas as pd
    x = np.asarray(x, dtype=float)
    if not len(x):
        return x.copy()
    return pd.Series(x).ewm(alpha=_alpha(halflife_bars), adjust=False).mean().to_numpy(dtype=float)


def zero_phase_ewma(x: np.ndarray, halflife_bars: float) -> np.ndarray:
    """Phasenfreie Glättung (vorwärts + rückwärts gemittelt) – nur Final-Sicht."""
    x = np.asarray(x, dtype=float)
    return 0.5 * (ewma(x, halflife_bars) + ewma(x[::-1], halflife_bars)[::-1])


def params(cfg: Dict) -> Dict:
    bpd = max(float(cfg.get("bars_per_day") or 24.0), 1e-9)
    fast = max(float(cfg.get("jump_fast_days") or 1.0), 0.1)
    slow = max(float(cfg.get("jump_slow_days") or 14.0), fast)
    c = max(float(cfg.get("jump_center") or 0.6), 0.02)
    pen_days = max(float(cfg["jump_penalty_days"] if cfg.get("jump_penalty_days") is not None else 1.5), 0.0)
    return {"bpd": bpd, "fast_bars": fast * bpd, "slow_bars": slow * bpd, "c": c,
            "lam": pen_days * bpd * c * c}


def features(close: np.ndarray, dvol_pct: np.ndarray, p: Dict, centered: bool = False) -> np.ndarray:
    """(n, 2) vola-normierte Trend-Features (rein)."""
    close = np.asarray(close, dtype=float)
    r = np.zeros(len(close))
    if len(close) > 1:
        r[1:] = np.diff(np.log(np.maximum(close, 1e-12)))
    r = np.nan_to_num(r, nan=0.0, posinf=0.0, neginf=0.0)
    smooth = zero_phase_ewma if centered else ewma
    scale = p["bpd"] * 100.0 / dvol_pct
    return np.column_stack([smooth(r, p["fast_bars"]) * scale,
                            smooth(r, p["slow_bars"]) * scale])


def losses(x: np.ndarray, c: float) -> np.ndarray:
    """(n, 3) quadratischer Abstand zu den Zentren ab/seitwärts/auf (rein)."""
    centers = np.array([[-c, -c], [0.0, 0.0], [c, c]])
    d = x[:, None, :] - centers[None, :, :]
    return (d * d).sum(axis=2)


def online_filter(L: np.ndarray, lam: float):
    """Vorwärts-DP: V_t(k) = L_t(k) + min(V_{t-1}(k), min_j V_{t-1}(j) + λ).
    Live-Zustand = argmin V_t (kausal). Rückgabe (labels, V normiert)."""
    n = len(L)
    lab = np.ones(n, dtype=np.int8)
    V = np.zeros((n, STATES))
    v0 = v1 = v2 = 0.0
    for i in range(n):
        m = min(v0, v1, v2) + lam
        v0 = L[i, 0] + min(v0, m)
        v1 = L[i, 1] + min(v1, m)
        v2 = L[i, 2] + min(v2, m)
        b = min(v0, v1, v2)
        v0, v1, v2 = v0 - b, v1 - b, v2 - b
        V[i, 0], V[i, 1], V[i, 2] = v0, v1, v2
        lab[i] = 0 if v0 == 0.0 else (1 if v1 == 0.0 else 2)
    return lab, V


def viterbi(L: np.ndarray, lam: float) -> np.ndarray:
    """Optimale Segmentierung (mit Rückverfolgung, nutzt die ganze Serie)."""
    n = len(L)
    if not n:
        return np.ones(0, dtype=np.int8)
    back = np.zeros((n, STATES), dtype=np.int8)
    v = L[0].astype(float).copy()
    for i in range(1, n):
        j = int(np.argmin(v))
        stay = v
        jump = v[j] + lam
        take_jump = jump < stay
        back[i] = np.where(take_jump, j, np.arange(STATES))
        v = L[i] + np.where(take_jump, jump, stay)
        v -= v.min()
    out = np.ones(n, dtype=np.int8)
    k = int(np.argmin(v))
    for i in range(n - 1, -1, -1):
        out[i] = k
        k = int(back[i, k])
    return out


def detect_jump(f: Dict, cfg: Dict) -> Dict:
    close = np.asarray(f["close"], dtype=float)
    n = len(close)
    p = params(cfg)
    dvol = np.nan_to_num(np.asarray(f["daily_vol_pct"], dtype=float), nan=2.0)
    dvol = np.where(dvol <= 0, 2.0, dvol)
    c, lam = p["c"], p["lam"]

    x_live = features(close, dvol, p)
    L_live = losses(x_live, c)
    live3, V = online_filter(L_live, lam)

    final3 = viterbi(losses(features(close, dvol, p, centered=True), c), lam)
    mp_days = float(cfg.get("min_phase_days") or 0.0)
    if mp_days <= 0:
        mp_days = min(max(n / p["bpd"] * 0.007, 1.0), 7.0)
    mp_bars = max(int(round(mp_days * p["bpd"])), 2)
    final3 = _absorb_short(final3, mp_bars)

    # Sicherheit des Live-Zustands: Abstand zum nächstbesten Zustand in
    # Einheiten der Sprungkosten (1 = Wechsel wäre voll "bezahlt").
    Vs = np.sort(V, axis=1)
    gap = Vs[:, 1] - Vs[:, 0]
    conf = np.clip(gap / max(lam, c * c), 0.15, 1.0)
    temp = max(lam, c * c) / 2.0
    probs = np.exp(-(V - V.min(axis=1, keepdims=True)) / temp)
    probs = np.clip(probs / probs.sum(axis=1, keepdims=True), 0.02, 0.96)
    probs /= probs.sum(axis=1, keepdims=True)
    trend = x_live.mean(axis=1)
    trendiness = np.clip(np.abs(trend) / (2.0 * c), 0.0, 1.0)
    live_dir = live3.astype(np.int8) - 1
    retrace = np.clip(c - live_dir * trend, 0.0, None)
    retrace[live_dir == 0] = 0.0
    warm = min(int(round(3 * p["slow_bars"])), n)
    zeros = np.zeros(n)
    return {"live_dir": live_dir, "live3": live3, "final3": final3,
            "trendiness": trendiness, "probs": probs, "conf": conf,
            "thr": np.full(n, 2.0 * c), "need": np.full(n, float(lam)),
            "retrace": retrace, "since_ext": zeros, "leg_prog": trendiness,
            "rev_count": np.zeros(n, dtype=int), "pivots": [],
            "ema_dir": live_dir.copy(), "ema_crosses": [],
            "warm": int(warm), "persist": 1,
            "min_phase_days": round(mp_days, 2)}
