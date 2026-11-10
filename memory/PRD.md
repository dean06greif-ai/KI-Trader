# PRD – KI-Trader Verbesserung (externes Repo, Render-Deploy)
Repo: https://github.com/dean06greif-ai/KI-Trader (Branch conflict_051126_1912) – nach /app übernommen, Ordnerstruktur unverändert.

## Aufgabe
Dynamische Strategien (regime-gefilterte Strategien) und Regime Lab neutral prüfen und verbessern,
Multi-Timeframe-Regime (8H + 1D) bewerten, Overfitting bei wenigen Trades beachten.
Grundsatz: stabil, modular, rückwärtskompatibel, Regressionstests.

## User-Entscheidungen
- Erst Analyse-Bericht (/app/ANALYSE_DYNAMIK_REGIME_0626.md), dann Umsetzung (bestätigt)
- Alle Anlageklassen, je Klasse eigenes Rezept
- Timeframes / Overfitting-Strenge: Agent entscheidet → Erkennen (Nowcasting), Glaubwürdigkeits-Gewichtung

## Umgesetzt (06.11.2026) – Details: /app/UMSETZUNG_DYNAMIK_REGIME_0626.md
- E1 Statistik-Kern `services/evidence_stats.py`; robuste Validierung/Verdict/Regime-Evidenz (Modus „robust“ für neue Läufe, „classic“ = Alt-Verhalten)
- E2 Zeitliche Validierung mit Embargo (`split_segments(mode="time")`), Ziel „robust“
- E3 `services/regime_hierarchy.py` Makro × Haupt je Anlageklasse + BTC-Leitmarkt
- E4 `services/regime_usefulness.py` Nutzen-Note
- E5 Regime-Assistent (Backend + `RegimeAssistant.js` im Regime-Lab, Abschnitt 0), optionaler KI-Trader-Fallback (Standard AUS)
- E6 Makro-Filter + Übergangsschutz je dynamischer Strategie (Standard AUS), `DynamicGuardPolicy.js`
- Forschungs-Modus `services/research_mode.py`, `/api/research-mode`
- Tests: 33 neue Unit-Tests + API-Test (Testing-Agent), Regression 515 grün (1 vorbestehender Fehler)

## Copilot-Prüfung (06.11.2026)
- Regime-/Strategie-Copilot kennen jetzt Assistent, Makro×Haupt, Nutzen-Note, robuste Bewertung, Schutzfilter, Ist-Zustand je Asset
- Antwortstil: erst direkte Antwort, Fachbegriffe erklärt, bis ~300 Wörter bei Erklärfragen; Verlauf 10 × 1000 Zeichen
- Fehler im Chat sichtbar (rote Blase, Text bleibt), /api/copilot/context-preview (Admin) zeigt den exakten Prompt
- Guthaben: Banner im Copilot (/api/copilot/credits), 402 -> nächster Key -> Free-Modell + Hinweis, Telegram sofort (Dedupe 6 h)
- Offen: echte Antwortqualität mit echtem OpenRouter-Key prüfen (in der Vorschau kein Key)

## Backlog
- P1: Makro-Filter auch im dynamischen Backtest/Walk-Forward (Backtest-Live-Gleichheit)
- P1: Größen-Faktor optional automatisch auf die Positionsgröße anwenden (heute nur Anzeige)
- P2: Nutzen-Note als zusätzliche Spalte bei Champion-Auswahl/Freigabe im Lab
- P2: Assistent-Lauf für Nicht-Krypto auf längerer Basiswert-Historie (Yahoo/IBKR)
