"""Phasendauer-Logik der Regime-Erkennung – EINE Quelle für alle Regime-Anzahlen.

Hintergrund (Prüfung 10/2026, scripts/regime_mode_phase_probe.py): Die Richtung
(auf/seitwärts/ab) ist bei 3, 5 und 9 Regimen identisch erkannt – die Regime-
Anzahl ändert nur die ZWEITE Achse (5er: Stärke leicht/stark, 9er: Vola
niedrig/mittel/hoch). Vorher
  * wurde die Unterachse ohne Mindestdauer umgeschaltet (5er: Ø Regime-Phase
    ~1,4 d bei 4,8 d Richtungs-Phase) – die dynamische Strategie wechselte
    dadurch alle ~1–2 Tage die Sub-Strategie,
  * galt für die Regime-Phase dieselbe Untergrenze (5 d) wie für die Richtungs-
    Phase -> bei 5/9 Regimen dominierte die Unterachse die Such-Strafe,
  * hing die automatische Glättungs-Wahl von der Regime-Anzahl ab.
Hier liegen die reinen, testbaren Bausteine dafür.
"""
import numpy as np

# Untergrenze der vollen Regime-Phase (= Wechsel der Sub-Strategie) relativ zur
# Untergrenze der Richtungs-Phase: mehr Regime je Richtung -> kürzere Regime-
# Phasen sind fachlich korrekt (eine 5-Tage-Aufwärtsphase darf in „leicht“ und
# „stark“ zerfallen), aber nicht beliebig kurz.
REGIME_PHASE_FLOOR_SHARE = {3: 1.0, 5: 0.6, 9: 0.5}
# Neue Analysen: Mindestdauer der Unterachse (Tage). Alt-Modelle ohne Schlüssel
# behalten exakt ihr bisheriges Verhalten (0 = aus).
DEFAULT_SUB_MIN_DAYS = 1.5
# Ziel der automatischen Glättungs-Wahl: Ø RICHTUNGS-Phase der Final-Sicht
# (Mitte des Sweet Spots 5–15 d), bei kurzen Zeiträumen höchstens 1/6 davon.
PROFILE_TARGET_DAYS = 8.7


def regime_phase_floor(mode, direction_min_days: float) -> float:
    """Untergrenze der vollen Regime-Phase je Regime-Anzahl (rein)."""
    try:
        m = int(mode)
    except (TypeError, ValueError):
        m = 9
    return float(direction_min_days) * REGIME_PHASE_FLOOR_SHARE.get(m, 1.0)


def profile_target_days(total_days: float) -> float:
    """Ziel-Ø-Richtungs-Phase für die Glättungs-Wahl – unabhängig von der
    Regime-Anzahl (rein)."""
    return max(min(PROFILE_TARGET_DAYS, float(total_days or 0) / 6.0), 1.0)


def sub_min_bars(cfg: dict) -> int:
    """Mindestdauer der Unterachse in Kerzen; 0 = aus (Alt-Modelle)."""
    days = float((cfg or {}).get("sub_min_days") or 0.0)
    if days <= 0:
        return 0
    bpd = float((cfg or {}).get("bars_per_day") or 1.0)
    return max(int(round(days * bpd)), 2)


def debounce_sub(dir3, sub, min_bars: int, causal: bool = True) -> np.ndarray:
    """Mindestdauer der Unterachse INNERHALB einer Richtungs-Phase (rein).
    Die Richtung selbst wird nie verändert – ein Richtungswechsel übernimmt die
    Unterstufe sofort (neue Phase).
    causal=True (Live): ein Stufenwechsel gilt erst, wenn er `min_bars` Kerzen
    angehalten hat (danach ab dieser Kerze) – ohne Zukunftswissen.
    causal=False (Final/Rückblick): Stufen-Läufe kürzer als `min_bars` werden
    der vorherigen Stufe derselben Richtungs-Phase zugeschlagen."""
    d = np.asarray(dir3, dtype=int)
    s = np.asarray(sub, dtype=int).copy()
    n = len(s)
    if min_bars <= 1 or n == 0:
        return s
    out = s.copy()
    if causal:
        cur, pend, cnt = s[0], None, 0
        for i in range(n):
            if i == 0 or d[i] != d[i - 1]:
                cur, pend, cnt = s[i], None, 0
            elif s[i] != cur:
                if s[i] == pend:
                    cnt += 1
                else:
                    pend, cnt = s[i], 1
                if cnt >= min_bars:
                    cur, pend, cnt = s[i], None, 0
            else:
                pend, cnt = None, 0
            out[i] = cur
        return out
    i = 0
    while i < n:
        j = i
        while j < n and d[j] == d[i]:
            j += 1
        k = i
        while k < j:
            e = k
            while e < j and s[e] == s[k]:
                e += 1
            if e - k < min_bars and k > i:
                out[k:e] = out[k - 1]
            k = e
        i = j
    return out
