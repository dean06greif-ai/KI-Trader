# PRD / Aufgabenakte – Read-only-Analyse KI Trader und Regime Lab

## Datum und Status
2026-09-15. Analyse, Offline-Testnachweise und ausführlicher Umsetzungsplan erstellt. Keine Produktimplementierung beauftragt oder vorgenommen.

## Originalproblemstellung (fachlicher Wortlaut, Zugangsdaten absichtlich ausgelassen)
„HEy das ist meine funktionierende externe/ausgelagerte(soll auch so bleiben) daytrading website. diese soll verbessert werden: website public git:
https://github.com/dean06greif-ai/KI-Trader/tree/conflict_150926_0200

Grundsatz: Die Website funktioniert bereits produktiv und stabil. Alle Verbesserungen, Bugfixes und Refactorings sollen sauber, modular und langfristig wartbar integriert werden.
Neue Funktionen dürfen nicht als Schnelllösung eingebaut werden, sondern sollen in die bestehende Architektur eingepflegt werden, sodass die Codebasis übersichtlich bleibt und zukünftige Erweiterungen einfacher umgesetzt werden können. Stabilität, Rückwärtskompatibilität und saubere Struktur haben Vorrang vor aggressiven Änderungen. Vor jeder größeren Änderung bitte zunächst die bestehende Architektur analysieren, Risiken identifizieren und ausreichend Regressionstests erstellen. Das Ziel ist, die interne Struktur zu verbessern, ohne bestehende Funktionen oder Nutzer-Workflows zu verändern.
Beachte dabei das du die Originalstruktur im Ordner/ die Datein beibehältst, damit alles perfekt extern auf render deployt werden kann.
Zu verbessern:
Und zwar sollst du zunächst das Regime Lab und den Ki Trader tiefenanalysieren und bewerten. Und dabei dann Fehlerquellen, unnötige Sachen, Hindernisse, falsche Logik und Verbesserungen um es perfekt zu machen herausarbeiten und dann dahingehend für eine andere KI einen sehr guten Plan erstellen um das je perfekt umzusetzen.

Das Regime Lab, sollt vlt in Zukunft mehr wie eine Labor sein um immer Indikatoren etc. Zu testen verbessern auf Vergangenheitsdaten der einzelnen Assets und das soll dann Grundlage bieten für die dynamischen Strategien / die jeweiligen damit verbundenen Regime auf welchen eine Strategie unterschiedlich getradet wird. Macht das Sinn für eine langfristig ultimative automatische trader Website oder nicht/ lieber anders/ lieber so wie jetz lassen???

Tue dabei deine Plan Fortschritte schon immer als Datei festhalten / die Erkenntnisse um einen ggf. Credits Ausfall vorzubeugen, du hast 110 Credits tobe dich aus.“

## Bestätigte Entscheidungen
- Ausschließlich tiefgehende Codeanalyse, isolierte Regressionstests und ausführlicher dateibasierter Plan; kein Anwendungscode-/Produktivsystemeingriff.
- Regime Lab und KI Trader einschließlich Datenqualität, Backtesting, Strategieauswahl, Risikomanagement, Orderausführung und Verbindung untersuchen.
- Deutsch kommunizieren; Originalstruktur/externer Betrieb beibehalten.
- Mitgeteilte Credentials nicht verwenden/speichern; Nutzer zum Widerruf/Erneuern aufgefordert.

## Personas
- Eigentümer/Trader: stabile produktive Anwendung, nachvollziehbare Bewertung und kontrollierte Weiterentwicklung.
- Nachfolgende Implementierungs-KI: konkrete Pfade, Fehlerrepros, priorisierte Pakete, Tests, Abnahme-/Migrations-/Rückfallregeln.
- Künftiger Researchnutzer: reproduzierbare Asset-/Regime-/Strategieversuche ohne versehentliche Livewirkung.

## Architekturentscheidungen
- Getrennte unveränderte Quellkopie `/app/review_source`, Commit `792ff0ac44861df447d3e619042ecef1704c37c5`.
- Analyseartefakte nur `/app/analysis`, Tests außerhalb Quellrepo; `/app/backend` und `/app/frontend/src`-Starter nicht durch Nutzerapp ersetzt.
- Bestehende React/CRACO-, FastAPI-/Mongo-, Core/Service/Router-/Workerstruktur erhalten.
- Empfohlen: gemeinsame versionierte Daten-/Regime-/Strategie-/Fillverträge, deterministischer Planresolver, unveränderliche Releases und Forschung/Live-Grenze. Kein kompletter Neubau.

## Erarbeitet am 2026-09-15
- Architekturkarte und qualitative Bewertung, 29 finale Befundgruppen (R01–R17, T01–T10, W01–W02) mit Evidenzgraden und Pfad-/Zeilenreferenzen.
- Unabhängige funktionale Zweitprüfung und eigene Nachprüfung.
- 25 gezielte Offlineprüfungen bestanden,3xfail (2 rote Sollabnahmen,1 offene Hypothese); zusätzliche59 bestehende Offline-Tests bestanden,3 bewusst ausgelassen. Keine Produktqualitäts-/Profitabilitätsfreigabe.
- T02/T03-Nachweise auf AST-extrahierte Originalbranches verbessert; Originalcode unverändert.
- Zielbild Forschungslabor, AP00–AP13-Implementierungsplan,36-Punkte-Sollabnahmematrix, additive Migration/Rückfallplan und kopierbarer KI-Handoff.
- Fortschritte inkrementell in `/app/analysis/00_FORTSCHRITT.md` gesichert; komplette Dateien über README erschlossen.
- Gesamtbericht und ZIP mit28 Dateien als statische Analyse-Downloads unter `/ki-trader-analyse/` bereitgestellt; HTTP200, ZIP-/Hashintegrität und README-Verweise geprüft. Anwendungscode der Nutzerquelle weiterhin unverändert.

## Offene Funktionen / priorisierter Backlog (nicht implementiert)
### P0
Ownership-/Scopefehler, Market-Teilfill-Recovery, Stop-Unknown, unbekanntes Risikobudget, echte Dynamic-Regelumschaltung und Baseline-Reset; kontrollierte Setup-Freigabe.
### P1
Dataset-/Candle-/Segmentparität, Backtest-Endposition/Teil-TP-Korrektur, Holdout-/Versionsbindung, Apply/Confirm-State, gemeinsamer MarketContext, Outcome-/Policyprovenienz, Workerparität, IBKR-Fillverträge.
### P2
Statusdarstellung, statistische Robustheit, gezielte Modulbereinigung, später belegte neue Indikatoren/Modelle.

## Nächste Schritte
Nutzer erhält Analysepaket und Empfehlung. Erst nach gesondertem Auftrag mit AP00–AP03 in separater Testkopie beginnen. Keine bestehende Funktion wurde repariert; sämtliche Quellfehler bleiben als Analysebefunde offen. Keine realen Broker-/LLM-/DB-/Produktiv-UI-Integrationstests ausgeführt. Laborzusatznutzen und Profitabilität bleiben nachzuweisen.
