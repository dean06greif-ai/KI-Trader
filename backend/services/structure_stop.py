"""Struktur-Stop (KI-Trader): SL hinter die 15m/1h-Struktur statt ins 1m-Rauschen.

Befund Prod 11/2026: SL 0,4–0,7 % → −0,34 R je Trade (Gebühren 0,24 R),
SL ≥ 1 % → −0,03 R, SL ≥ 2 % → +0,01 R. Der Einstieg bleibt beim Detektor/KI,
nur Stop und Ziele werden aus der höheren Zeiteinheit abgeleitet:
  SL = hinter dem letzten Swing (Tief bei LONG, Hoch bei SHORT) der letzten
  `lookback` Kerzen im Struktur-Timeframe + Puffer, begrenzt auf [min %, max %].
  Liegt die Struktur weiter als max % weg → kein Trade (Stop wäre beliebig).
  TP1/TP-Full werden mit gleichem CRV mitskaliert.
Abschaltbar (Default AUS = bisheriges Verhalten). Reine Funktionen.
"""
from typing import Dict, List, Optional, Tuple

DEFAULTS = {
    "structure_stop_enabled": False,
    "structure_tf": "15m",          # 15m oder 1h
    "structure_lookback": 12,       # Kerzen im Struktur-TF (12×15m = 3 h)
    "structure_min_pct": 1.0,
    "structure_max_pct": 3.0,
    "structure_buffer_pct": 0.1,    # Puffer hinter dem Swing
}
TFS = ("15m", "1h")


def settings(ai_cfg: Optional[Dict]) -> Dict:
    ai_cfg = ai_cfg or {}
    out = {k: ai_cfg.get(k, v) for k, v in DEFAULTS.items()}
    if out["structure_tf"] not in TFS:
        out["structure_tf"] = DEFAULTS["structure_tf"]
    return out


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    if "structure_stop_enabled" in updates:
        cfg["structure_stop_enabled"] = bool(updates["structure_stop_enabled"])
    if updates.get("structure_tf") in TFS:
        cfg["structure_tf"] = updates["structure_tf"]
    for k, lo, hi in (("structure_lookback", 3, 96), ("structure_min_pct", 0.3, 5.0),
                      ("structure_max_pct", 0.5, 8.0), ("structure_buffer_pct", 0.0, 1.0)):
        if k in updates:
            try:
                v = max(lo, min(hi, float(updates[k])))
                cfg[k] = int(v) if k == "structure_lookback" else round(v, 3)
            except (TypeError, ValueError):
                continue
    if float(cfg.get("structure_max_pct", 3.0)) < float(cfg.get("structure_min_pct", 1.0)):
        cfg["structure_max_pct"] = cfg["structure_min_pct"]


def swing_level(htf: List[Dict], side: str, lookback: int) -> Optional[float]:
    rows = list(htf)[-lookback:] if htf else []
    if not rows:
        return None
    try:
        return min(float(c["low"]) for c in rows) if side == "LONG" else max(float(c["high"]) for c in rows)
    except (KeyError, TypeError, ValueError):
        return None


def compute(side: str, entry: float, sl: float, tp1: float, tpf: float, htf: List[Dict],
            ai_cfg: Optional[Dict]) -> Tuple[Optional[Tuple[float, float, float]], str]:
    """Neue Levels (sl, tp1, tpf) oder (None, Grund). Rein."""
    st = settings(ai_cfg)
    side = str(side).upper()
    entry = float(entry)
    lvl = swing_level(htf, side, int(st["structure_lookback"]))
    if lvl is None or entry <= 0:
        return None, "keine Struktur-Kerzen"
    buf = entry * float(st["structure_buffer_pct"]) / 100.0
    raw = (lvl - buf) if side == "LONG" else (lvl + buf)
    dist_pct = (entry - raw) / entry * 100.0 if side == "LONG" else (raw - entry) / entry * 100.0
    lo, hi = float(st["structure_min_pct"]), float(st["structure_max_pct"])
    if dist_pct > hi + 1e-9:
        return None, f"Struktur-Stop {dist_pct:.2f}% > Max {hi:g}% ({st['structure_tf']}-Swing zu weit)"
    dist_pct = max(dist_pct, lo)
    d = entry * dist_pct / 100.0
    old = abs(entry - float(sl)) or d
    k = d / old
    sgn = 1 if side == "LONG" else -1
    new_sl = entry - sgn * d
    new_tp1 = entry + sgn * abs(float(tp1) - entry) * k
    new_tpf = entry + sgn * abs(float(tpf) - entry) * k
    return (new_sl, new_tp1, new_tpf), (f"Struktur-Stop {st['structure_tf']}: SL {old / entry * 100:.2f}%"
                                        f"→{dist_pct:.2f}%, TPs ×{k:.2f}")
