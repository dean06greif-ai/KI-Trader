# KI Trader & Regime Lab: Analyse und Umsetzungsdossier

**Stand:** 15. September 2026 · **Auftrag:** ausschließlich Analyse, Tests und Planung.

**Geprüfte Quelle:** `dean06greif-ai/KI-Trader`, Branch `conflict_150926_0200`  
**Commit:** `792ff0ac44861df447d3e619042ecef1704c37c5`

## Kurzantwort auf deine wichtigste Frage

**Ja: Das Regime Lab zu einem echten historischen Forschungslabor weiterzuentwickeln ist sinnvoll. Ich würde es weder unverändert lassen noch komplett neu bauen.** Die vorhandenen Bausteine sind dafür bereits umfangreich. Entscheidend ist nicht, immer mehr Indikatoren hinzuzufügen, sondern belastbar zu prüfen, ob eine Änderung nach Kosten, auf neuen Daten und mit derselben ausführbaren Strategie tatsächlich besser ist.

Empfohlenes Zusammenspiel:

**Historische Daten → Forschungsversuch → unabhängige Validierung → freigegebene Strategieversion → Paper/Shadow → begrenzte Live-Freigabe → Überwachung.**

Der KI Trader sollte freigegebene Strategien und harte Risikogrenzen verwenden. Eine LLM-Einschätzung darf Ideen liefern, aber weder unklare Orderzustände als Erfolg behandeln noch fehlende Live-Evidenz durch selbst deklarierte „Konfidenz“ ersetzen. Ein Lab, das automatisch seine jeweils besten Rücktestergebnisse live schaltet, wäre dagegen die falsche Richtung.

## Wesentliche Ergebnisse

1. **Live-Umschaltung kann andere Regeln handeln als der Backtest.** Discovery-Regeldefinitionen werden gespeichert und angezeigt, im normalen dynamischen Apply-Pfad aber nicht ausgeführt. Außerdem bleiben alte Parameter bei Baseline-Rückkehr erhalten. Siehe R01/R02.
2. **Ein Regime-Refresh kann fremde Trades beeinflussen.** Übergangsschutz wirkt symbolweit und vor Bestätigung; der Watchdog kann eine neue fremde Position für einen Rest halten und vollständig schließen. Siehe R03/T01.
3. **Backtestkennzahlen sind in belegten Fällen falsch oder unvollständig.** Offene Endpositionen verschwinden aus PnL/Gebühren; TP1 und TPFull in derselben Kerze ergeben im Test +6 statt +4. Siehe R08.
4. **Forschung und echte Abschlusstests sind nicht konsequent getrennt.** EMA-/Kombi-Auswahl optimiert auf dem „Holdout“; Zuordnungsänderungen entwerten alte Walkforward-Ergebnisse nicht. Final-Labels gelangen in die Trainingssegmentauswahl. Siehe R05/R09/R10.
5. **Order- und Freigabestatus brauchen eindeutige Zustände.** Teilfüllungen, unbestätigter Stop-Loss, fehlende Equity und Ausnahmen in Reife-Gates werden teilweise zu optimistisch behandelt. Siehe T02–T06.
6. **Regime Lab und KI Trader besitzen verschiedene Regime-Taxonomien.** Das ist nicht grundsätzlich falsch, aber ohne expliziten versionierten Vertrag keine verlässliche gemeinsame Lernbasis. Siehe R15.

## Was ausdrücklich NICHT behauptet wird

- Nicht nachgewiesen ist, dass die aufgezeigten Fehler bei dir bereits Geld verloren haben. Aktive Produktivkonfiguration, Kontostand, Datenbankinhalte und echte Fills wurden nicht abgefragt.
- Es gibt keinen Nachweis für Profitabilität, eine bestimmte Rendite oder einen „ultimativen“ fehlerfreien Trader.
- Nicht jede der 532 Python-Dateien wurde vollständig geprüft. Die Tiefenprüfung konzentriert sich auf die relevanten Daten-, Forschungs-, Entscheidungs- und Ausführungspfade.
- Die externe Oberfläche, IBKR-/Bitunix-Integration und Live-Läufe wurden nicht end-to-end ausgeführt. Deine Anwendung wurde nicht gestartet und nicht verändert.
- R05: Der retrospektive Trainingspfad ist quelltextbelegt. Der Versuch, genau dessen Future-Append-Effekt an einem getesteten synthetischen Reactive-Datensatz numerisch zu zeigen, blieb ohne Nachweis. Das ist separat dokumentiert, nicht als bestätigter Live-Lookahead ausgegeben.

## Teststand richtig lesen

- **25 erfolgreiche gezielte Prüfungen/Charakterisierungen.** Ein bestandener Charakterisierungstest bedeutet hier häufig: Der Fehler wurde reproduziert — nicht behoben.
- **2 erwartungsgemäß scheiternde Soll-Abnahmen** für die noch falsche TP1-/TPFull-Simulation und **1 als offen markierter Forschungsversuch** (`xfail`).
- **59 zusätzliche bestehende Offline-Tests bestanden**, 3 bewusst nicht ausgeführt. Eine K-Means-Normalisierungswarnung wurde als R16 aufgenommen.
- Die zuvor separat ausgeführten 4 bestehenden Tests sind in diesen 59 enthalten und werden **nicht nochmals addiert**.
- Alle gezielten DB-/Broker-Seiteneffekte sind **TEST-DOUBLES / STUBS**, keine echten Handelszugriffe. Es gibt keine veränderte oder nachgebaute Trading-Anwendung.

## Dokumente und Lesereihenfolge

| Datei | Inhalt |
|---|---|
| [01_ARCHITEKTUR_UND_ISTBEWERTUNG.md](01_ARCHITEKTUR_UND_ISTBEWERTUNG.md) | Ist-Flüsse, Verantwortlichkeiten, vorhandene Stärken, Architektururteil |
| [02_BEFUNDE_FINAL.md](02_BEFUNDE_FINAL.md) | Priorisierter Katalog mit Quellenzeilen, Szenarien, Folgen und Behebung |
| [03_ZIELBILD_REGIME_LAB_UND_KI_TRADER.md](03_ZIELBILD_REGIME_LAB_UND_KI_TRADER.md) | Empfohlenes Labor, gemeinsame Verträge, Daten- und Freigabearchitektur |
| [04_UMSETZUNGSPLAN_FUER_KI.md](04_UMSETZUNGSPLAN_FUER_KI.md) | Kleine aufeinander aufbauende Arbeitspakete mit Abnahmekriterien |
| [05_REGRESSION_ABNAHME_MIGRATION.md](05_REGRESSION_ABNAHME_MIGRATION.md) | Testmatrix, sichere Migration, Rückfall und Abbruchbedingungen |
| [06_KI_HANDOFF_STARTPROMPT.md](06_KI_HANDOFF_STARTPROMPT.md) | Direkt verwendbarer Arbeitsauftrag für die nächste KI |
| [07_GRENZEN_ENTSCHEIDUNGEN_QUELLEN.md](07_GRENZEN_ENTSCHEIDUNGEN_QUELLEN.md) | Offene Entscheidungen, Prüfgrenzen und Fachquellen |
| [08_TESTNACHWEISE.md](08_TESTNACHWEISE.md) | Testergebnisse und genaue Reichweite der Belege |
| [09_ZWEITPRUEFUNG_EINORDNUNG.md](09_ZWEITPRUEFUNG_EINORDNUNG.md) | Unabhängige funktionale Prüfung und kritische Nachprüfung |
| [10_CODE_LANDKARTE.json](10_CODE_LANDKARTE.json) | Maschinenlesbare Modul-/Funktionslandkarte, Zeilen und Hashes |
| [00_FORTSCHRITT.md](00_FORTSCHRITT.md) | Fortlaufende Sicherung und Wiederaufnahmehinweise |
| `tests/`, `test_reports/` | Ausgelagerte Nachweistests, unveränderte Baseline-Kopien, JUnit-Berichte |

`02_BEFUNDE_ARBEITSSTAND.md` bleibt als nachvollziehbare Zwischenhistorie erhalten. Für Schlussfolgerungen gilt **02_BEFUNDE_FINAL.md**, nicht ein überholter Zwischenstand.

## Empfohlener nächster Auftrag

**Zunächst AP00–AP03 umsetzen:** Testbasis absichern, fremde Positionen vor Schutz-Automatik schützen, Teilfüllungs-/Stopzustände richtig behandeln, dynamische Umschaltung deterministisch machen. Erst danach Lab-Erweiterungen freigeben. Keine pauschale Reorganisation oder neue zweite Trading-Engine.

**Sofort unabhängig vom Code:** Die im Chat offengelegten Zugangsdaten bei den jeweiligen Anbietern widerrufen/erneuern. Sie wurden nicht in dieses Paket übernommen und nicht benutzt.