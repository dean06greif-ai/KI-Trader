"""Restzeit-Schätzung laufender Jobs (rein & testbar).

Bisher: ETA = Laufzeit / Fortschritt × Rest (rein linear über den ganzen Lauf).
Problem: Phasen laufen unterschiedlich schnell (Daten laden, Suche, Robustheits-
Checks am Ende) – stand der Fortschritt kurz vor Schluss, zeigte die Anzeige
minutenlang "~5 s".

Neu: Mischung aus linearer Gesamt-Schätzung und dem Tempo der letzten Minuten
(gleitendes Fenster), abzüglich der Zeit seit dem letzten Fortschritt. Hängt ein
Job länger als geschätzt, wächst die Restzeit wieder ehrlich mit (statt auf
wenigen Sekunden stehen zu bleiben). Zustand liegt in job["_eta"] (nicht öffentlich).
"""
from typing import Dict, Optional

WINDOW_S = 240.0      # Tempo der letzten 4 Minuten
RECENT_WEIGHT = 0.65  # Gewicht des aktuellen Tempos ggü. dem Gesamt-Durchschnitt
MAX_SAMPLES = 40


def estimate(job: Dict, elapsed: float, progress: float, now: float) -> Optional[int]:
    """Restzeit in Sekunden (None = noch keine Aussage möglich)."""
    p = float(progress or 0)
    if p < 2 or p >= 100 or elapsed <= 0:
        return None
    st = job.setdefault("_eta", {"samples": [], "last_p": None, "last_move": now})
    if st["last_p"] is None or p > st["last_p"]:
        st["samples"].append((now, p))
        del st["samples"][:-MAX_SAMPLES]
        st["last_p"] = p
        st["last_move"] = now
    linear = elapsed / p * (100 - p)
    recent = None
    win = [(t, q) for t, q in st["samples"] if now - t <= WINDOW_S]
    if len(win) >= 2 and win[-1][1] > win[0][1] and win[-1][0] > win[0][0]:
        rate = (win[-1][1] - win[0][1]) / (win[-1][0] - win[0][0])
        recent = (100 - p) / rate
    est = linear if recent is None else (1 - RECENT_WEIGHT) * linear + RECENT_WEIGHT * recent
    since = max(now - st["last_move"], 0.0)
    remaining = est - since
    if remaining < since * 0.5:
        # Länger ohne Fortschritt als erwartet: nicht bei "~5 s" stehen bleiben
        remaining = since * 0.5
    return int(max(remaining, 1))
