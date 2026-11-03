# 05 – Regression, Abnahme und migrationssicheres Vorgehen

## 1. Was die bisherigen Tests leisten

Die Analyse enthält echte ausgeführte Offlineprüfungen, aber keine produktive Integration. Datenbank/Broker im Nachweistest sind kontrollierte Test-Doubles. T02/T03 wurden nach der zweiten Agenteniteration verbessert: Sie extrahieren und führen jetzt den **tatsächlichen Original-Exception-/If-Branch** aus, statt nur eine nachgeschriebene Verzweigung zu testen.

Aktueller Stand:

| Lauf | Ergebnis | Einordnung |
|---|---|---|
| Gezielte Analysefälle R01–R14/T01–T05 | 25 bestanden, 3 xfail | Überwiegend bestätigte Fehlercharakterisierung; 2 noch rote Soll-Abnahmen, 1 offene Forschungshypothese |
| Fünf ausgewählte vorhandene Offline-Testdateien | 59 bestanden, 3 nicht ausgewählt | Baseline für Regimeengine, Observer/Gate, Risiko und Fingerprint |
| Frühere vier Einzeltests | 4 bestanden | Bereits in den59 enthalten; nicht addieren |

Die59 Tests zeigen, dass wichtige Grundfunktionen funktionieren. Sie widerlegen die gezielten Integrations-/Zustandsfehler nicht. Eine300-Zeilen-Unitdatei deckt keinen kompletten Brokerlebenszyklus ab.

## 2. Sichere Wiederholung

Quellpfad zeigt auf eine **separate lokale Kopie** des geprüften Commits, nicht auf einen laufenden Produktivordner. Testprozess ohne produktive Umgebungsdaten starten; keinesfalls Server-Lifespan aufrufen. Voraussetzung ist eine geeignete isolierte Pythonumgebung mit den benötigten und geprüften Test-/Projektabhängigkeiten.

```bash
export KI_TRADER_SOURCE_DIR=/pfad/zur/separaten/KI-Trader/backend
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1

python -m pytest -p anyio.pytest_plugin \
  /pfad/zum/analysepaket/tests/test_offline_extended_findings.py \
  /pfad/zum/analysepaket/tests/test_regime_dynamic_r01_r13_regressions.py \
  --confcutdir=/pfad/zum/analysepaket/tests \
  -q --tb=short --junitxml=gezielte_nachweise.xml

python -m pytest -p anyio.pytest_plugin \
  /pfad/zum/analysepaket/tests/baseline \
  --confcutdir=/pfad/zum/analysepaket/tests \
  -q --tb=short \
  -k 'not performance_on_intraday_history and not scenario_bank_all_plausible and not wiring_decision_signal_trade' \
  --junitxml=bestehende_baseline.xml
```

`tests/conftest.py` entfernt Credentialvariablen im **Testprozess**, neutralisiert dotenv und sperrt TCP-Verbindungen. Das ist eine Sicherheitsmaßnahme für diese ausgewählten Tests, keine universelle Sandbox für beliebigen Code. Für breitere Tests zusätzlich Betriebssystem-/Containernetzwerk sperren und Dateisystemrechte begrenzen.

Die kopierten Baseline-Testdateien sind unverändert; einzelne vorhandene Tests besitzen historische `/app/backend`-Suchpfade. Das gewählte Backend muss tatsächlich importiert werden (Modulpfade prüfen). Nicht blind andere Tests aus dem Quellrepository dazumischen: mehrere lesen `.env`, setzen URLs oder sprechen produktive Endpunkte an. Der originale Markermechanismus bewertet erst nach Collection.

### Nicht als „grün“ konservieren

Tests wie `test_r02_apply_configs_baseline_keeps_stale_overrides` erwarten den Fehler. Nach dessen Behebung müssen sie bewusst auf den korrekten Zielzustand umgestellt und passend umbenannt werden. Ein „Fix“, der wieder das fehlerhafte Verhalten herstellt, nur um diese Tests grün zu bekommen, wäre falsch.

Die beiden R08-Solltests sind `xfail(strict=True)`: Nach echtem Fix xfail entfernen und sie als Pflichtregression laufen lassen. Der R05-xfail ist hingegen eine **nicht geglückte Hypothesenprobe**, kein Beweis eines bestehenden Defekts und kein zu erzwingender Fehler.

## 3. Verbindliche Testpyramide für die Umsetzung

### Ebene A – Reine mathematische/zeitliche Funktionen

- Candleaggregierung, OHLC-Validität, Lücken, UTC/Sessionkalender.
- JSON-Roundtrip von Modell-/Strategieparametern, finite Zahlen und konstante Features.
- Prefix-Invarianz: festes Modell, keine Zukunft verändert vergangene kausale Features/Labels.
- Deterministischer Planresolver und totale/fehlende Regimezuordnung.
- R-/PnL-/Fee-/Qty-/FX-Umrechnung und Stoprisiko.

### Ebene B – Zustandsmaschinen mit Fake-DB und Fake-Broker

- Beobachtung vs. Apply vs. Approval, alte Version, Retry, doppelte Commands.
- Fill-/Cancel-/Schutzstatus, Racebedingungen und Restmengen.
- Idempotenter Outcome-Sync, Ledgerrevision, Fingerprint.
- Unbekannte Daten → nachvollziehbare Sperre neuer Live-Exposure.

### Ebene C – Isolierte Serviceintegration

- Lokale Testdatenbank, dedizierte Testconfigs, keine Produktionscredentials.
- Originalrouter + Test-Lifespan mit ausgeschalteten Schedulern/Brokern; nicht den echten Startprozess als Testabkürzung benutzen.
- Echte Request-/Responsevalidierung, Persistenz/Restart, Cloud-/Worker-Vertrag.
- Benutzeroberfläche gegen diese isolierte Instanz, bestehende Nutzerabläufe unverändert.

### Ebene D – Broker-Sandbox/Paper und kontrolliertes Shadow

Erst mit gesonderter Freigabe und passender Testumgebung. Brokerantworten/Fills unter realen Marktrandbedingungen prüfen; Registry-/Stopzustände und Risikoobergrenzen überwachen. Candle-Replay kann Liquidität, Filllatenz und Brokerfehler nicht vollständig ersetzen.

## 4. Abnahmematrix

| ID | Szenario | Muss gelten |
|---|---|---|
| A01 | Refresh, auto_apply aus | Keine Config-/Order-/Close-Wirkung |
| A02 | Neuer Regimezustand während offener Bestätigung | Alter Command wird nicht auf neuen Snapshot umgedeutet |
| A03 | Applyfehler nach DB-Update | Sichtbar failed/pending, sicherer Retry, kein verlorener Wechsel |
| A04 | StrategieA undB auf gleichem Asset | Nur zugehöriger Scope wird getoggelt/geschlossen |
| A05 | Paper und Live parallel | Paperereignis verändert keine Livefreigabe/Position |
| A06 | Andere fremde Position nach Bot-Close | Kein heuristischer Vollclose |
| A07 | Teilfill40/60/100% nach Timeout | Lokale Qty entspricht belegtem Fill; Unknown nicht als null/volle Menge |
| A08 | Cancel-ACK fehlt, späterer Fill | Intent bleibt rekonstruierbar, kein Doppelentry |
| A09 | StopstatusNone | Unknown sichtbar, Retry aktiv, keine neue unkontrollierte Exposure |
| A10 | Equity fehlt/veraltet | Neue Live-Risikoerhöhung abgelehnt, laufende Betreuung erhalten |
| A11 | Zwei gleichzeitig erlaubte Signals | Summe reserviertes+offenes Risiko überschreitet Gate nicht |
| A12 | A→B→Baseline | Gleicher Endzustand wie direkte Baseline, manuelle Keys erhalten |
| A13 | Gegensätzliche Discovery-Regeln | Runtime und Referenz wählen exakt dieselbe Definition |
| A14 | Regime ohne Binding | Explizit gleiche Fallbackpolitik in allen Pfaden |
| A15 | Teilkerze/Multi-TF | Keine Zukunftsverfügbarkeit; verbindlicher Kontext nur mit erlaubten Bars |
| A16 | Historie einen Monat später | Selber Datenhash/Zeitraum oder erklärter legacy-unpinned-Status |
| A17 | Korrigierte letzte Rohminute | Update korrekt übernommen, abgeschlossener Snapshot bleibt versioniert |
| A18 | Segmentgrenze | Keine Doppelzählung/Verlust einer Kerze |
| A19 | Offene Position am Testende | Unrealisiertes Ergebnis, Fees und Risiko sichtbar |
| A20 | TP1+TPFull in gleicher Bar | Teilfüllung nach definiertem Modell, Long/Short-Symmetrie |
| A21 | Gap über SL oder unklare Same-Bar-Reihenfolge | Keine garantierte perfekte Ausführung; konservativer/reporteter Fall |
| A22 | Warmup-Signal vor Testbeginn | Keine Order, aber vollständiger Indikatorzustand |
| A23 | Future append, festes Modell | Alle vergangenen kausalen Entscheidungen identisch |
| A24 | Äußerer Holdout geändert | Gewählte Modell-/Strategieparameter bleiben unverändert |
| A25 | Änderung von Regel/Cost/Regime nach Test | Alter Bericht gilt nicht mehr für aktuellen Kandidaten |
| A26 | Neue Policy, alter Trade | Alter Trade behält ursprüngliche Policy-/Exitversion |
| A27 | Outcomeupdate scheitert | Erneuter Lauf verliert nichts, Ergebnisrevision genau einmal |
| A28 | Gleiche Lektion auf denselben Trades | Keine neue unabhängige Evidenzbestätigung |
| A29 | Signal und zugehöriger Trade | Eine Gelegenheit, zwei klar getrennte Outcomearten |
| A30 | Worker andere Version/Hash | Ergebnis nicht als gültige freigegebene Evidenz übernommen |
| A31 | Gleicher Job Cloud/Worker | Reproduzierbarer Input/Output innerhalb deklarierter Toleranz |
| A32 | Worker-/Serverrestart und verspätete Antwort | Idempotenter Jobabschluss, kein überschriebenes neues Ergebnis |
| A33 | K-Means mit konstantem Feature | Finite Normierung und stabile JSON-Wiederholung |
| A34 | IBKR nur ein Leg gefüllt | Keine Behauptung vollständig gefüllter Gesamtposition |
| A35 | UI zeigt „aktiv“ | Anzeige referenziert angewandte Version, nicht bloße Beobachtung |
| A36 | Update/Archivierung/Löschen | Keine Stops/offenen Positionen verlieren ihre Betreuung |

Nicht jede dieser36 Sollabnahmen wurde im Analyseauftrag ausgeführt. Die vorhandenen Reprotests decken Teilmengen ab; fehlende Integrationstests stehen bewusst im Implementierungsplan.

## 5. Migration: additiv, beobachtbar und umkehrbar

### Schritt1 – Inventar ohne Mutation

Nach ausdrücklicher Erlaubnis: anonymisierte Schema-/Zähleraufnahme relevanter Collections/Settings. Welche dynamischen Dokumente sind aktiv? Welche nutzen Discovery/gemischte Bindings? Welche Check-/Apply-/Confirm-/Transitionmodi? Welche Workerstände? Offene Positionen nur lesen, nichts schließen.

### Schritt2 – Neue Reader/Schemafelder

- Bestehende IDs, Router und Felder behalten.
- `schema_version`, `release_id`, `model_id`, `data_revision`, `definition_hash`, `observed/applied` additiv.
- Legacy-Reader interpretieren alte Dokumente explizit. Kein neues Feld wird ohne Fallback sofort Pflicht für historische Anzeige.
- Pydantic-Responses/JSON-Cleaning prüfen; Mongo-`_id`/ObjectId nicht ungefiltert serialisieren. Inserts können Eingabedicts mutieren: Rückgaben separat/validiert bilden.

### Schritt3 – Backfill mit Dry-run

- Batchweise, wiederholbar, Fortschrittsmarke und Before-/After-Checksum.
- Alte ungebundene Datenstände als unbekannt markieren, keine historische Validierung erfinden.
- Discoverydefinitions-/Mappingauflösung prüfen, Konflikte nicht automatisch zuordnen.
- Fehlende Ownership darf keinen pauschalen Trade-/Overridebesitz erzeugen.
- Keine produktiven Daten löschen, keine riesige All-at-once-Migration im Serverboot.

### Schritt4 – Dual-Read/Shadowvergleich

Alter und neuer Resolver lesen denselben Eingang; neuer Pfad zunächst ohne Schreib-/Orderrechte. Unterschiede speichern und fachlich erklären. Kein Dual-Write an den Broker, keine parallel konkurrierenden Trade-Engines.

### Schritt5 – Eingrenzbare Aktivierung

Featureflag pro Release/Scope und erwarteter Version. Zunächst Test/Paper, erst später ausdrücklich genehmigter enger Livebereich. Nicht alle Assets und Moduspfade gleichzeitig umstellen.

### Schritt6 – Veraltete Wege erst später entfernen

Nach vollständigem Referenzinventar und Beobachtungsperiode alte Writes stilllegen. Reader historischer Trades/Analysen behalten. Große Modulverschiebungen erst bei stabiler Semantik, nicht als Bedingung des Bugfixes.

## 6. Rückfall ist mehr als „alten Code starten“

- Freigabeflag deaktivieren, neue risikosteigernde Entries pausieren.
- Geöffnete Positionen behalten ihre Exit-/Schutzbetreuung und ursprünglichen Artefactreferenzen.
- Alten freigegebenen Plan nur verwenden, wenn Daten-/Broker-/Risikolage dafür gültig ist. Bekannte gefährliche Altlogik nicht wieder aktivieren.
- Eigene dynamische Overrides nach vorherigem Snapshot und Ownership zurücknehmen; keine zwischenzeitlichen Benutzeränderungen überschreiben.
- Unaufgelöste Intents/Teilschritte vor Wiederaufnahme reconciliieren; keine Wiederholung unbekannter Orders.
- Datenmigration nur zurückrollen, wenn keine neuen abhängigen Daten verloren gehen. Bevorzugt kompatible Reader statt destruktiver Rückmigration.

## 7. Abbruchbedingungen

Falsche Positions-ID, Qtyabweichung außerhalb erlaubter Rundung, Unknown-Schutz ohne aktive Wiederherstellung, zu alte Equity, fehlender Dataset-/Releasehash, fremde Scopewirkung, unerklärte Live-/Replay-Signalabweichung, nicht versionierter Modellwechsel, doppelte Fills/Orders oder widersprüchliche freigegebene Konfiguration.

Reaktion ist nicht automatisch „alle Positionen zum Markt schließen“. Neue Entries stoppen, bestehende Schutzmechanismen erhalten, konkrete Broker-/Positionslage aufklären und die vorher festgelegte Notfallpolicy ausführen.