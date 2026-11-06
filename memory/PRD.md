# PRD – KI-Trader Verbesserung (externes Repo, Render-Deploy)
Repo: https://github.com/dean06greif-ai/KI-Trader (Branch conflict_051126_1912), Struktur muss erhalten bleiben.

## Aufgabe
Dynamische Strategien (regime-gefilterte Strategien) und Regime Lab neutral prüfen, Fehler/Lücken finden,
Multi-Timeframe-Regime (Vorschlag andere KI: 8H + 1D, 4H) bewerten, Overfitting bei wenigen Trades beachten.
Grundsatz: stabil, modular, rückwärtskompatibel, Regressionstests.

## User-Entscheidungen
- Vorgehen: erst Analyse-Bericht, dann umsetzen
- Anlageklassen: alle, je Klasse eigene Regime
- Timeframes / Overfitting-Strenge / Ziel: Agent entscheidet (Erkennen statt Vorhersagen, Glaubwürdigkeits-Gewichtung)

## Stand 06/2026
- Analyse erstellt: /app/ANALYSE_DYNAMIK_REGIME_0626.md (Befunde D1–D8, R1–R5, Plan E1–E6)
- Noch keine Code-Änderungen

## Backlog
- P0: E1 Statistik-Kern (Shrinkage, Untergrenze, Bootstrap) + E2 zeitliche Validierung
- P1: E3 Multi-TF (Makro × Haupt, BTC-Kontext), E5 Regime-Assistent je Anlageklasse
- P2: E4 Nutzen-Note im Lab, E6 Positionsgröße nach Regime-Sicherheit
