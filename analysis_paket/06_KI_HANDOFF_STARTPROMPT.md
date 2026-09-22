# 06 – Kopierbarer Startauftrag für die nächste KI

Der folgende Text ist für eine **später ausdrücklich beauftragte Implementierung** gedacht. Die vorliegende Analyse hat keine Produktfixes vorgenommen.

---

Du arbeitest an einer bereits produktiv und extern laufenden Daytrading-Anwendung. Sie soll extern bleiben. Stabilität, Rückwärtskompatibilität und ursprüngliche Ordner-/Dateistruktur haben Vorrang. Kein kompletter Neubau und kein paralleles zweites Trading-System.

## Quelle

- Repository: https://github.com/dean06greif-ai/KI-Trader
- Analysierter Branch: `conflict_150926_0200`
- Analysierter Commit: `792ff0ac44861df447d3e619042ecef1704c37c5`
- Analysestand: 15.09.2026.

Beginne damit, den aktuellen Commit mit diesem Stand zu vergleichen. Die Fehlerliste ist kein Beweis, dass jeder Fehler nach späteren Änderungen noch besteht.

## Pflichtlektüre

1. `README.md` des Analysepakets.
2. `01_ARCHITEKTUR_UND_ISTBEWERTUNG.md`.
3. `02_BEFUNDE_FINAL.md`.
4. `04_UMSETZUNGSPLAN_FUER_KI.md`.
5. `05_REGRESSION_ABNAHME_MIGRATION.md`.
6. `08_TESTNACHWEISE.md` und die Tests.

`02_BEFUNDE_ARBEITSSTAND.md` ist historische Zwischenarbeit, nicht maßgeblicher Schlussbericht. Die generische Arbeitsumgebungs-Vorschau ist NICHT die externe Anwendung. Frühere Bemerkungen über deren Startseite sind keine Fehlerbefunde des Produktes.

## Ziel

Das vorhandene Regime Lab soll langfristig ein reproduzierbares Forschungslabor werden: Indikatoren/Features, Regimekontext und Strategien auf festen historischen Datensätzen testen, unabhängige Evidenz erzeugen und versionierte Strategiereleases bereitstellen. Der KI Trader konsumiert freigegebene Artefakte innerhalb harter Risikogrenzen. Ein LLM darf Vorschläge machen, aber keine unbekannten Order-/Stopzustände oder fehlende Live-Reife überstimmen.

## Erster beauftragbarer Abschnitt

Implementiere **zunächst nur AP00–AP03**, sofern der Nutzer diesen Abschnitt tatsächlich freigegeben hat. AP02 in kleine Teiländerungen aufteilen. Nicht ungefragt den gesamten Backlog abarbeiten oder live aktivieren.

1. Sichere Offline-Test-/Vertragsbasis.
2. Ownership-/Scopefehler verhindern: Watchdog-Fremdpositionsclose und symbolweite Regime-Nebenwirkungen.
3. Tatsächliche Fillmenge, dreistufiger Schutzstatus und konservative Unknown-Risikobehandlung.
4. Ein deterministischer Resolver für alle dynamischen Strategie-/Parameterbindungen.

## Kritische aktuelle Befunde

- **T01:** `position_watchdog._adopt` kann nach einem kürzlichen Bot-Close eine andere fremde Position gleicher Seite vollständig schließen. Positions-ID/Mengenbeleg erforderlich.
- **R03:** `transition_protect` wirkt symbolweit und vor Freigabe. Refresh muss ohne Handelswirkung sein.
- **T02:** Market-Timeout-Recovery kann bei60% Fill100% geplante Menge verbuchen. Maker-Pfad verhält sich hier anders; nicht blind vereinheitlichen ohne Tests.
- **T03:** `_ensure_live_sl=None` hinterlässt `sl_exchange_missing=False`. Unknown ist weder bestätigt noch sicher fehlend.
- **T06:** Fehlende Equity/SLdaten können als bestandene Budgetprüfung bzw.0 Risiko gelten.
- **R01:** Discovery-Substrategiedefinitionen werden im normalen Dynamic-Apply nicht ausführend umgeschaltet; Backtest und Live können andere Regeln verwenden.
- **R02:** Leere/partielle Folgeconfigs lassen alte dynamische Parameter zurück.

Spätere P1-Punkte: stale Last-State/Confirm, veraltete Validierungen, festes Dataset, echte Positionserhaltung in Backtests, Holdouttrennung, Regimevertrag, vollständige Policy-/Outcomeprovenienz und Worker-Paketparität.

## Unverhandelbare Sicherheits-/Arbeitsregeln

- Keine Zugangsdaten aus dem früheren Chat benutzen, speichern oder ausgeben. Diese müssen außerhalb deiner Arbeit erneuert werden.
- Keine bestehenden `.env` laden, keine Produktionsdatenbank anbinden, keine Broker-/LLM-Calls, kein Server-Lifespan starten, solange nicht gesondert für eine geeignete Testumgebung erlaubt.
- Keine Live-Orders auslösen, keine Positionen schließen/stornieren, keine produktiven Configs oder Migrationen ändern.
- `backend/`, `frontend/`, `local_worker/` und bestehende Start-/Import-/API-Verträge behalten.
- Neue Fachmodule über bestehende Facades einbinden. Der Worker-ZIP-Builder verpackt aktuell nur unmittelbare `.py`-Dateien: neue Unterpakete brauchen explizite Paket-/Manifesttests.
- Fachfehler zunächst minimal fixen, Refactoring getrennt. Kein Redesign zusammen mit Trading-Korrekturen.
- DB-/Broker-Unbekannt ist ein eigener Zustand, kein Erfolg. Bei Unsicherheit neue Exposure beschränken; laufende Positionsbetreuung nicht abstellen.

## Testregeln

- Jede Änderung erhält einen reproduzierenden Test und dann eine Soll-Abnahme.
- Bestehende Analyse-Charakterisierungstests erwarten teilweise den Fehler. Nach Fix gezielt auf korrektes Verhalten umstellen, nicht fehlerhaftes Verhalten restaurieren.
- Die Analyse hatte25 bestandene gezielte Checks,3xfail und59 zusätzlich bestandene bestehende Offline-Tests. **Das bedeutet nicht, die Anwendung sei fehlerfrei.**
- R05-Future-Append-Effekt auf dem getesteten Reactive-Datensatz wurde nicht numerisch bewiesen; Live-Prefixstabilität dagegen schon. Keine pauschale Behauptung, jede Live-Regimeberechnung benutze Zukunftsdaten.
- Für komplette Orderflows reicht AST-Branchprüfung nicht. Nach Minimalfix volle Zustandsmaschine mit Fake-Broker/Fake-DB und später geeigneter freigegebener Sandbox prüfen.
- Bei jeder Scope-/Statekorrektur zwei Strategien, zwei Modes, zwei Assets, doppelte Aufrufe und Restart testen.

## Berichtspflicht nach jedem kleinen Paket

Fortschrittsdatei aktualisieren mit:

```text
Paket / aktueller Commit:
Bezug auf Befund-IDs:
Geprüfte Call-Sites und unveränderte Verträge:
Geänderte Dateien:
Geändertes Verhalten und Migrationswirkung:
Ausgeführte Tests / Rohberichte:
Bekannte Grenzen / nicht ausgeführte Tests:
Rückfall-/Abbruchverhalten:
Nächster zulässiger Schritt:
```

Keine Freigabe eines Pakets allein wegen syntaktisch erfolgreichem Import. Keine Behauptung realer Profitabilität aus historischen Tests. Ziel ist eine nachvollziehbare, sichere, wartbare Grundlage — nicht maximaler Funktionsumfang.

---

## Vorschlag für deine kurze Nachricht an die nächste KI

„Lies dieses Analysepaket vollständig. Ich beauftrage zunächst AP00–AP03 gemäß Plan, ausschließlich in einer separaten Entwicklungs-/Testkopie. Analysiere vor jedem Fix den aktuellen Code, halte die Originalstruktur und öffentliche Verträge ein, erstelle Regressionen und dokumentiere jeden Schritt. Keine Produktivverbindung, keine Live-Orders und keine eigenmächtige Aktivierung. Zeige nach diesem Abschnitt Ergebnisse und offene Risiken, bevor du die nächste Phase beginnst.“