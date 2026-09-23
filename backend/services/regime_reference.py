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
           "source": "centered_reference"}
    h0 = _split_index(candles[:n], train_end_ts)
    if h0 is not None and h0 < n:
        m = rt.agreement(live[h0:], truth[h0:], mode)
        out["holdout_direction_pct"] = m["direction_pct"] if m["bars"] else None
        out["holdout_bars"] = m["bars"]
    i0 = _split_index(candles[:n], inner_start_ts)
    if i0 is not None:
        i1 = h0 if h0 is not None else n
        if i0 < i1:
            m = rt.agreement(live[i0:i1], truth[i0:i1], mode)
            out["inner_direction_pct"] = m["direction_pct"] if m["bars"] else None
            out["inner_bars"] = m["bars"]
    return out


def grade(pct: Optional[float]) -> Optional[str]:
    if pct is None:
        return None
    return "gut" if pct >= REF_GOOD else ("mittel" if pct >= REF_OK else "schwach")
