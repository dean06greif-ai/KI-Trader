"""Regime-Nutzen (Plan 1.4, rein & testbar): Taugt die LIVE-Richtung als
Grundlage für Strategiewechsel? Unabhängig von jeder Referenz-Definition wird
gemessen, wie sich der Kurs NACH jeder Kerze entwickelt hat – getrennt nach der
live erkannten Richtung (ab / seitwärts / auf).

Kennzahlen je Horizont (1 Tag, 3 Tage), bevorzugt im Holdout:
- fwd_pct[richtung]: Ø Rendite danach in % (nur Kerzen mit dieser Live-Richtung)
- separation_pct:    Ø(auf) − Ø(ab)  -> > 0 = Richtung hat Vorhersagewert
- sign_hit_pct:      Anteil Trend-Kerzen, bei denen die Rendite danach das
                     Vorzeichen der Richtung hatte (auf: > 0, ab: < 0)
- side_range_ratio:  Ø |Rendite| seitwärts / Ø |Rendite| im Trend
                     (< 1 = Seitwärtsphasen sind wirklich ruhiger)
"""
from typing import Dict, List, Optional

import numpy as np

from services import regime_truth as rt

HORIZONS_DAYS = (1.0, 3.0)
MIN_BARS = 30   # je Richtung, sonst None


def _split_index(candles, ts) -> Optional[int]:
    if ts is None:
        return None
    for i in range(len(candles)):
        c = candles[i]
        t = c["timestamp"] if isinstance(c, dict) else int(candles.ts[i])
        if int(t) > int(ts):
            return i
    return None


def _closes(candles) -> np.ndarray:
    if hasattr(candles, "cl"):
        return np.asarray(candles.cl, dtype=float)
    return np.array([float(c["close"]) for c in candles], dtype=float)


def horizon_stats(close: np.ndarray, trend: np.ndarray, k: int) -> Dict:
    n = len(close)
    if k <= 0 or n <= k:
        return {}
    fwd = (close[k:] / np.maximum(close[:-k], 1e-12) - 1.0) * 100.0
    tr = trend[: n - k]
    out: Dict = {"fwd_pct": {}, "bars": {}}
    means = {}
    for cls, key in ((0, "down"), (1, "side"), (2, "up")):
        m = tr == cls
        out["bars"][key] = int(m.sum())
        means[key] = float(np.mean(fwd[m])) if m.sum() >= MIN_BARS else None
        out["fwd_pct"][key] = None if means[key] is None else round(means[key], 3)
    if means["up"] is not None and means["down"] is not None:
        out["separation_pct"] = round(means["up"] - means["down"], 3)
    up, dn = tr == 2, tr == 0
    hits = int(np.sum(fwd[up] > 0) + np.sum(fwd[dn] < 0))
    tot = int(up.sum() + dn.sum())
    out["sign_hit_pct"] = round(hits / tot * 100.0, 1) if tot >= MIN_BARS else None
    side, trend_m = tr == 1, (tr == 0) | (tr == 2)
    if side.sum() >= MIN_BARS and trend_m.sum() >= MIN_BARS:
        a_side = float(np.mean(np.abs(fwd[side])))
        a_tr = float(np.mean(np.abs(fwd[trend_m])))
        out["side_range_ratio"] = round(a_side / a_tr, 3) if a_tr > 0 else None
    return out


def compute(candles, live_labels: List, mode: int, bpd: float,
            train_end_ts: Optional[int] = None) -> Dict:
    """Regime-Nutzen je Horizont; Basis = Holdout (falls ≥ MIN_BARS je Richtung), sonst gesamt."""
    n = min(len(candles), len(live_labels))
    if n < 10:
        return {}
    close = _closes(candles)[:n]
    trend = rt._trend_arr(live_labels[:n], mode)
    h0 = _split_index(candles, train_end_ts)
    out: Dict = {"horizons": {}}
    for hd in HORIZONS_DAYS:
        k = max(int(round(hd * bpd)), 1)
        st = horizon_stats(close[h0:], trend[h0:], k) if h0 is not None and n - h0 > k else {}
        basis = "holdout"
        if not st or st.get("separation_pct") is None:
            st, basis = horizon_stats(close, trend, k), "overall"
        st["basis"] = basis
        out["horizons"][f"{hd:g}d"] = st
    return out


def summary(util: Dict, horizon: str = "3d") -> Dict:
    """Kurzform für Qualitätskarte/Autopilot."""
    h = (util or {}).get("horizons", {}).get(horizon) or {}
    return {"utility_separation_pct": h.get("separation_pct"),
            "utility_sign_hit_pct": h.get("sign_hit_pct"),
            "utility_side_range_ratio": h.get("side_range_ratio"),
            "utility_basis": h.get("basis")}
