# Analysefortschritt: Regime Lab und KI Trader

## Auftrag und Sicherheitsgrenzen
- Nutzerentscheidung: ausschließlich tiefgehende Analyse, isolierte Regressionstests und dateibasierter Umsetzungsplan für eine andere KI.
- Umfang: Regime Lab, KI Trader, Datenqualität, Backtesting, Strategieauswahl, Risikomanagement und Orderausführung.
- Quelle: https://github.com/dean06greif-ai/KI-Trader/tree/conflict_150926_0200
- Anwendungscode, ursprüngliche Ordnerstruktur und produktive Systeme werden nicht verändert.
- Mitgeteilte Zugangsdaten werden weder verwendet noch in Berichte, Tests oder Konfiguration übernommen. Rotation/Widerruf ist dringend erforderlich.
- Kein Start des Trading-Servers, keine Verbindung zur produktiven Datenbank, keine Broker- oder LLM-Aufrufe, keine Live-Orders.

## Status
1. Auftrag bestätigt; Analyseverzeichnis angelegt.
2. Erledigt: Repository separat unter `/app/review_source` abgerufen. Commit `792ff0ac44861df447d3e619042ecef1704c37c5`, Commitzeit 2026-09-15T00:00:41Z; Analysetag 2026-09-15 (UTC).
3. Erledigt: Architektur und relevanten Daten-/Entscheidungs-/Orderfluss kartiert. Backend enthält 532 Python-Dateien mit 125.486 Zeilen einschließlich Tests/Skripten; gezielte Tiefenprüfung statt Behauptung einer vollständigen Prüfung jeder Zeile. 280 vorhandene Backend-Testdateien inventarisiert.
4. Erledigt: Finaler Katalog in `02_BEFUNDE_FINAL.md`, qualitative Bewertung und unabhängige Nachprüfung dokumentiert. Arbeitsstand bleibt nur Zwischenhistorie.
5. Erledigt: unabhängige funktionale Zweitprüfung und zwei Offline-Testiterationen. Letzte Suite: 25 bestandene Charakterisierungen/Prüfungen, 3 xfail (2 Soll-Abnahmen des noch fehlerhaften TP-Verhaltens; 1 nicht reproduzierte Forschungshypothese). Zusätzlich 4 ausgewählte bestehende Unit-Tests bestanden. Keine Qualitätsfreigabe der Anwendung!
6. Erledigt: finale Dokumente (Architektur, Befundkatalog, Zielbild, AP00–AP13, Regression/Abnahme/Migration und kopierbarer KI-Startprompt). Downloadbündel erstellt; ZIP-Integrität, Dateihashes, README-Verweise und HTTP-Downloads erfolgreich geprüft.

## Zwischenstand 2026-09-15
- Bestehende Router-/Service-/Core-Struktur und vorhandene Regime-Fassade beibehalten. Kein Neubau erforderlich.
- Die Server-Lifespan startet viele echte Handels-/Daten-/Lernloops und Boot-Migrationen. Deshalb ausdrücklich NICHT gestartet.
- `backend/tests/conftest.py` lädt .env und ordnet Tests erst nach Modulimport anhand URL-Textstellen als unit/live ein. `pytest -m unit` allein ist KEINE sichere Netzwerktrennung.
- Regime-Analyse zeigt reaktiv rückkorrigierte Final-Labels; Live-Klassifikation benutzt kausale Live-Labels. Die Strategie-Suche liest jedoch die Final-Segmente. Dies muss in der Forschung systematisch getrennt werden.
- Vorläufige Empfehlung zur Produktfrage: JA zum historischen Forschungslabor, NEIN zu einem ungeprüften automatischen Lab→Live-Kreislauf. Gemeinsame deterministische Definitionen, unabhängige Validierung und explizite Freigabe sind nötig.

## Zwischenstand nach zweiter Testiteration
- Zusätzliche Tests liegen jetzt ausschließlich unter `/app/analysis/tests/`. `git diff --exit-code` und Liste nichtversionierter Dateien der Quellkopie sind leer (15.09.2026).
- Der Watchdog-Vollclose einer fremden Position wurde mit AST-extrahierter Originalmethode und Fake-Broker bestätigt. Reale Orders wurden nicht ausgeführt.
- Backtest-Same-Bar-Abweichung numerisch bestätigt: +6 statt +4 bei Menge 2, Entry 100, TP1 101/99, TPFull 103/97 (Long/Short), 50% Teilverkauf, Gebühren 0.
- T02/T03-Tests modellieren Branches mit Quelltext-Ankern, führen NICHT den vollständigen Original-Orderflow aus. Finale Berichte müssen diese schwächere Testtiefe offenlegen.
- R05: Kausale Prefix-Invarianz im getesteten festen Reactive-Modell bestätigt; retrospektive Änderung in genau dieser synthetischen Probe nicht gefunden. Codepfad Final→Training ist belegt; tatsächliche Kontamination einer konkreten Produktivanalyse bleibt unbewiesen.
- Neue quelltextbelegte Prüfpunkte: Markierung `ai_learn_synced` vor erfolgreichem Decision-Update; nicht unabhängige Wiedererkennung von Lektionen; fehlendes Equity / fehlender SL wird im Risikomodul nicht fail-closed behandelt. Diese Punkte erhalten im finalen Katalog eigene Evidenzkennzeichnung (kein Laufzeitnachweis behaupten).

## Finaler Nachprüfstand
- 59 weitere bestehende Offline-Tests bestanden,3 bewusst ausgelassen. Zusammen mit25 gezielten Checks84 bestanden;3xfail separat. Frühere4 Einzeltests sind enthalten, nicht nochmals addieren.
- T02/T03 im ausgelagerten Harness verbessert: nun Originalbranches aus AST statt nachgeschriebener Logik. Nach Korrektur ausschließlich von Testhilfen erneut25 passed/3xfail. Originaldateien unverändert.
- K-Means-Numerikwarnung aus bestehendem Test nachverfolgt: `std>=1e-9` wird auf6 Dezimalstellen zu0 gerundet. AlsR16 dokumentiert.
- Finalentscheidung: vorhandene Architektur gezielt härten, dann begrenzter Forschungslaborpilot. Kein zusätzlicher Indikator-/LLM-/Brokerbau als erste Maßnahme.

## Abschluss / Wiederaufnahme
- Finales Dossier:29 Befundgruppen,14 Arbeitspakete (AP00–AP13),36 Soll-Abnahmen, Gesamtbericht, Code-Landkarte und separate Nachweistests.
- Geprüfte Quelle: unverändert; `git diff HEAD` leer, keine nichtversionierten Produkt-/Testdateien in der Quellkopie.
- Maßgebliche Reports: `test_reports/FINAL_SUMMARY.json`, `final_offline.xml`, `baseline_additional.xml` im Paket.
- Downloads: `/ki-trader-analyse/KI-Trader-Analyse-792ff0ac.zip`, `/ki-trader-analyse/GESAMTBERICHT.md`, `/ki-trader-analyse/06_KI_HANDOFF_STARTPROMPT.md` auf der Analysevorschau.
- Nachfolgende KI: README, finalen Katalog und Handoff lesen. Keine Produktfixes wurden umgesetzt. Vor neuer Umsetzung expliziten Auftrag beachten; empfohlener Einstieg AP00–AP03. Keine Zugangsdaten aus dem früheren Chat verwenden.

## Wiederaufnahme nach Unterbrechung
Zuerst diese Datei und die weiteren Dateien in `/app/analysis/` lesen. Keine Nutzergeheimnisse aus dem Chat rekonstruieren. Den geprüften Commit und vorhandene Testresultate prüfen. Beobachtete Fehler strikt von Hypothesen und Empfehlungen trennen. Änderungen ausschließlich an Analysedokumenten und isolierten Tests außerhalb des Quellrepositories vornehmen.