# Prüfbericht 07.10.2026 – Regime-Phasendauer je Regime-Anzahl + Such-Presets

## Frage
„Nur mit 9 Regimen erreiche ich eine Ø Phasendauer > 5 Tage, mit 3 oder 5 Regimen fast nie – müsste es nicht umgekehrt sein?“

**Antwort: Ja, eigentlich müsste es umgekehrt sein (bzw. gleich). Es lag an Messfehlern bzw. unfairer Bewertung, nicht am Markt.**
Die Regime-Anzahl ändert die **Richtungs-Erkennung** (auf/seitwärts/ab) nicht. Sie fügt nur eine zweite Achse hinzu:
5 Regime = Stärke (leicht/stark), 9 Regime = Volatilität (niedrig/mittel/hoch). Bei identischem Detektor ist die Richtung bei 3/5/9 Regimen Kerze für Kerze gleich.
Gemessen mit `backend/scripts/regime_mode_phase_probe.py` (Testbett BTC/ETH 1h, 720 Tage).

## Befunde (alle behoben)
| # | Befund | Wirkung | Fix |
|---|--------|---------|-----|
| A | Automatische Glättungs-Wahl (`_profile_quality`) maß volle Regime-Abschnitte gegen „Zeitraum / 8, 12, 18“ (je Regime-Anzahl) | 9 Regime wählten „grob/standard“, 3/5 Regime „fein“ → **kürzere Phasen bei weniger Regimen** | gleiche Messgröße (Ø Live-Richtungs-Phase) und gleiches Ziel (~8,7 d, Mitte Sweet Spot) für alle Regime-Anzahlen |
| B | Unterachse (Stärke/Vola) ohne Mindestdauer | 5 Regime: Ø Regime-Phase **1,39 d** bei 4,8 d Richtungs-Phase → Such-Strafe ~10,8 Punkte, durch die Suche nicht beeinflussbar | neue Einstellung `sub_min_days` (Standard 1,5 d, im Regime-Lab unter „Phasen-Glättung“): 5er-Regime-Phase 1,39 → 4,28 d, Score 28,6 → 37,3 (Richtung/F1 unverändert) |
| C | Such-Strafe nutzte für die volle Regime-Phase dieselbe 5-Tage-Untergrenze wie für die Richtung | 9/5 Regime wurden für Unterstufen-Wechsel bestraft, 3 Regime kaum | Richtungs-Phase muss in den Sweet Spot (5–15 d), Regime-Phase nur über eine skalierte Untergrenze: **3 → 5 d, 5 → 3 d, 9 → 2,5 d** (`services/regime_phase.py`) |
| D | Referenz (detektor-unabhängige „Wahrheit“) glättete volle Abschnitte → Referenz-RICHTUNG hing an der Regime-Anzahl | 9 Regime bekamen bei identischer Live-Richtung bis **+5 Punkte Referenz-F1** (z.B. jump: 60,1 vs. 55,2) → bessere Note nur durch die Messung | Richtung zuerst glätten, Unterachse danach innerhalb der Phase (Referenz-Revision 2.1) |
| E | Suchraum enthielt keine Parameter der Unterachse | Suche konnte die Regime-Phase bei 5/9 Regimen nicht verbessern | Suchraum je Regime-Anzahl: `sub_min_days` (5/9), `strong_speed_ratio` (5) |
| F | KI-Trader-Prompt „Struktur: AUFWÄRTS seit X Tagen“ zählte ab dem letzten Regime-Wechsel (auch Stufenwechsel) | bei 5/9 Regimen zu kleine „seit“-Angabe | `direction_since` (Beginn der Richtung) |

Nach den Fixes liefern 3/5/9 Regime bei identischer Richtung **identische Scores/F1** (Testbett), die Unterschiede liegen nur noch in der Regime-Phase.

## Ergebnis echte Daten (lokale Preview, Bitunix BTC+ETH 1h, 540 Tage, Autopilot 20 Runden)
| Regime | Ø Richtungs-Phase | Ø Regime-Phase | Score | Note |
|---|---|---|---|---|
| 3 | 12,2 d | 12,1 d | 68,5 | gut (5/6 Kriterien) |
| 5 | 12,7 d | 11,7 d | 63,3 | gut (5/6) |
| 9 | 12,2 d | 8,1 d | 68,5 | gut (5/6) |

Vorher (Ausgangslage 5 Regime): Regime-Phase 4,8 d, Note „schwach“. Offenes Kriterium in allen Modi: „Verpasste Phasen ≤ 15 %“ – das ist jetzt der eigentliche Hebel für „sehr gut“ (längere Suche / Profil „Lange Endlos-Suche“).

## Timeframe
Alle Detektor-Einstellungen sind in **Tagen** angegeben – die Phasendauer hängt daher kaum vom Timeframe ab. Für weniger Regime braucht man **keinen** kleineren Timeframe. Kleinere TF erhöhen nur die Auflösung (schnellere Bestätigung, mehr Rauschen).

## Welche Regime-Anzahl für dynamische Strategien?
- **3 Regime**: meiste Daten je Regime, wenigste Strategie-Wechsel → robusteste Basis für dynamische Strategien und den KI-Trader. Empfehlung als Standard.
- **5 Regime**: sinnvoll, wenn „starker“ vs. „leichter“ Trend unterschiedlich gehandelt werden soll (z.B. Trendfolge nur im starken Trend).
- **9 Regime**: jede Phase bekommt nur ~1/9 der Daten → höheres Overfitting-Risiko bei der Strategie-Suche je Regime; nur mit viel Historie und Walk-Forward.
Die frühere „9 ist am besten“-Beobachtung war ein Mess-Artefakt (Befunde A und D).

## Rückwärtskompatibilität
- Gespeicherte Modelle ohne `sub_min_days` (alle bisherigen Analysen, freigegebene Lab-Modelle, dynamische Strategien, KI-Trader-Brücke) klassifizieren **exakt wie vorher**.
- Neue Analysen/Autopilot-Läufe nutzen die Fixes. Alte 9-Regime-Ergebnisse im Verlauf sind leicht geschönt (Befund D) – neue Läufe nicht 1:1 mit alten vergleichen.
- Die Mindesthaltedauer (jeder Regime-Wechsel) bleibt unverändert.

## Such-Presets (neu)
`frontend/src/lib/searchPresets.js` (eine Quelle) + `components/SearchPresetBar.js`:
Trendfolge · Mean Reversion · Breakout · Momentum · Scalping · Range/Seitwärts · Smart Money · Breite Suche.
Jedes Preset aktiviert nur passende Indikatoren + Mitoptimierungs-Gruppen, Ziel, Min. Trades, Max. Regeln, Iterationen, Regel-Timeframes und Richtungs-Bias.
- **Strategie-Optimizer** (Parameter/Discovery/Combo/Endlos): Presets nutzen dasselbe Schema wie die Copilot-Vorschläge, Walk-Forward rollierend an.
- **Regime-Lab → Strategie je Regime**: Preset-Leiste mit ★-Empfehlung je Marktphase (Trend → Trendfolge, Seitwärts → Mean Reversion, Breakout → Breakout); zusätzliche Gruppen Trail-SL, Zeit-Exit, Entry-Ordertyp.
- **Dynamik-Werkbank**: Preset-Leiste + „Je Regime automatisch passendes Preset“ (Backend `regime_presets`, optional, ohne = bisheriges Verhalten).
- **Regime-Autopilot**: Such-Profile Daytrading (5–15 d) · Reaktiv (3–10 d) · Ruhig/Swing (8–25 d) · Lange Endlos-Suche.

## Tests
- `backend/tests/test_regime_phase_fairness.py` (12), `backend/tests/test_search_presets_workbench.py` (4), Live `test_iter56_review.py`.
- Gesamte Unit-Suite: keine neuen Fehler gegenüber dem Original-Branch (vorhandene Fehler sind Live-/Umgebungs-Tests).
- Diagnose-Skripte: `backend/scripts/regime_mode_phase_probe.py`, `backend/scripts/regime_mode_autopilot_probe.sh` (nur lokale Preview).
