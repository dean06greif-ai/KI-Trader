# Gesamtbericht: KI Trader und Regime Lab

<!-- Quelldokument: README.md -->

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

---

<!-- Quelldokument: 01_ARCHITEKTUR_UND_ISTBEWERTUNG.md -->

# 01 – Bestehende Architektur und Bewertung

Basis: Commit `792ff0ac44861df447d3e619042ecef1704c37c5`. Pfade relativ zum Repository; Zeilennummern beziehen sich auf diesen Stand. Bewertung ist qualitativ, nicht Ergebnis eines Profitabilitätsbenchmarks.

## 1. Architektur nicht ersetzen, Verantwortlichkeiten klären

Die Anwendung ist kein einfacher Prototyp: 532 Python-Dateien im Backend, 280 `test_*.py` unter `backend/tests`, React/CRACO-Frontend, zahlreiche Router/Services, lokaler Rechenworker und getrennte Brokeradapter. `server.py` ist mit 474 Zeilen wesentlich kleiner als zentrale Fachmodule. Eine Router-/Core-/Service-Trennung existiert bereits.

Problematisch ist weniger der Verzeichnisname als die Verteilung von Verantwortung: Regime werden an mehreren Stellen neu berechnet, Einstellungen über globale veränderliche Objekte weitergereicht und Zustandswechsel aus Beobachtung, Anwendung, Freigabe und Orderwirkung vermischt. Einzelne Schutzregeln existieren, ihre Fehler-/Ausnahmezustände bilden aber keinen durchgängigen Vertrag.

### Wesentliche Module

| Bereich | Vorhandene Verantwortliche | Erhalten / gezielt verbessern |
|---|---|---|
| Start und Verkabelung | `backend/server.py`, `core/state.py`, `core/scheduler.py` | Öffentliche Struktur und Imports erhalten; Seiteneffekte erst bei bewusster Initialisierung |
| Signale | `core/pipeline.py`, `services/strategy_scanner.py` | Gemeinsame Pipeline erhalten; vollständige Provenienz und abgelehnten Zustand mitführen |
| Regime-Fassade | `services/regime_core.py`, `services/regime.py` | Bestehende Fassade als stabile Schnittstelle benutzen, keine neue Parallel-Engine |
| Regime-Modelle | `regime_engine.py`, `regime_features.py`, `regime_reactive.py`, `regime_kombi.py`, `regime_truth.py` | Kausale Klassifikation getrennt von diagnostischer Rückschau |
| Labor | `routers/regime_lab.py`, `services/regime_lab.py`, `regime_opt.py` | Orchestrierung von Daten, Modellen, Suche und Validierung entkoppeln |
| Dynamische Strategien | `dynamic_strategy.py`, `dynamic_live.py`, `routers/dynamic.py` | Derselbe versionierte Strategieplan in Forschung und Ausführung |
| Historie | `history_sources.py`, `candle_cache.py`, `candles.py`, `timeframes.py`, `core/instruments.py` | Markt-/Brokeridentität, feste Datenstände, Kerzenabschluss |
| Backtest | `backtester.py`, `fast_sim.py`, `fast_signals.py`, `simulation_pool.py` | Prüfreferenz festlegen; Beschleunigung muss nachweislich gleiche Semantik besitzen |
| KI-Entscheidung | `ai_engine.py` plus Context/Governance/Housekeeping | Reine Entscheidung, Freigabe und Orderausführung separat halten |
| KI-Marktregime | `ai_market_observer.py` | Kurzfristigen Kontext erhalten, explizit gegenüber Lab-Regime benennen/versionieren |
| Risiko/Schutz | `entry_guard.py`, `entry_checks.py`, `risk_budget.py`, `position_sizing.py`, `trade_guard.py` | Gute vorhandene Bausteine härten; keine zweite Guard-Kette einführen |
| Ausführung | `bitunix_trade.py`, `ibkr_trade.py`, `entry_order_registry.py`, `entry_inflight.py` | Broker-Fills und Identität statt Annahmen als Wahrheit |
| Abgleich | `position_watchdog.py`, `pnl_reconcile.py` | Schutz gegen fremde Positionen, idempotente Ledger-/Outcome-Aktualisierung |
| Lernen | `ai_learning.py`, `ai_validation.py`, `ai_playbook.py`, `setup_lifecycle.py`, `setup_backtest/` | Echte unabhängige Evidenz statt bloßer Wiederholung von LLM-Aussagen |
| Policy-Forschung | `policy_lab.py`, `policy_promotion.py`, `policy_fingerprint.py`, `ml_gate.py` | Vorhandene Champion-/Challenger- und Versionsideen erweitern |
| Worker | `local_worker/worker.py`, `services/local_exec.py`, `routers/local_worker.py` | Gemeinsamer Berechnungscode, Paket- und Ergebnisversionen absichern |
| Oberfläche | `RegimeLab.js`, `DynamicPanel.js`, `AITradingPanel.js` | Bestehende Bedienabläufe und visuelle Sprache erhalten; Statuswahrheit verbessern |

## 2. Tatsächlicher Laborfluss

```text
RegimeLab.js
  → /api/regime-lab/analyze
  → regime_lab.run_analysis
      fetch_histories → backtester.fetch_history → candle_cache/history_sources
      aggregate_candles
      train_hist = vorderer Prozentanteil pro Asset
      regime_core/regime.detect_regimes(train_hist)
      _symbol_payload(model, GESAMTE Historie)
          live_labels  → live_segments und aktuelle Sicht
          final_labels → segments, Phasenstatistik, Rückschau
      Mongo: regime_lab_analyses

  → /optimize
      regime_opt.run_regime_optimizer
      regime_ranges(... only_train=True) liest gespeicherte segments
      Strategien/Parameter suchen und ggf. Discovery-Definitionen erstellen
      Mongo: regime_lab_runs / Zuordnungen

  → /walkforward
      Daten erneut relativ zu jetzt laden, am alten Enddatum abschneiden
      kausale Live-Labels, nur nach train_end_ts handeln
      eval_dynamic vs. statische Basis; segmentweise Simulation
      Verdict nach Mindesttrades/PnL/DD

  → /build
      dynamisches Dokument + ggf. erste Custom-Strategie
      Mongo: dynamic_strategies, custom_strategies
```

**Positiv:** Regime-Modellfit und abschließende kausale Klassifikation sind bereits getrennt vorgesehen. Die Oberfläche kennt ausdrücklich Final- und Live-Bänder. Auto-Check/Auto-Apply neuer dynamischer Strategien starten deaktiviert. Kombinierte und assetspezifische Analysen existieren.

**Brüche:** Datenstände sind nicht fest; Final-Segmente werden für Suche weiterverwendet; „Holdout“ wird teilweise selbst zur Auswahl optimiert; gespeicherte Zuordnungen und Freigaben besitzen keinen gemeinsamen unveränderlichen Fingerprint; Segment-Simulation verliert offene Positionen.

## 3. Tatsächlicher Dynamic-Live-Fluss

```text
dynamic_live.run_loop / manueller refresh
  → refresh_state: Historie und Modellklassifikation
  → log_switches / _switched_symbols
  → transition_protect (ggf. block_new / close_open)
  → last_state speichern
  → optional pending_switch ODER apply_active

apply_active
  ├─ regime_strategies vorhanden → Strategie-Toggles + Parameter/Trade-Overrides
  └─ andernfalls → nur Trade-Overrides der Basisstrategie
```

`sub_strategies` ist im zweiten Pfad keine ausführende Auswahl. Die Anzeige kann daher einen anderen Eindruck vermitteln als der tatsächliche Registry-Aufruf. `last_state` ist Erkennung, nicht sichere Bestätigung erfolgreicher Anwendung; genau diese Unterscheidung fehlt.

## 4. Tatsächlicher KI-/Trade-Fluss

```text
Scanner-Kerzen und MarketObserver
  → AIEngineContext: Markt-/Asset-/Setup-/Lernkontext
  → konfigurierter LLM, ggf. Provider-/Modell-Fallback
  → Antwort parsen, Entscheidungen und policy_version speichern
  → AIEngine._apply_decision:
      Alter / Ausführbarkeit / Setup / Confidence / Master-Regeln
      ggf. Wiederbewertung, ML-Gate, Duplikatprüfungen
      Maker-/Limit-Plan oder sofortiges Signal
  → core.pipeline.process_signal
      Schalter / Coin-Strategie-Mode / Signal und Benachrichtigungen
  → AutoTradeManager.on_signal / _on_signal_impl
      zentrale Entry-Guards, Kosten, Kapital/Risiko
      Brokerroute oder Paper
      Order → Fills / Schutzorders → lokaler Trade
  → Monitor / AITradeManager / PositionWatchdog / PnL-Abgleich
  → AILearning.sync_outcomes → Statistiken / Lektionen / Policy-Experimente
```

Das ist bereits ein umfassendes System. Es fehlen nicht pauschal „Stop-Loss“, „Risikomanagement“, „Lernmodule“ oder „Deduplizierung“. Diese sind vorhanden. Kritisch sind Teilzustände: unbekannter Fill, unbekannter Schutzstatus, überalterte Evidenz, unterschiedliche Regime-Domäne, fehlende Equity und Fehler zwischen zwei Datenbankoperationen.

### Wichtige vorhandene Stärken

- `process_signal:79–87` synchronisiert Coin-/Strategie-Modus aus DB in den Cache, um unterschiedliche Sicht auf Paper/Live zu vermeiden.
- `ai_engine:2112–2118` prüft veraltete KI-Entscheidungen; Collection-Daten sind in zentralen Pfaden von normalen Trades getrennt.
- `entry_guard` bündelt Regeln; `risk_budget` kennt Portfolio-/Assetklassenlimits; `position_sizing` bietet risikoabhängige statt ausschließlich fixe Margen.
- `entry_order_registry` und `entry_inflight` existieren bereits für späte Fills und Adoption-Rennen.
- Der Watchdog prüft Registry-Zuordnung VOR dem gefährlichen Restpfad. Das reduziert den Fehlerbereich, beseitigt ihn aber nicht.
- `ai_learning:282–319` unterscheidet Signal-Touch und echten Trade-PnL; ein kanonischer Trade soll ein bloßes TP1-Touch-Label überschreiben.
- Governance kennt Whitelists, `suggest`/`auto`, Master-Sperren, Mindestdatenbasis und Bestätigung für Makroänderungen.
- `policy_promotion` besitzt Mindestbeobachtungen, Bootstrap-Differenz und Drawdown-Vergleich; `policy_fingerprint` erfasst mehrere zentrale Policy-Bestandteile.
- Backtest-Boost für Setup-Reife ist gedeckelt und setzt profitable echte Paper-Trades voraus (`setup_backtest/weights.py:19–46`). Nicht fälschlich behaupten, ein reiner Backtest genüge immer.
- Bestehende Offline-Regimetests prüfen Richtung, Hysterese, Prefix-Stabilität, Gate-Domäne und Legacy-Kompatibilität.

## 5. Qualitatives Urteil

| Dimension | Urteil | Konsequenz |
|---|---|---|
| Funktionsumfang | Stark / sehr umfangreich | Nicht weitere Module bloß zum Umfang hinzufügen |
| Modulare Grundstruktur | Brauchbar, aber Fachgrenzen verwischt | Adapter und reine Kerne unter bestehenden Pfaden |
| Forschungs-Reproduzierbarkeit | Unzureichend abgesichert | Feste Datenstände und Versionsbindung zuerst |
| Backtest-/Live-Parität | Belegte kritische Brüche | Keine neue automatische Promotion vor Parität |
| Ausführungssicherheit | Viele gute Schutzmodule, relevante Ausnahmefehler | Priorität auf Identität und unbekannte Zustände |
| Lernqualität | Gute Provenienzansätze, gemischte Evidenz und Bestätigungen | Einheit des Lernens = versioniertes, unabhängiges Ergebnis |
| Langfristiges Potential | Gut bei schrittweiser Härtung | Ja zum Labor, nein zum kompletten Neubau |

## 6. Was nicht ohne Weiteres gelöscht werden sollte

Legacy-K-Means, bisherige Router, alternative Brokerpfade, Fallbacks für Altdokumente und existierende Strategien können noch durch gespeicherte Daten oder externe Workflows referenziert sein. Ein reines Text-Suchergebnis beweist bei dynamischen Imports/Registry-Schlüsseln keine Nichtnutzung.

Entfernung erst nach Nutzungs-/Referenzinventar, Migrationsadapter, Regressionen und deklariertem Deprecation-Zeitraum. Historische Audit-/Prototypskripte können später dokumentiert archiviert werden; ihre Existenz ist kein Grund, ausführbaren Fachcode jetzt umzubauen.

## 7. Struktur- und Betriebsverträglichkeit

`backend/`, `frontend/`, `local_worker/` sowie Startbefehle und Umgebungsvariablennamen bleiben unverändert. Keine neue Datenbank, kein Austausch des Frontend-Buildsystems, kein parallel laufender Order-Service als Schnelllösung. Der Serverstart führt Migrationen aus und startet reale Hintergrundloops; deshalb wurde er in dieser Analyse ausdrücklich nicht ausgeführt.

Besonders wichtig für künftige Modulaufteilung: Das Worker-ZIP sammelt gegenwärtig nur `*.py` direkt unter `core/services/strategies/models` (`routers/local_worker.py:233–240`). Neue Unterpakete dürfen nicht eingeführt werden, ohne den Paketvertrag mitzuziehen. Das ist ein Wartbarkeitsrisiko, kein in dieser Prüfung nachgewiesener Totalausfall des bestehenden Workers.

---

<!-- Quelldokument: 02_BEFUNDE_FINAL.md -->

# 02 – Finaler Befundkatalog

**Quelle:** Commit `792ff0ac44861df447d3e619042ecef1704c37c5` · 15.09.2026. Alle Pfade relativ zum Quellrepository.

## Leseschlüssel

- **P0:** Vor neuer unüberwachter Live-Automatisierung klären/beheben; mögliche fremde Orderwirkung oder unmittelbare Unzuverlässigkeit von Exposure/Schutz.
- **P1:** Vor Verwendung der betroffenen Forschungsergebnisse/Freigaben korrigieren.
- **P2:** Wartbarkeit, Statusklarheit oder weitergehende methodische Verbesserung.
- **E1:** Originalfunktion/-branch offline mit synthetischen Daten und/oder Fake-Abhängigkeiten ausgeführt.
- **E2:** Quelltext-/Datenflussbeleg, ggf. statischer Test; kein vollständiger Laufzeitnachweis.
- **E3:** Methodischer/architektonischer Risikobefund oder offene Hypothese. Keine Behauptung tatsächlich eingetretener Produktivfolgen.

„Test bestanden“ kann heißen: **Der Fehler ist reproduziert.** Es wurden bewusst keine Produktfixes vorgenommen.

## A. Regime Lab und dynamische Strategien

### R01 · P0 · Gespeicherte Discovery-Regeln werden live nicht umgeschaltet · E1/E2

**Quelle:** `backend/routers/regime_lab.py:389–438`; `services/dynamic_live.py:107–108,160–185,273–278`; `services/regime_opt.py:451–479` (alle Servicepfade unter `backend/`).

**Szenario:** Regime A erhält eine Discovery-Definition mit Trend-Regeln, B eine andere mit Range-Regeln. Build registriert die erste Definition als Basis, speichert die weiteren unter `sub_strategies`. Apply ohne vollständiges `regime_strategies` wendet lediglich Trade-Configs an. Der Test bestätigt, dass keine Regimedefinition ausgeführt/übernommen wird. Der Backtest baut dagegen Strategien aus den Definitionen.

**Folge:** „Regime B aktiv“ kann angezeigt werden, obwohl weiterhin Regeln von A signalisieren. Eine gemischte Registry-/Discovery-Zuordnung gelangt ebenfalls nicht zuverlässig in den vollständigen Multi-Strategiepfad.

**Korrektur:** Pro Regime einen unveränderlichen ausführbaren `StrategyBinding` auflösen, Registry- und Discovery-Kandidaten gleich behandeln; einziger Resolver für Simulation und Runtime. Keine Live-Regeln aus reinem Anzeigetext ableiten. Bestehende Basisstrategie nicht global überschreiben.

**Abnahme:** A erzeugt auf Fixture ein Signal, B nicht; nach Umschaltung entspricht Live exakt dem B-Backtest. Gemischte Bindings, gleiche Registry-ID mit verschiedenen Parametern und zwei Assets isoliert testen.

### R02 · P0 · Baseline-Rückkehr und Partial-Overrides vererben alte Werte · E1

**Quelle:** `backend/services/dynamic_live.py:171–185,207–218,245–257`.

**Szenario:** Erst `{leverage:5,tp1_crv:2}`, dann `{}`. Apply meldet `baseline=True`, speichert aber die alten Overrides weiter. Eine nächste Teilkonfiguration ersetzt ebenfalls nur enthaltene Keys.

**Folge:** Identisches Regime kann je nach vorherigem Regime mit anderen Werten handeln. Dies betrifft Trade-Parameter und Strategieparameter.

**Korrektur:** Vollständigen effektiven Zustand aus Basissnapshot + persönlichem Override + freigegebenem Regimepatch berechnen. Dynamisch besessene Keys explizit entfernen/resetten; manuelle nicht überschreiben. Ownership und Versionsprüfung nötig.

**Abnahme:** A→B→Baseline und direkt Baseline liefern dieselbe Konfiguration; keine Werte fremder Strategien/Modes gehen verloren.

### R03 · P0 · Übergangsschutz ist symbolweit und wirkt vor Freigabe · E1

**Quelle:** `backend/services/dynamic_live.py:294–328,340–367`; `services/trade_guard.py:257–266`; `routers/dynamic.py:109–117`.

**Szenario:** Eine dynamische Strategie wechselt Regime auf BTC. `close_open` sucht alle offenen BTC-Trades, ohne Strategy-/Dynamic-/Mode-Scope. `check_one` führt den Schutz auch bei `auto_apply=False` und vor `require_confirmation` aus. Symbolbezogene Locks blockieren auch fremde Strategien/Modes.

**Folge:** Der vermeintliche Refresh/Bestätigungsvorschlag kann andere Positionen schließen oder deren Entries sperren.

**Korrektur:** Beobachtung strikt ohne Handelswirkung; eine explizite scoped Transition-Command aktiviert Schutz. Standardmäßig nur nachweislich zugehörige Trades erfassen. Falls globale Notbremsen gewünscht sind, separate benannte Funktion und gesonderte Bestätigung.

**Abnahme:** Zwei Strategien und Paper/Live auf BTC; nur der freigegebene Scope wird verändert. Refresh, Vorschau, Dismiss und unbestätigter Wechsel schließen nichts.

### R04 · P1 · Fehlgeschlagenes Apply wird nach gespeichertem `last_state` nicht wiederholt · E1

**Quelle:** `backend/services/dynamic_live.py:349–367`.

**Szenario:** Erkennung wird gespeichert, Apply wirft einen Fehler. Nächster Zyklus erkennt keinen Wechsel mehr und versucht Apply nicht erneut.

**Korrektur:** `observed_state`, `desired_state`, `applied_state` und `application_status` getrennt persistieren. Idempotente Apply-Version mit Retry; keine Behauptung, eine reine andere Reihenfolge löse Broker-/DB-Atomizität vollständig.

**Abnahme:** Fehler nach jedem Apply-Schritt injizieren; Restart/Retry liefert denselben Zielzustand ohne doppelte Orderwirkung.

### R05 · P1 · Retrospektive Final-Labels gelangen in die Trainingssegmentauswahl · E2 + begrenzter E1

**Quelle:** `backend/services/regime_lab.py:255–279,890–910`; `regime_opt.py:79–95,165–173,421–433`; `regime_reactive.py:129–165,203–208`; `regime_kombi.py:170–174,236–243`.

**Belegt:** Lab speichert `segments` aus Final-Labels auf der vollständigen Historie. Suche liest diese und schneidet lediglich Zeitgrenzen ab. EMA/Kombi-Final berechnen explizit zentrierte Steigungen; rückblickende Kurzphasenbereinigung ist keine Online-Klassifikation. Das spätere Walkforward nutzt immerhin kausale Live-Labels.

**Grenze:** Der Test der Verkabelung stubbt den Labeler. Eine zusätzliche numerische Reactive-Probe bestätigte die kausale Prefix-Stabilität, fand aber keine Änderung des gewählten Final-Präfixes. **Keine konkrete produktive Trainingskontamination und kein Lookahead der Live-Klassifikation bewiesen.**

**Folge/Risiko:** Retrospektiv günstige Phasenzuordnung kann Suchergebnisse verbessern, die live so nicht erreichbar sind; bei Nähe zum Trainingsende kann spätere Information die Trainingsphase beeinflussen.

**Korrektur:** `causal_regime` für handelbare Forschung/PnL; `retrospective_reference` ausschließlich Diagnose. Wenn Reference-Labels für ein beaufsichtigtes Lernziel verwendet werden, deren Vorwärts-Labelhorizont ausdrücklich erfassen und purgen. Kein pauschales Entfernen des hilfreichen Final-Bandes.

### R06 · P1 · Gespeicherte Analyse ist später nicht auf demselben Datenfenster reproduzierbar · E1

**Quelle:** `backend/services/regime_lab.py:55–78,407–427`; `regime_opt.py:79–86,422–425`.

`fetch_history` wird mit `days` relativ zu jetzt aufgerufen; alter `end_ts` schneidet nur hinten ab. Mit fortschreitender Zeit wandert der Beginn nach vorn. Offline-Quelle reproduziert schrumpfende Historie bis zum Unterschreiten der Mindestmenge.

**Korrektur:** Unveränderlicher Datensatz/Snapshot mit Start, Ende, Quelle, Marktart, Candle-Checksum und Qualitätsbericht. Bestandsanalysen als `legacy_unpinned` markieren, nicht rückwirkend neue Daten als alten Test verkaufen.

### R07 · P1 · Segmentgrenzen zählen die erste Kerze der Folgephase doppelt · E1

**Quelle:** `backend/services/regime_lab.py:120–126,914–925`.

`to_ts` erhält die Kerze am exklusiven Endindex `e`, der Leser nutzt `bisect_right`. Zwei >=10-Bar-Segmente teilen dadurch eine Kerze.

**Korrektur:** Einheitlich halboffene Intervalle `[start,end)` und explizites `end_exclusive_ts`; Altdokumente über Schemaadapter lesen.

**Abnahme:** Rekonstruierte Zuordnung hat jede relevante Kerze genau einmal; erster/letzter Abschnitt, Marktpausen und Warmup geprüft.

### R08 · P1 · Offene Endpositionen fehlen; TP1+TPFull werden zu optimistisch abgerechnet · E1

**Quelle:** `backend/services/backtester.py:347–370,404–460,462–535`; `dynamic_strategy.py:12–13,237–247`.

1. `open_t` am Daten-/Segmentende fließt nicht in Rückgabe/PnL/Fees ein. Docstring behauptet Schließen am Regimewechsel, Implementierung tut es nicht. Ein Trade mit schwebendem Verlust und Entryfee kann als `trades=0,pnl=0,fees=0` erscheinen.
2. Bei TP1 und TPFull in gleicher Kerze unterbleibt TP1, gesamte Restmenge schließt zum TPFull. Fixture Long: Entry100, Qty2, TP1=101, TPFull=103, 50% Teilverkauf, kein SL, Fee0: **+6 statt +4**. Short spiegelbildlich gleich.
3. Warmup-Trades werden erst nach Simulation gefiltert; sie können einen Entry im eigentlichen Segment verhindern. Das ist ein weiterer Quellrisikobefund, separat zu testen.

**Korrektur:** Zeitlich kontinuierlichen Positionszustand über Regimewechsel führen; offene Position am Testende mark-to-market oder explizit nach vereinbartem Modell liquidieren, stets samt Gebühren/offenem Risiko ausweisen. Warmup nur zum Indikatoraufbau, `entry_allowed_from` verhindert vorherige Orders. Same-Bar-TP-Sequenz und Stop-Priorität verbindlich festlegen; keine sichere Tickreihenfolge aus OHLC erfinden.

**Abnahme:** Goldene Long-/Short-/Gap-/Teilfill-/Same-Bar-Szenarien für Referenz, Fast-Sim und Paper; Differenzen entweder null oder dokumentierte Modellgrenzen.

### R09 · P1 · Build/Apply ist nicht an eine aktuelle bestandene Validierung gebunden · E1/E2

**Quelle:** `backend/routers/regime_lab.py:340–371,376–448`; `routers/dynamic.py:120–174`; `frontend/src/components/RegimeLab.js:561–568,650–657,1236–1238`.

Assign/Keep ändern das Experiment ohne alten Walkforward als veraltet zu markieren. Build übernimmt ein vorhandenes Verdict, ohne einen passenden bestandenem Fingerprint zu erzwingen. Build als ENTWURF ohne Test darf sinnvoll bleiben; problematisch ist die spätere Verwechslung mit freigegebenem Live-Artifact.

**Korrektur:** `draft`, `validation_required`, `validated`, `approved`, `retired`; jede semantische Änderung erzeugt neue Revision. Nur aktueller serverseitig nachgerechneter Befund berechtigt zur Freigabe; clientseitig gesendete Metriken nie als Beweis akzeptieren.

### R10 · P1 · „Holdout“ wird selbst optimiert · E2

**Quelle:** `backend/services/regime_lab.py:531–535,638–665`; `frontend/src/components/RegimeLab.js:838,1147`.

EMA-Bestperiode wird nach `holdout_direction_pct` gewählt; Kombi-Score enthält dasselbe Maß. Dieses Fenster ist damit Auswahlvalidierung, kein unangetasteter abschließender Test. Wiederholte manuelle Suche auf demselben sichtbaren Holdout verstärkt das Problem.

**Korrektur:** Train/innere Validierung/äußerer Test plus einmaliges Lockbox-Fenster; jeden Versuch mitzählen. Regime-Übereinstimmung ist Diagnose und keine PnL-/Edge-Metrik. UI-Bezeichnung der tatsächlichen Verwendung anpassen.

### R11 · P1 · Unterschiedliche Politik für unvollständige Kerzen · E1/E2

**Quelle:** `backend/services/timeframes.py:73–105`; `dynamic_live.py:41,97`; `regime_gate.py:65`; `strategy_scanner.py:223`.

Dynamic/Gate aggregieren mit `drop_partial=False`, Scanner explizit mit `True`. Eine erst teilweise gebildete Stundenkerze bleibt in der Regimeberechnung. Eine abgeschlossene 1m-Kerze bedeutet nicht, dass der daraus gebildete 1h-Bucket abgeschlossen ist.

**Korrektur:** `bar_open_ts`, `bar_close_ts`, `available_at`, `is_closed`, Sessionkalender und Mindestabdeckung als Vertrag. Intrabar-Frühwarnung getrennt von verbindlichem Trading-Regime. Fehlende Rohminuten nicht per bloßem Zeitstempel als vollständig interpretieren.

### R12 · P1 · Fehlendes Regime-Binding handelt im Backtest Basis, live dagegen nichts · E1

**Quelle:** `backend/services/dynamic_strategy.py:269–284`; `dynamic_live.py:235–245`.

`strategies_by_regime.get(rid,strategy)` fällt auf Basis zurück. Live findet keine zugeordnete Strategie und deaktiviert die gemappten Strategien. Außerdem können `keep=false` und verbleibende Assignment-Keys widersprüchlich werden.

**Korrektur:** Explizite gemeinsame `unmapped_policy` und Gültigkeitsprüfung. Für NEUE freizugebende Pläne konservativ `no_new_entries`; Legacy-Verhalten nur versioniert und sichtbar erhalten.

### R13 · P1 · Bestätigung bindet nicht an den gesehenen Snapshot; Löschen hinterlässt Wirkung · E1/E2

**Quelle:** `backend/routers/dynamic.py:93–99,161–182`; `services/dynamic_live.py:357–361`.

Confirm prüft lediglich, dass `pending_switch` existiert, und wendet `last_state` an. Ein zwischenzeitlicher Refresh kann einen anderen Zustand liefern. Delete entfernt Dokument/Logs, aber nicht besessene Overrides/Locks/Toggles.

**Korrektur:** Command-ID + erwartete observed/model/config Version; abgelaufene Bestätigung ablehnen und neue Vorschau anbieten. Löschen als „deaktivieren/archivieren“ mit ownership-gesteuertem Unapply; keine offenen Trades oder schützenden Brokerorders blind entfernen.

### R14 · P1 · Cache kann die letzte noch offene Rohkerze einfrieren · E1/E2, konditional

**Quelle:** `backend/services/candle_cache.py:237–251`; `history_sources.py:132–165`.

Nachladen beginnt bei `cached_end+60_000`. Wenn die letzte gespeicherte 1m-Kerze noch unvollständig war, wird ihr aktualisierter Schlussstand nicht erneut angefordert. Der Stub-Test bestätigt den Startpunkt und das unveränderte Cacheelement; ein tatsächlich unvollständiger aktueller Provider-Datensatz wurde nicht live erhoben.

**Korrektur:** Überlappendes Nachladen der jüngsten Kerzen, Upsert/Dedup nach Instrument+Quelle+Intervall+Start; abgeschlossene Snapshotversionen nachträglich nicht still umschreiben. Datenkorrekturen erzeugen neue Revision.

### R15 · P1 · Mehrere Regime-Domänen ohne gemeinsamen Identitätsvertrag · E2/E3

**Quelle:** `backend/services/ai_market_observer.py:52–64,79–135,154–185`; `ai_engine.py:2149–2157`; `regime_gate.py:28–38,53–69`; `regime_engine.py:982–1008`.

Lab nutzt Modus-/Detektorregime, KI-Observer kurzzeitige `trend/range/breakout/drift × vol`, Gate labelbasierte `bulle/bär/seitwärts` aus einer separaten Berechnung. Unterschiedliche Horizonte sind legitim. Gleicher Begriff „Regime“ darf aber keine Gleichheit von Modell, Zeitraum, Bedeutung oder Trainingsdomäne vortäuschen.

**Korrektur:** Gemeinsamer `MarketContext` mit benannten Ebenen und versionierter Taxonomie; kein implizites Mapping aus deutscher Labelzeichenfolge. Langfristiges Kontextregime, kurzfristiges Setup und Risk-Overlay getrennt halten. Historische Legacy-Labels nicht ohne Provenienz neu interpretieren.

### R16 · P1 (Legacy) · K-Means-Normalisierung rundet Mindeststandardabweichung auf null · E2 + Laufwarnung

**Quelle:** `backend/services/regime.py:220,263–265,278,298–309`.

Fit begrenzt `std` auf `1e-9`, serialisiert mit 6 Dezimalstellen → `0.0`. Klassifikation dividiert danach ohne erneute Untergrenze durch null. Die zusätzliche vorhandene K-Means-Regression besteht, erzeugt aber `RuntimeWarning: invalid value encountered in divide` bei Zeile301. Beispielsweise konstantes relatives Volumen ist betroffen.

**Korrektur:** Rechenpräzision nicht für Anzeigerundung opfern; serialisierte std validieren/flooren, konstante Feature-Dimensionen kontrolliert behandeln. Ungültige Altmodelle sichtbar migrieren oder sperren.

**Abnahme:** Endliche Distanzen/Confidences, JSON-Roundtrip-Gleichheit, konstante Features, `np.errstate(divide='raise',invalid='raise')` im Test.

### R17 · P2 · Teststatus und Evidenzstärke werden in der UI zu pauschal dargestellt · E2

**Quelle:** `frontend/src/components/RegimeLab.js:517,631–657,838,1236–1238`.

`WF getestet` hängt am Vorhandensein eines Ergebnisses, nicht zwingend an bestanden/aktuell/unabhängig. Einzelne WF-PnL-Angaben erhalten positive Gestaltung unabhängig von Evidenzstatus. Tooltip „ehrlichster Wert“ bezieht sich auf Live=Final-Richtung, nicht auf Handelbarkeit/Profitabilität.

**Korrektur:** Bestehende UI erhalten, aber `nicht geprüft / bestanden / nicht bestanden / veraltet / unzureichende Daten` anzeigen; negative Werte nicht positiv färben. Duplicate Test-IDs bei gleichzeitig sichtbaren Symbolkarten vermeiden. Kein Browser-Lauf der externen Website erfolgt: dies ist eine Quellprüfung.

## B. KI Trader, Risiko und Ausführung

### T01 · P0 · Watchdog kann neue fremde Vollposition als Rest schließen · E1

**Quelle:** `backend/services/position_watchdog.py:648–690`.

Nach erfolgloser Registry-Zuordnung reicht ein gleichseitiger geschlossener Bot-Trade desselben Symbols in den letzten30 Minuten. Kein zwingender Positions-ID-/Mengenabgleich. Test: alter Trade Qty0,01, neue fremde Position andere ID Qty5 → `flash_close(... full=True)`.

**Korrektur:** Deterministische Ownership/Positionsidentität; Dust-Cleanup nur mit belegter Restmenge und Broker-Mengenpräzision. Unbekannte Fremdposition nicht destruktiv verändern, sondern isoliert melden und neue eigene Exposure bis zum Abgleich begrenzen. Manuelle Betreuung ausdrücklich separat freigeben.

### T02 · P0 · Market-Recovery übernimmt geplante statt tatsächlich gefüllter Menge · E1 des Originalbranches

**Quelle:** `backend/services/bitunix_trade.py:2380–2402,2425–2445,2580ff.`.

Timeout, geplante Qty10, untracked6 → `code=0`, Qty bleibt10. Bei4 wird abgelehnt, obwohl eine reale Teilposition existieren kann. Im Maker-Branch `1592–1601` wird hingegen untracked Qty ausdrücklich zurückgegeben; diesen Unterschied erhalten und als Referenz prüfen.

**Korrektur:** Vor der Order Intent persistieren, Fillhistorie/orderId/clientId zuordnen, verifizierte Qty/gewichteten Fillpreis verbuchen. `unknown` und `partially_filled` sind echte Zustände; kein blindes Re-Order nach Timeout. Nicht zugeordnete Börsenmenge allein ist kein sicherer Ownership-Beweis.

**Grenze:** Original-Exceptionbranch ausgeführt, gesamte Broker-/DB-Orderkette nicht. Nachgelagerte Abgleicher können später korrigieren; das macht den inkonsistenten Anfangszustand nicht korrekt.

### T03 · P0 · Nicht verifizierter Stop-Loss wird nicht als unbestätigt markiert · E1 des Originalbranches

**Quelle:** `backend/services/bitunix_trade.py:2490–2547,2867–2891`.

`sl_ok=None` (Verifikation nicht möglich) umgeht `if sl_ok is False`; bei vorhandener `position_id` bleibt `sl_exchange_missing=False`. Test enthält Gegenfälle True/False.

**Korrektur:** `confirmed / missing / unknown` statt Boolean. Unknown → priorisierte Retry-/Reconciliation-Warteschlange, alarmierter Healthstatus und Sperre neuer risikosteigernder Entries. Ob Schutzclose nötig ist, entscheidet eine explizite risikoabhängige Notfallpolicy; nicht jede vorübergehende Abfrageblindheit pauschal voll schließen.

### T04 · P0 · Setup-Reife-Gate ist in Fehlerfällen offen und besitzt Default-Bypass · E1

**Quelle:** `backend/services/ai_engine.py:1956–2034` sowie `live_gate_bypass_ok` im selben Modul.

Fehlendes Setup oder Exception in `cached_setup_stats` liefert `None` (kein Block). Bei unreifem Setup kann Default-Bypass mit Modellkonfidenz70, Schwelle65 und bisher0 Bypass-Trades erlauben. Bypass ist begrenzt und protokolliert; es ist kein unbegrenzter Zugriff.

**Korrektur:** Unbekannte/ungültige Live-Evidenz als `insufficient_evidence`, nicht „erlaubt“. Unreife Kandidaten nur Paper/Shadow. Falls kontrollierte Live-Exploration fachlich gewünscht ist: separate explizit genehmigte Policy mit eigenem winzigem Budget und unveränderlichen Obergrenzen, niemals allein wegen LLM-Selbstkonfidenz.

### T05 · P1 · Policy-Fingerprint ist nicht vollständig · E1/E2

**Quelle:** `backend/services/policy_fingerprint.py:14–26,44–65`; `ai_engine.py:1685–1700`.

Fingerprint erfasst Prompt, Lektionen, Playbook, Modell, Gate-Version und Sizing. Andere entscheidungsrelevante Configs wie min_confidence/fee_guard und ein Regimeartifact sind nicht ausdrücklich eigene Bestandteile. Bei sonst konstanten Bestandteilen bleiben Hashes im Test gleich. Ein min_confidence-Wert gehört nicht in den Sizing-Hash; der Fix muss einen zusätzlichen Policy-Config-Hash schaffen.

**Korrektur:** Gesamte effektive versionierte Entscheidungs-/Kosten-/Regime-/Risiko-/Ausführungsdefinition referenzieren; Modellfallback und tatsächlichen Promptkontext nachvollziehbar speichern. Bisherigen Fingerprint für Alttrades unverändert lassen, `fingerprint_schema=2` additiv.

### T06 · P0 · Fehlende Equity oder Stopdaten erlauben zu optimistische Risikorechnung · E1 bestehender Tests / E2

**Quelle:** `backend/services/risk_budget.py:33–44,68–75`; `entry_guard.py:71–83`; `position_sizing.py:123–142`.

Unbekannte Equity wird ausdrücklich fail-open behandelt; fehlende/ungültige SL-/Qty-Daten ergeben0 offenes Risiko. Ein Stop im Gewinn reduziert das modellierte Entry-zu-SL-Risiko zwar legitim, beseitigt aber nicht Gap-, Gebühren- oder Schutzorderrisiko. Der zentrale Budgetwrapper lässt Exceptions durch. Bestehender Test `test_check_fail_open_without_equity_and_when_disabled` bestätigt dieses heutige Verhalten.

**Korrektur:** Für neue Live-Exposure zwischen disabled, known-valid, stale und unknown unterscheiden. Bei unbekanntem Risiko keine risikosteigernde Freigabe; bestehende Positionen weiter schützen. Freshness und `stop_protection_status` einbeziehen. Assetklassenlimit ist keine gemessene Korrelationskontrolle; Stress-/Bruttoexposure optional ergänzen.

### T07 · P1 · Outcome-Sync markiert einen Trade vor erfolgreicher Ergebnisübernahme als erledigt · E2

**Quelle:** `backend/services/ai_learning.py:301–323`.

`ai_learn_synced=True` wird VOR `ai_decisions.update_many` geschrieben. Fehler danach → nächster Lauf findet den Trade nicht mehr. Periodischer PnL-Abgleich korrigiert nicht nachweislich diese fehlende Decision-Synchronisation.

**Korrektur:** Idempotenter Outcome-Event mit Version; zuerst Upsert/Consumer-Erfolg, dann konsumierten Stand markieren. Broker-PnL-Revisionen müssen eine neue Outcomeversion erzeugen, nicht am Boolean hängen bleiben.

**Abnahme:** Fehler vor/nach jeder Schreiboperation; erneuter Lauf und spätere Gebührenkorrektur aktualisieren exakt einmal je Ergebnisversion.

### T08 · P1 · Lernen verwechselt wiederholte Sichtung mit unabhängiger Evidenz · E2/E3

**Quelle:** `backend/services/ai_learning.py:110–181,377–411,728,813–853`; `ai_validation.py:92–113`.

Kandidatenzähler wächst bei erneut gleichem Lektionsschlüssel. Der Prompt fordert dieselben Titel ausdrücklich erneut an. Kandidaten-Sample addiert geschlossene Trades und entschiedene Signale; zu einem Trade gehöriges Signal kann dieselbe Gelegenheit doppelt repräsentieren. `aggregate_performance` mischt Paper/Live im Gesamtsaldo und filtert Collection nicht ausdrücklich heraus. Modus-Unterstatistiken sind immerhin vorhanden. Für Makrobestätigungen existiert bereits `real_confirmations` mit neuen Trades — nicht pauschal behaupten, alle Bestätigungen seien unecht.

**Korrektur:** Evidenz-IDs, Entscheidungsversion, Ergebnisquelle und disjunkte neue Beobachtungen erfassen; Signal-Touch separat von netto abgerechnetem Trade, Collection separat von regulärem Paper/Live. LLM-Wiederholung ist Hypothesenbestätigung durch das Modell, kein statistischer Test. Vorhandene `real_confirmations` verallgemeinern.

### T09 · P1 · Registry-Matching/Cleanup verliert eindeutige Orderprovenienz · E2

**Quelle:** `backend/services/entry_order_registry.py:55–62,65–77`.

Matching wählt jüngste Order nach Symbol/Seite, nicht zwingend verifizierte Fill-/Client-/Positionsidentität. Cleanup löscht Registrydatensatz nach Cancelversuch auch bei Exception. Das kann die Aufklärung späterer Fills erschweren. Kein konkreter Doppelorder-Livefall in dieser Analyse ausgeführt.

**Korrektur:** Intent+Order+Fill+Position getrennt verknüpfen; unbestätigtes Cancel bleibt unresolved/tombstoned. Terminalstatus erst bei Brokerbeleg. T01/T02 zusammen lösen, nicht unabhängig neue Heuristiken hinzufügen.

### T10 · P1 · IBKR-Platzierung ist nicht gleich bestätigter vollständiger Fill · E2/E3

**Quelle:** `backend/services/ibkr_trade.py:116–165`.

`open_live_forex` plant bis zu zwei Legs, fragt den Fill des ersten ab und kann `ok=True`, geplante Qty und `fill_price=None` zurückgeben. Das ist als akzeptierte Order korrekt, als bestätigte Gesamtposition nicht. Der Gesamteinfluss hängt von Aufrufer und späterem Broker-Sync ab; kein vollständiger IBKR-Test erfolgt.

**Korrektur/Prüfauftrag:** Rückgabe um accepted/partial/filled/unknown erweitern, kumulierte Fills je Leg rechnen, gewichteten Fillpreis und Schutz je Restmenge führen. Same-Day-Gateway-Reconnect, ein gefülltes/ein abgelehntes Leg und Teilstorno in Sandbox nachweisen. Gemeinsamer Fill-Vertrag, aber Brokerbesonderheiten erhalten.

## C. Struktur und Forschungsbewertung

### W01 · P1 für Refactoring · Workerpaket ignoriert Unterpakete · E2

**Quelle:** `backend/routers/local_worker.py:202–211,233–240`; `local_worker/worker.py:308–343`.

Paketierung nutzt nichtrekursives `glob('*.py')`. Neu empfohlene Unterpakete wären ohne Paketänderung nicht vorhanden. Manifest prüft Anzahl/Pflichtdateien, nicht Gleichheit jedes Berechnungsmoduls. Kein bestehender Komplettausfall behauptet.

**Korrektur:** Zunächst neue flache Service-Module oder geprüfte rekursive Allowlist; Manifest mit Commit/Dateihashes, gemeinsame Input-/Result-Schemas und Cloud/Worker-Parität. Keine beliebigen lokalen Configs oder Daten exportieren.

### W02 · P2 · Statistische Freigabe ist besser als bloßer PnL, aber noch keine harte Edge-Garantie · E2/E3

**Quelle:** `backend/services/policy_promotion.py:59–109`; `setup_backtest/weights.py:19–46`; `regime_opt.py:34–55,496–514`.

Unabhängiges Einzeltrade-Bootstrap bildet zeitlich/assetübergreifend korrelierte Trades nur begrenzt ab. Kandidatentrials und tatsächlich gehandelte Champion-Trades können unterschiedliche Gelegenheiten/Fillmodelle haben. Backtest-Boost kann mit lediglich2 profitablen echten Paper-Trades beitragen; das ist eine Produktheuristik, kein belastbarer Edge-Beweis. Dynamischer Verdict prüft einfache Mindesttrades/PnL/DD, nicht Suchvielfalt und wiederholte Holdout-Nutzung.

**Korrektur:** Paarweise gleiche Entscheidungssituationen, block-/sessionweises Bootstrap, Versuchszähler, Unsicherheitsintervalle und Kostenstress; Evidenzquellen nicht ungeprüft zu einer „echten“ Stichprobe aufsummieren. Nicht gleich ein komplexes ML-/Statistikframework neu bauen.

## Priorisierte Reihenfolge

1. **Ausführungsgrenzen:** T01/T02/T03/T06 und R03.
2. **Deterministische Aktivierung/Freigabe:** R01/R02/R04/R12/R13/T04.
3. **Valide Forschung:** R06/R07/R08/R09/R10/R11/R14/R16.
4. **Gemeinsame Modelle und evidenzbasiertes Lernen:** R05/R15/T05/T07/T08/T09/T10.
5. **Verständliche Oberfläche, Worker-/Langzeitpflege:** R17/W01/W02 — W01 bereits vor jeder neuen Unterpaketstruktur beachten.

Die P0-Kategorie ist eine Reihenfolge für Risikoreduktion, keine Anweisung, jetzt fremde Konten aufzurufen oder Positionen zu schließen. Jede tatsächliche Änderung benötigt einen gesonderten Implementierungsauftrag.

---

<!-- Quelldokument: 03_ZIELBILD_REGIME_LAB_UND_KI_TRADER.md -->

# 03 – Zielbild: Forschungslabor mit kontrollierter Verbindung zum Trader

## 1. Entscheidung: gezielter Ausbau statt Neubau

**Deine Laboridee ist fachlich sinnvoll.** Ein gutes Regime Lab beantwortet nicht nur „Welche Farbe hat dieser Chartabschnitt?“, sondern:

> Welche handelbare Regel bringt für welche Assets und Marktbedingungen einen robusten Zusatznutzen gegenüber einer einfachen Alternative — nach Kosten und außerhalb der Daten, auf denen sie ausgewählt wurde?

Regime sind dabei **Kontext**, kein Selbstzweck. Nicht jede Strategie muss neun verschiedene Regeln besitzen. Ein guter statischer Ansatz kann einer aufwendig umgeschalteten Strategie überlegen sein. „Kein neuer Trade“ ist ein zulässiges und oft notwendiges Ergebnis. Mehr Automatik ist nur dann ein Fortschritt, wenn sie die Qualität nachweislich erhöht.

### Gegenüberstellung der Optionen

| Option | Vorteil | Nachteil | Urteil |
|---|---|---|---|
| Unverändert lassen | Keine kurzfristige Umstellungsarbeit | Belegte Ausführungs-/Forschungsbrüche bleiben | Nicht als langfristiges Ziel geeignet |
| Komplett neu bauen | Fachgrenzen frei gestaltbar | Hoher Regressions-/Migrationsaufwand, parallele Engines | Nicht empfohlen |
| Nur weitere Indikatoren/KI-Prompts | Schnell sichtbare Funktionen | Vergrößert Suchraum, Overfitting und Wartung | Erst nach valider Messbasis |
| Vorhandenes Lab zu versionierter Forschungsstrecke ausbauen | Bestehende Oberfläche/Module nutzbar, kontrollierbarer Fortschritt | Disziplin bei Daten/Freigaben nötig | **Empfohlen** |

## 2. Zielarchitektur innerhalb der vorhandenen Struktur

```text
                       RESEARCH / KEINE ORDERRECHTE
Feste Marktdaten → Featureversion → Regime-/Strategieexperiment
                                      ↓
                           innere Auswahlvalidierung
                                      ↓
                           unabhängige äußere Tests
                                      ↓
                             Research Report
                                      ↓
                       StrategyRelease (unveränderlich)
                                      ↓ explizites Approval
──────────────────────────────────── Grenze ────────────────────────────────────
                             RUNTIME / HANDEL
Neue verfügbare Daten → versionierter MarketContext → PlanResolver
                                                        ↓
                        deterministisches Signal / begrenzter KI-Vorschlag
                                                        ↓
                          zentrale RiskPolicy / PortfolioReservation
                                                        ↓
                            OrderIntent → Broker → FillEvents
                                                        ↓
                          Schutzstatus + Ledger + Reconciliation
                                                        ↓
                    Monitoring / Shadowvergleich / neue Forschungshypothese
```

Die Rückkopplung aus realen Ergebnissen erzeugt **neue Kandidaten**, keine stillen Änderungen eines bereits freigegebenen Releases. Rechenworker erhalten Daten-/Experimentaufträge, keine Möglichkeit, aus einem Forschungsergebnis selbst eine Livefreigabe abzuleiten.

### Logische Bausteine und existierende Anker

Neue Namen sind Vorschläge, keine bereits implementierten Dateien. Die erste Stufe kann flache Module unter `backend/services/` verwenden; ein späteres Unterpaket setzt aktualisierte Worker-Paketierung voraus.

| Logischer Baustein | Bestehender Anker | Additive Erweiterung |
|---|---|---|
| Dataset Snapshot | `history_sources`, `candle_cache`, `candles` | `research_dataset.py`: unveränderliche Fenster, Qualität, Manifest |
| Feature-/Regimevertrag | `regime_core`, `regime_engine`, `ai_market_observer` | `market_context.py`: benannte Ebenen, Herkunft, Zeitpunkte |
| Ausführbarer Strategieplan | `dynamic_strategy`, `dynamic_live`, Strategy Registry | `strategy_plan.py`: reiner Resolver, Binding-/Fallbackvertrag |
| Referenzsimulation | `backtester` | Reiner Position-/Fill-Kern; bestehendes `simulate_pair` als Adapter |
| Experimente/Splits | `regime_lab`, `regime_opt` | `research_experiment.py`, `research_validation.py` |
| Freigabe | `routers/regime_lab`, `routers/dynamic`, `policy_promotion` | `strategy_release.py`: Version, Evidence-Hash, Approval |
| Risiko | `entry_guard`, `risk_budget`, `position_sizing` | Freshness, konservative Unknown-Policy, Reservation |
| Orderzustand | `entry_order_registry`, `entry_inflight`, Brokeradapter | Erweiterte Intent-/Fill-/Schutzstatusdaten |
| Ergebnisse/Lernen | `ai_learning`, `pnl_reconcile`, `policy_fingerprint` | versionierte OutcomeEvents, disjunkte Evidenz |

Keine neue Datenbank oder Infrastrukturkomponente ist für den ersten sinnvollen Schritt notwendig. Metadaten können in der vorhandenen MongoDB bleiben, größere Datenmengen in bestehenden passenden lokalen/Worker-Datenformaten mit Hashreferenz. Nicht komplette jahrelange Candlearrays unkontrolliert in einzelne Mongo-Dokumente packen.

## 3. Datenvertrag: identische Inputs, nachvollziehbare Bedeutung

### `DatasetManifest` (vorgeschlagen)

Pflichtfelder:

- `dataset_id`, `schema_version`, `created_at`, `code_commit`.
- `instrument_id` als stabile ID; Symbol, Assetklasse, Datenvenue, Handelsvenue, spot/perpetual/FX, Quote- und Kontowährung separat.
- `timeframe`, `bar_timestamp_semantics`, UTC-Fenster `[start_ts,end_exclusive_ts)`.
- `source_revision`, `raw_checksum`, `normalized_checksum`, genaue Candleanzahl.
- `available_until_ts`, Umgang mit noch offenen Bars, Marktkalender-/Sessionversion.
- Qualityreport: Lücken, Dubletten, unsortierte Bars, nichtendliche Zahlen, OHLC-Invarianten, Ausreißer, ungültige Volumenfelder, Listing-/Delistingfenster.
- Bei Futures: Kontrakt-/Funding-/Tick-/Qty-Information und deren zeitliche Gültigkeit, soweit verfügbar. Bei FX: Kalender und Währungsumrechnung. Nicht verfügbare Informationen ausdrücklich markieren.
- Datenberichtigungen als neue Version. Bestehende Experimentdaten nicht still ersetzen.

**Datenqualität ist ein Gate.** Fehlende Tage sollen nicht als ruhiger Markt erscheinen. Keine perfekte Liquiditäts-/Slippageannahme, wenn lediglich Candlepreise verfügbar sind. Daten einer anderen Venue können für Forschung nützlich sein, müssen aber als Proxy gekennzeichnet und gegen die Zielvenue validiert werden.

### Zeitbegriffe nicht vermischen

`bar_open_ts` ≠ `bar_close_ts` ≠ `available_at` ≠ `decision_at` ≠ `order_submitted_at` ≠ `filled_at`.

Eine Information darf einen Trade nur beeinflussen, wenn `available_at <= decision_at`. Multi-Timeframe-Kontext wird als rückwärtsgerichteter As-of-Join verbunden. Ein 1h-Wert darf nicht ab 10:00 in den 10:05-Trade gelangen, wenn die Stundenkerze erst11:00 geschlossen ist.

### Gemeinsame Zeitgrenzen über Assets

Gleichzeitig trainierte Assetmodelle verwenden eine gemeinsame Kalendergrenze, nicht bloß jeweils75% der vorhandenen Candleanzahl. Ein später gelistetes Asset oder längere Datenlücken verändern sonst die relativen Zeiträume; bei gepoolter Modellwahl können Trainingsdaten eines Assets zeitlich in den Test eines anderen reichen.

## 4. Regimevertrag: Kontext, Setup und Risiko trennen

### Drei sinnvolle Ebenen

1. **Struktureller Kontext:** längerfristiger Trend/Seitwärts, Volatilität, Liquidität/Session. Eher stabil; Tages-/Mehrstundenhorizont nach Datenbasis.
2. **Handelbares Setup:** kurzfristiger Pullback, Breakout, Range-Entry usw. Deutlich schneller als das strukturelle Regime.
3. **Risk-Overlay:** Datenqualität, Spread/Funding, Stress/News/Session, Korrelation/Exposure, unbekannter Brokerzustand. Darf Entries einschränken, muss nicht automatisch ein neues „Regime“ erzeugen.

Dadurch muss nicht ein einziger langsamer Regimedetektor zugleich Tagesschwankungen, Entry-Timing und Liquiditätsrisiko lösen.

### `MarketContext` (vorgeschlagen)

```json
{
  "schema_version": 1,
  "instrument_id": "example-btc-perpetual",
  "as_of_bar_close_ts": "2026-09-01T12:00:00Z",
  "available_at": "2026-09-01T12:00:02Z",
  "feature_version": "features-example-v1",
  "regime_model_id": "regime-example-v1",
  "taxonomy_version": "context-v1",
  "structural_regime": "trend_up",
  "volatility_bucket": "high",
  "short_term_setup_context": "pullback",
  "confidence_score": 0.72,
  "score_is_calibrated_probability": false,
  "data_quality": "valid",
  "status": "known"
}
```

Beispieldaten, keine produktive Konfiguration oder Modellleistung.

**Wichtig:** Bestehende Confidence-/Probability-Felder der Detektoren sind heuristische Scores. Sie sind ohne separate Kalibrierung keine72%-Gewinn- oder Regimewahrheitswahrscheinlichkeit. Anzeige und Freigabe müssen das respektieren.

### Historische Referenz ist ein anderes Objekt

`RetrospectiveReference` trägt ausdrücklich `uses_future=true`, Berechnungs-/Vorwärtshorizont, Version und diagnostischen Zweck. Es kann Verzögerung, Richtungswechsel und Regimeverständlichkeit bewerten. Es darf nicht die zum Entscheidungszeitpunkt verfügbare Information ersetzen.

### Welche Modellfamilie zuerst?

- Vorhandene deterministische EMA/Kombi/Reactive-Familien behalten und gegeneinander vergleichen.
- Kleine, interpretierbare Kontextmenge als Baseline; Aufspaltung in mehr Regime nur bei zusätzlichen robusten Informationen und ausreichenden unabhängigen Fällen.
- K-Means für Bestandskompatibilität und explizite Forschungsversuche behalten; numerischen R16 zuerst beheben.
- HMM, Change-Point-Modelle, ML-Classifier oder Soft-Routing sind **spätere Kandidaten**, keine notwendige Erstmaßnahme.
- Assetspezifische Kalibrierung nur bei Datenstärke; sonst Pooling über sachlich sinnvolle Assetklassen mit vorsichtiger assetspezifischer Abweichung.

## 5. Strategieplan: einmal definiert, überall gleich aufgelöst

### `StrategyRelease` / `StrategyBinding`

- Stabile Release-ID und Schema-/Versionsnummer.
- Referenz auf genaue Registry- oder Custom-Definition plus Definitionhash.
- Pro Kontextregime: `strategy_definition_id`, vollständige Strategieparameter, Trade-/Exitparameter, zulässige Seiten, Instrumente, Timeframe.
- Eindeutiger Fallback für `unknown`, `unmapped`, `stale`, `insufficient_history`.
- Transitionpolicy: was gilt für neue Entries und für schon offene Trades?
- Data-/Feature-/Regime-/Cost-/Risk-/Executionmodell-Versionen.
- Validierungsbericht, Fingerprint, Status, Freigabeakteur und Zeitpunkt.

**Resolverfunktion:** Gleiche Eingabe aus Release + MarketContext + gültigen persönlichen Einstellungen → gleicher EffectivePlan, unabhängig vom zuvor aktiven Regime. Keine kumulativen State-Merges.

### Bestehende Positionen bei Regimewechsel

Empfohlener Standard für neue Releases: **kein neuer Entry nach altem Plan, bestehende Position weiter unter ihrem ursprünglichen Exit-/Schutzvertrag verwalten.** Eine neue Policy darf nicht rückwirkend Risiko erhöhen oder Stops beliebig entfernen.

`close_open` ist nur eine ausdrücklich getestete Strategieentscheidung. Backtest muss ihre Kosten und Exitpreise tatsächlich simulieren. Keine symbolweite Fremdpositionen-Schließung als Nebenwirkung.

### Konfigurationspräzedenz

In einem Test explizit festschreiben:

```text
versionierter Basisplan
  + freigegebener Regime-Binding-Snapshot
  + erlaubte persönliche Scope-Overrides
  → effektiver Plan
  → globale harte Risikogrenzen begrenzen ihn abschließend
```

Persönliche Overrides dürfen harte Grenzen nicht überschreiben. Welche bestehenden Keys Override-relevant sind, wird einmal inventarisiert; keine zweite versteckte Präzedenz im Brokeradapter.

## 6. Echter Laborablauf

### Schritt A – Fragestellung vor Suche definieren

Beispiel: „Verbessert ein ATR-normalisierter Trendfilter die Nettoerwartung einer vorhandenen Pullbackstrategie in hochvolatilen Kontexten gegenüber derselben Strategie ohne Filter?“

Vorab festhalten: Assetuniversum, Datenzeitraum, Trainings-/Testfenster, Kosten-/Fillmodell, erlaubter Suchraum, Hauptmetrik und Verlust-/Stabilitätsgrenzen. Erst dann suchen. Nicht nach jedem Resultat das Ziel ändern.

### Schritt B – Indikatoren als versionierte Features testen

- Univariate Wirkung/bedingte Verteilungen, Missingness und Stabilität über Zeit/Assets.
- Redundanz zwischen Indikatoren; keine fünf stark korrelierten Trendindikatoren als fünf unabhängige Stimmen zählen.
- Ablation: derselbe Plan ohne den Indikator, mit einfacher Alternative und mit dem vorgeschlagenen Merkmal.
- Alle Transformations-/Skalierungs-/Auswahlschritte nur im Trainingsfold fitten.
- Diagnosekennzahlen wie Trefferquote, Lag und Übergangsstabilität getrennt von Trade-Nettoergebnissen.

### Schritt C – Verschachtelte zeitliche Validierung

1. Ein äußeres Zeitfenster dient nur zur Bewertung der vollständig ausgewählten Pipeline.
2. Innerhalb des davor liegenden Trainingsfensters finden Feature-/Regime-/Strategie-/Parametersuche und Auswahlvalidierung statt.
3. Ausgewählte Pipeline einfrieren; im äußeren Fenster barweise kausal durchlaufen lassen.
4. Danach vorrollen, nur bereits verfügbare Daten verwenden.
5. Ein letzter reservierter Zeitabschnitt wird einmalig nach Festlegung der Methode geprüft. Wird er erneut zur Anpassung verwendet, ist er ab dann Validierung; neues unabhängiges Material nötig.

Purging folgt den tatsächlich überlappenden Outcome-/Labelintervallen. Embargo/Gaps werden begründet an Horizont, Positionstragedauer und Datengenerierung festgelegt — nicht willkürlich „fünf Tage“, nicht fälschlich jeder Blick in zulässige historische Warmupdaten verboten.

### Schritt D – Gleiche Vergleichsbasis

Vergleichen gegen:

- keinen Trade als Cash-/Risiko-Baseline;
- freigegebene statische Strategie;
- einfachen festen Regimefilter;
- Regime-Parameteranpassung;
- erst dann echte Strategiewahl oder komplexere Modelle.

Alle Varianten sehen dieselben verfügbaren Entscheidungssituationen und verwenden dieselben Kosten-/Fillregeln. Unterschiedliche Zahl gefüllter Trades und geblockte Chancen offenlegen; nicht nur Gewinner oder nur im jeweiligen Modell gewählte Zeitfenster vergleichen.

### Schritt E – Ergebnisbericht

Pflicht: netto PnL und R, realisierte UND offene Equity-/Drawdownpfade, Gebühren/Funding/Slippage, Turnover, Exposure, Auslastung, Trade-/Episodeanzahl, Regime-Coverage, Profitfaktor mit korrektem Sonderfall „keine Verluste“, Ausfall-/Nichtfillquote, Signal-zu-Fill-Verzögerung.

Unsicherheit: block-/sessionbasierte Konfidenzintervalle, Sensitivität gegenüber Parametern/Kosten/Fenstern, Ergebnisverteilung statt nur bester Wert. Suchversuche, Abbrüche und nicht ausgewählte Kandidaten zählen. Deflated Sharpe/PBO sind bei größerer Suche mögliche Ergänzungen, keine magischen Freigabezertifikate.

**Minimum-Trades sind keine Naturkonstante.** Wenige hochkorrelierte Trades aus einer Trendepisode sind keine unabhängige Stichprobe. Mindestanzahl, Zeit-/Regimeabdeckung und Konfidenz müssen gemeinsam beurteilt werden; sonst `insufficient_evidence`, nicht `passed`.

## 7. Rolle des KI Traders

### Sinnvolle LLM-Aufgaben

- Testbare Hypothesen und kleine erlaubte Parameterbereiche vorschlagen.
- Forschungsberichte zusammenfassen, Unterschiede erklären, belegte Schwachstellen identifizieren.
- Nachrichten/qualitativen Kontext strukturiert erfassen, mit Quelle/Zeitpunkt versehen und außerhalb harter Sicherheitsentscheidungen halten.
- Kandidaten für Paper/Shadow vorschlagen; Ergebnisse anhand Evidence-IDs kommentieren.

### Nicht allein an das LLM delegieren

- Live-Freigabe bei fehlender Evidenz.
- Positionsidentität und Fillmengen nach Timeout.
- Absolute Risiko-/Kapitalgrenzen, Kill-Switch und Stopbestätigung.
- Ungültige oder veraltete Daten als aktuelle Wahrheit interpretieren.
- Eigenständig den Validator oder unveränderlichen freigegebenen Release umschreiben.

Das bestehende KI-Frontend muss nicht verschwinden. Der erste Umbau ist intern: strukturierte Vorschläge statt versteckter Zustandsänderungen, gemeinsame freigegebene Artefakte und klare Gründe bei Nichtausführung.

## 8. Freigabe und Betrieb

### Zustände

`draft → research_validated → shadow → paper_validated → approval_pending → approved_live → retired`

Daneben `rejected`, `stale`, `insufficient_evidence`, `suspended`. „Shadow“ trifft Entscheidungen ohne Orders; „Paper“ führt simulierte Orders nach dem definierten Fillmodell aus. Das darf nicht zusammengezählt werden.

### Harte Freigabekriterien

- Dataset, Code, Definition und Validierungsfingerprint passen exakt.
- Keine offenen P0-Ausführungs-/Paritätsprobleme im genutzten Pfad.
- Risiko- und Kostenstress erfüllt vorab festgelegte Grenzen.
- Ausreichende unabhängige OOS-/Paper-Evidenz für die erlaubte Domain.
- Reconciliation und Schutzstatus vollständig; bekannte Broker-/Kontokonfiguration.
- Explizite menschliche Erstfreigabe und klarer Scope; kein automatisches Hochskalieren wegen weniger Gewinner.

### Überwachung

Drift der Features/Regimebelegung, Fill-/Slippageabweichung, Datenlücken, Stop-Unknown, offene Intentalter, Regime-Flattern, tatsächliche vs. gewünschte Konfiguration, netto Ergebnisse pro Release. Vorab definierte Reaktion: neue Entries stoppen oder auf letzten geeigneten freigegebenen Plan zurückkehren; bestehende Positionsbetreuung erhalten.

## 9. Erste sinnvolle Laborversion

Noch keine neuen Indikatoren nötig. Ein Pilot mit wenigen ausreichend liquiden Assets, **einer vorhandenen Strategie**, **einem bestehenden Regimedetektor**, festem Datensatz und **einem** kleinen Suchraum reicht.

Akzeptanz des Piloten: gleiche Signale/Positionsübergänge im Referenzreplay und Runtime-Resolver, nachvollziehbar unveränderte Daten, ehrlicher unabhängiger Bericht, explizite Draft→Approval-Grenze. Wenn der Pilot keinen Zusatznutzen zeigt, die statische Basis beibehalten. Genau diese Möglichkeit macht ein Labor wissenschaftlich nützlich.

---

<!-- Quelldokument: 04_UMSETZUNGSPLAN_FUER_KI.md -->

# 04 – Umsetzungsplan für die nächste KI

**Dieser Plan ist nicht ausgeführt.** Gegenstand des vorliegenden Auftrags waren Analyse und Tests. Vor tatsächlichen Codeänderungen benötigt die nächste KI einen Implementierungsauftrag. Grundlage: Commit `792ff0ac44861df447d3e619042ecef1704c37c5`.

## Verbindliche Leitplanken

1. `backend/`, `frontend/`, `local_worker/`, vorhandene Imports/API-Routen, Datenbanknamen und Startprozesse nicht pauschal verändern.
2. Keine Produktionszugänge aus dem Analysechat verwenden. Keine Live-Orders, DB-Migrationen oder Server-Lifespan ohne gesonderte Freigabe ausführen.
3. Vor jedem Paket tatsächlichen aktuellen Commit und betroffene Call-Sites prüfen. Zeilenangaben können nach Änderungen wandern.
4. Zuerst Verhalten in gezielten Solltests festlegen; dann minimal korrigieren; anschließend Regression. Charakterisierungstests, die den Fehler erwarten, nach Fix bewusst durch Solltests ersetzen.
5. Einen Fachfehler nicht mit gleichzeitigem großem Refactoring mischen. Neue Facades/Adapter erhalten bestehende Signaturen und JSON-Verträge; Felder zunächst additiv.
6. Je Paket Kurzbericht mit Dateien, geändertem Verhalten, Testergebnissen, offenen Risiken und Rückfallmöglichkeit. Fortschritt nach jedem Paket als Datei sichern.
7. Keine Behauptungen „fehlerfrei“, „profitabel“ oder „produktionserprobt“ ohne jeweiligen Nachweis.

## Abhängigkeiten und sinnvolle Parallelität

```text
AP00 Test-/Vertragsbasis
  ├─ AP01 Schutz/Ownership ─ AP02 Fill/Unknown/Risiko
  ├─ AP03 Deterministische Dynamic-Pläne ─ AP04 Freigabe/State
  └─ AP05 Datenstände ─ AP06 Referenzsimulation
                          └────────┬───────────────┘
                              AP07 Forschungsvalidierung
                                  ↓
                              AP08 Gemeinsamer Kontext
                                  ↓
                       AP09 Policy-/Lernprovenienz
                                  ↓
                       AP10 Labor-Pilot / UI-Verträge
                                  ↓
                       AP12 Shadow-/Paper-Abnahme

AP11 Worker-/Betriebsvertrag begleitet ALLE neuen Rechenmodule.
AP13 Bereinigung/weiterführende Forschung erst danach.
```

AP05 kann ohne Livecodeänderung parallel zu Ownership-Härtung vorbereitet werden. An `bitunix_trade.py` und `dynamic_live.py` nicht mehrere KI-Agenten gleichzeitig unkoordiniert schreiben lassen. Ein zentraler Verantwortlicher pro Paket verwaltet die Schnittstellen.

## AP00 – Baseline, sichere Testumgebung, Vertragsinventar

**Priorität:** P0 · **Größe:** S/M · **Abhängigkeit:** keine.

**Dateien:** vorhandene Tests, `backend/tests/conftest.py`, Start-/Config-/Routerverträge; Analysepaket `tests/` nur als Referenz.

**Aufgaben:**
1. Quellstand pinnen und vorhandene Analyse mit aktuellem Code diffen.
2. Relevante Router-/Response-Schemas und gespeicherte Strategie-/Konfigurationsformen als anonymisierte Fixtures erfassen. Keine echten Zugangsdaten.
3. Testisolation vor Imports: lokale Fake-DB, deny-by-default Netzwerk, keine `.env`, keine Lifespan/Bootjobs. `pytest -m unit` allein ist wegen vorhandener Collectionlogik nicht ausreichend.
4. Die ausgelagerten Charakterisierungstests in die beauftragte Entwicklungsumgebung übernehmen; jedes Testziel als Ist- oder Sollvertrag markieren.
5. Eigentums-/Konfigurationsmatrix inventarisieren: instrument, strategy, dynamic, account, broker, mode und welche Instanz globale Caches schreibt.

**Abnahme:** Offline-Suite reproduzierbar; Versuch eines Netzwerkzugriffs scheitert vor Clientnutzung; Noop-Import startet keine Loops. Aktuelle Snapshot-/Positionsfixtures ohne Secrets. Die bestehende externe Anwendung wurde dadurch nicht verändert.

**Rückfall:** Keine Produktionswirkung. Nicht die generische Analyseumgebung als Nutzerwebsite behandeln.

## AP01 – Positionsschutz mit eindeutiger Ownership

**Priorität:** P0 · **Größe:** M · **Befunde:** T01, T09, R03.

**Dateien:** `services/position_watchdog.py`, `entry_order_registry.py`, `dynamic_live.py`, `trade_guard.py`; passende bestehende Tests.

**Aufgaben:**
1. `OwnedPositionRef`-Logik definieren: Broker/Account/Position-/Order-/Intent-ID, Strategy/Dynamic/Mode, tatsächliche Fills. Symbol+Seite+Zeitfenster genügt nicht.
2. Watchdog-Restclose nur bei nachgewiesener identischer Position und Restmenge; Qty-Minimum/Step/Rounding berücksichtigen.
3. Nicht zugeordnete Fremdpositionen unverändert lassen; Healthwarnung und kontrollierte Reconciliation statt heuristischem Vollclose.
4. `transition_protect` scoped und erst Teil eines freigegebenen Apply-Commands. Refresh/Dismiss ohne Handelswirkung.
5. Unbestätigte Registry-Cancels nicht vergessen; unresolved Tombstone mit nächsten Abgleichterminen.

**Solltests:** Unterschiedliche Positions-IDs bei gleichem Symbol; alter kleiner Bottrade/neuer großer manueller Trade; Hedge-Positionen; zwei Strategien; Paper vs. Live; verspäteter Fill nach Cancel-Timeout; Dust unter Step; doppelte Watchdogausführung.

**Abnahme:** Kein Close ohne konkreten Eigentums-/Mengenbeleg. Alle beabsichtigten Änderungen tragen Scope und Reason. Kein bestehender Stop wird beim Deaktivieren entfernt.

**Rückfall:** Nur neue Entries pausieren, falls Ownership ungeklärt. Niemals den bekannten fehlerhaften Fremdpositionsclose als automatischen Rollback erneut aktivieren.

## AP02 – Orderzustandsautomat, Teilfüllung, Stop-Unknown und Risikofrische

**Priorität:** P0 · **Größe:** L, in mindestens3 kleine Teiländerungen teilen · **Befunde:** T02/T03/T06/T09/T10.

**Dateien:** `bitunix_trade.py`, `ibkr_trade.py`, `entry_order_registry.py`, `entry_inflight.py`, `entry_guard.py`, `risk_budget.py`, `position_sizing.py`, `pnl_reconcile.py`.

**AP02a – Fillvertrag:** Intent vor Send persistieren; Zustände `created/submitted/acknowledged/partial/filled/cancel_requested/cancelled/rejected/unknown`. Stable client-ID und verifizierte kumulierte Fills. Qty in allen Pfaden tatsächliche Menge, nicht geplante Annahme. Maker und Market verwenden denselben Vertrag, aber unterschiedliche Executionmodelle.

**AP02b – Schutzvertrag:** `confirmed/missing/unknown`, Schutzorder-ID und abgedeckte Restmenge. Unknown zeitlich begrenzt retry/reconcile; neue risikosteigernde Orders sperren. Notfallclose nur über explizite Policy und verfügbare Positionswahrheit. Reconnect darf Unknown nicht heimlich zu confirmed machen.

**AP02c – Budgetvertrag:** Equityalter und Datenquelle; unbekannte Restmenge/SL nicht als0 Risiko. Reservierung vor externem Orderrequest verhindert gleichzeitiges Überschreiten von Budget/Slots. Reservation bei terminalem Cancel freigeben, bei Unknown erhalten und später abgleichen. Einprozesslock nur als lokale Ergänzung, nicht als dauerhafte Identitätsgarantie.

**Solltests:** Timeout vor/nach Brokerannahme;0/40/60/100% Fills; weiterer Fill während Cancel; anderer fremder Positionzuwachs; zwei IBKR-Legs unterschiedlich gefüllt; SL True/False/None; alte Equity; fehlender SL; doppelte Fillereignisse; Restart zwischen jedem Zustand; zwei gleichzeitige Signale.

**Abnahme:** `sum(fill_qty)` = gebuchtes Exposure innerhalb Broker-Rundung; keine bestätigte Position ohne belegten Fillstatus; kein unbestätigter SL als „geschützt“; unbekanntes Risiko erzeugt keine neue Livefreigabe. Entscheidungen und unvermeidbare Unsicherheit sind sichtbar.

**Rückfall:** Orders nicht blind stornieren oder wiederholen. Reader bleiben zu alten Tradefeldern kompatibel; neue Zustände additiv. Bestehende Positionen behalten sichere Betreuung.

## AP03 – Ein reiner Resolver für dynamische Strategien

**Priorität:** P0/P1 · **Größe:** L · **Befunde:** R01/R02/R12.

**Dateien:** `dynamic_live.py`, `dynamic_strategy.py`, `routers/regime_lab.py`, Strategy Registry/Custom-Definitionen, vorgeschlagen `services/strategy_plan.py`.

**Aufgaben:**
1. `StrategyBinding`-Schema und `resolve_effective_plan(release,context,overrides)` als reine Funktion einführen.
2. Vollständige Definitionen für Registry, Discovery und gemischte Zuordnung auflösen. Jede Definition besitzt Hash/Version; kein Austausch einer global geteilten Basisdefinition.
3. Effektive Parameter von Basissnapshot ableiten; keine kumulativen Merges. Dynamische vs. persönliche Ownership dokumentieren.
4. `unmapped/unknown/stale` explizit behandeln. Bestehende Fallbacks in Legacy-Adapter, neue Releases standardmäßig ohne neue Entries bei fehlendem Binding.
5. Beide Simulations- und Runtimeadapter verwenden den Resolver. Gleichzeitige Releases auf demselben Strategy-/Symbol-Scope entweder verbieten oder explizit priorisieren.

**Solltests:** Gegensätzliche A-/B-Regeln; Registry+Discovery; derselbe Strategietyp mit zwei Parametervarianten; A→B→Baseline vs. direkte Baseline; Teilconfig; unbelegtes Regime; zwei Assets; zwei dynamische Dokumente; manuelle Parameter bleiben erhalten.

**Abnahme:** Identische Inputs liefern identischen EffectivePlan/hash; gleiche Regimehandlung in Backtest/Paper/Live-Resolver. Zur Freigabe muss nicht die gesamte Runtime neu geschrieben werden.

## AP04 – Beobachtung, Apply, Bestätigung und Release trennen

**Priorität:** P1 · **Größe:** M/L · **Befunde:** R04/R09/R13/T04.

**Dateien:** `dynamic_live.py`, `routers/dynamic.py`, `routers/regime_lab.py`, `ai_engine.py`, vorgeschlagen `strategy_release.py`.

**Aufgaben:**
1. Persistente `observed/desired/applied` Zustände und `pending_command` mit vollständigem Snapshot, Versions-ID, Actor, Scope und Ablaufdatum.
2. Compare-and-swap / erwartete Version beim Confirm; neue Beobachtung entwertet alte Bestätigung oder erfordert neue Vorschau.
3. Idempotenter Applyworkflow mit Teilschritten/Status/Retry. Erfolgreiches Beobachten ≠ erfolgreiches Anwenden.
4. Draft-Build darf ohne grünen Test bleiben; Live-Approval nur mit passendem Evidence-/Definition-/Datenhash. Edit erzeugt neue Revision und stale-Status der alten Validierung.
5. Setup-Live-Gate bei fehlenden Daten/Exceptions geschlossen für neue Exposure. Bypass gesondert benennen und standardmäßig kein Weg für unreife Releases.
6. Delete = deaktivieren/archivieren mit scoped Unapply; Referenzen und offene Positionsbetreuung erhalten.

**Abnahme:** Refresh hat keinerlei Orderwirkung; veralteter Confirm liefert Konflikt; Applyfehler wird retrybar angezeigt; kein alter grüner Verdict nach Änderung; neue unvalidierte Strategie kann nicht live aktiviert werden.

## AP05 – Reproduzierbare Datenstände und saubere Candlegrenzen

**Priorität:** P1 · **Größe:** L · **Befunde:** R06/R07/R11/R14/R16.

**Dateien:** `regime_lab.py`, `history_sources.py`, `candle_cache.py`, `candles.py`, `timeframes.py`, `regime.py`; proposed `research_dataset.py`.

**Aufgaben:**
1. Historische Start-/Endanker durchgängig übergeben oder feste Datenmanifeste nutzen. Keine „days ab jetzt“-Reproduktion alter Analyse.
2. Providerupdates überlappend holen, nach Candleidentität mergen; Qualität prüfen. Datensnapshot nach Abschluss unveränderlich.
3. Halboffene Intervalle und expliziter UTC-Kerzenabschluss; Kalender-/Sessionlücken von Feedlücken unterscheiden.
4. Gleiche Candlepolitik für verbindliche Runtime-/Labregime. Intrabar-Signale gesondert kennzeichnen.
5. Legacy-K-Means-Normalisierungspräzision korrigieren, JSON-Roundtrip und konstante Dimensionen prüfen.
6. Alte Analysen erhalten `legacy_unpinned/quality_unknown`, nicht stillschweigend „validiert“.

**Abnahme:** Gleiche Analyse eine Woche später liest exakt gleiche Candleanzahl/Hash. Kein doppelt gezählter Segmentrand. Aktuelle Teilkerze verändert abgeschlossene Historie nicht. Ungültige Daten erzeugen erklärten Abbruch. Keine Divide-by-zero-Warnung in Legacy-Modell.

## AP06 – Referenzsimulation mit vollständigem Positions-/Kostenmodell

**Priorität:** P1 · **Größe:** L · **Befunde:** R08/R12/W02.

**Dateien:** `backtester.py`, `dynamic_strategy.py`, `fast_sim.py`, `fast_signals.py`, `paper_execution.py`, `fee_model.py`, `setup_backtest/`.

**Aufgaben:**
1. Gemeinsamen deterministischen Positionszustand/Exitrechner definieren; bestehende public Funktionssignaturen über Adapter erhalten.
2. Signal auf geschlossener Bar → frühester tatsächlich möglicher Fill. Explizites Close-on-close-Modell nur als Annahme kennzeichnen; keine kostenlose exakte Schlusskursausführung unterstellen.
3. Warmup berechnet Features, verbietet Orders. Positionen bei Regimewechsel entsprechend Transitionpolicy weiterführen oder explizit schließen.
4. Same-Bar: SL/Liquidation/Teil-TP/TPFull/Gaps in dokumentierter konservativer Reihenfolge. OHLC-Unsicherheit offen ausweisen.
5. Offene Endpositionen samt Gebühren/unrealisiertem PnL/Exposure/DD in Report. EOD-Liquidation nur optional klar benannt.
6. Mindestkommission, Brokerpräzision, Funding, Spread/Slippage und Maker-Nichtfills in ausreichender Modelltreue. Fehlende Daten nicht erfunden auffüllen.
7. Referenz gegen Fast-Sim, Paper und Setup-Backtest für gemeinsame Semantik testen. Optimierte Pfade nur für unterstützte Cases zulassen, andernfalls Referenzfallback.

**Abnahme:** R08-Solltests grün (+4 statt+6); Portfolioequity enthält offene Positionen; gleicher Release+Dataset+Seed liefert identischen Bericht; beschleunigter Pfad hat messbare Paritätsgrenze. Ergebnisse dürfen durch ehrliche Kosten schlechter werden.

## AP07 – Forschungsvalidierung ohne Holdout-Tuning

**Priorität:** P1 · **Größe:** L · **Befunde:** R05/R09/R10/W02.

**Dateien:** `regime_lab.py`, `regime_opt.py`, `regime_truth.py`, vorgeschlagen `research_experiment.py`/`research_validation.py`.

**Aufgaben:**
1. Experimentmanifest vor Suchstart: Fragestellung, Dataset, erlaubte Suchräume, Kosten-/Executionversion, Hauptmetrik, Seeds, Foldkalender, unveränderliche Releasekandidaten.
2. Final-Reference strikt aus handelbarer kausaler Segmentauswahl entfernen. Beaufsichtigte Zukunftsziele nur mit explizitem Labelhorizont und Purge.
3. Train/innere Validation/äußere Tests; gemeinsamer Kalender über Assets. Fit aller Transformationen ausschließlich auf Training.
4. EMA-/Kombi-Vergleich nutzt innere Validation. Äußerer Test weder Rankingziel noch nachträglich verschobene Schwelle.
5. Versuchszähler, Wiederverwendung sichtbarer Holdouts, abgebrochene Runs und Kandidatenabstammung speichern.
6. Vergleiche auf gleichen Chancen mit statischer Basis und einfachen Filtern; robuste Netto-/Kosten-/DD-Auswertung, Unsicherheit und `insufficient_evidence`.

**Abnahme:** Zukunftsdaten verändern keine früher verfügbaren Features/Entscheidungen; äußeres Fenster hat keinerlei Einfluss auf Auswahlcode; geänderte Kandidaten invalidieren Evidencehash; finaler Bericht reproduzierbar. R05-Offenpunkt mit geeigneten positiven/negativen Prefixfixtures explizit schließen.

## AP08 – Gemeinsamer MarketContext statt zufälliger Regimegleichheit

**Priorität:** P1 · **Größe:** M/L · **Befunde:** R15/T05.

**Dateien:** `regime_core.py`, `regime_engine.py`, `ai_market_observer.py`, `regime_gate.py`, `ai_engine_context.py`, `ml_gate.py`, `strategy_plan.py`.

**Aufgaben:**
1. Namens-/Zeit-/Taxonomievertrag für strukturelles Regime, kurzfristigen Setupkontext und Risk-Overlay.
2. Gemeinsamen Feature-/Regimeartifact versioniert erzeugen; Runtime verwendet freigegebenen Stand statt für jede Gatefrage ein ungebundenes neues Modell zu fitten.
3. Alte Observer-/Gate-/Lablabels über explizite benannte Adapter; keine Sprachlabel-Substring-Erkennung als Identität.
4. `unknown/stale/out_of_domain` als echte Zustände. Heuristische Scores nicht als kalibrierte Gewinnwahrscheinlichkeit darstellen.

**Abnahme:** Gleicher Artifact und Candlepräfix liefert in Lab, Dynamic und KI denselben strukturellen Kontext. Zulässige Kurzfristunterschiede tragen eigene Namen/Versionen; Alttrades werden nicht neu gelabelt.

## AP09 – Policy- und Lernprovenienz, idempotente Ergebnisse

**Priorität:** P1 · **Größe:** L · **Befunde:** T05/T07/T08/W02.

**Dateien:** `policy_fingerprint.py`, `ai_engine.py`, `ai_learning.py`, `ai_validation.py`, `pnl_reconcile.py`, `setup_backtest/weights.py`, `policy_lab.py`, `policy_promotion.py`.

**Aufgaben:**
1. Fingerprint-Schema2 mit effective config / Regime-/Feature-/Kosten-/Execution-/Gateversion; tatsächliches Modellfallback mitführen.
2. OutcomeEvent nach Fill-Ledger/PnLrevision; Verarbeitungsmarker erst nach erfolgreichem idempotentem Update. Späte Gebühren-/Fundingkorrektur erneuert Outcomeversion.
3. Beobachtungen nach Live/Paper/Shadow/Collection/Backtest, Assetdomain, Release und Outcomequelle trennen. Ein Signal+Trade zählt als eine Gelegenheit, nicht zwei unabhängige Beispiele.
4. Lektionen benötigen neue disjunkte Evidenz-IDs; Wiederholung desselben Textes/Datensatzes erzeugt keine weitere unabhängige Bestätigung.
5. Champion und Challenger auf denselben geeigneten Chancen vergleichen. Blockbootstrap statt angenommener Unabhängigkeit aller Trades prüfen.
6. LLM darf Hypothesen kommentieren, nicht selbst seine Freigaberegeln ändern. Bestehende Governance-Whitelists behalten.

**Abnahme:** Replay desselben Outcomes erzeugt keine Duplikate; Pause zwischen DB-Updates verliert keine Ergebnisse; Nicht-Sizing-Policyänderung verändert Gesamtfingerprint; keine Livefreigabe durch rein wiederholte Backtest-/LLM-Evidenz.

## AP10 – Labor-Pilot und verständliche UI-Zustände

**Priorität:** P1/P2 · **Größe:** M · **Befunde:** R17, Gesamtziel.

**Dateien:** `RegimeLab.js`, `DynamicPanel.js`, `AITradingPanel.js`, bestehende Unterkomponenten und zugehörige API-Schemas.

**Aufgaben:**
1. Pilot: wenige Assets, ein bestehender Detektor, eine vorhandene Strategie, begrenzter Suchraum.
2. Bestehende Navigation/Overlays erhalten; keine kosmetische Neugestaltung parallel zur Fachmigration.
3. Datenversion, Modellversion, Draft-/Validation-/Approvalstatus und tatsächlichen applied_state anzeigen.
4. Final-Band als rückblickende Referenz, kausales Band als handelbarer Kontext kennzeichnen. „WF getestet“ durch korrekten aktuellen Status ergänzen.
5. Stale-Confirm, fehlender Holdout, zu wenig Daten, laufender/abgebrochener Job, fehlender Worker und unvollständige Ergebnisse verständlich behandeln.

**Abnahme:** Bisherige URLs/Flows bleiben erreichbar. Nutzer erkennt, was lediglich beobachtet, getestet, freigegeben und wirklich aktiv ist. Responsive Browserregression erst gegen isolierte echte Appinstanz mit Testdaten; keine fremde Startervorschau als Produktfehler.

## AP11 – Worker-/Rechen- und Strukturvertrag

**Priorität:** P1 begleitend · **Größe:** M · **Befund:** W01.

**Dateien:** `local_worker/worker.py`, `routers/local_worker.py`, `services/local_exec.py`, neue Berechnungsmoduldateien.

**Aufgaben:** Gemeinsame Schemas und Rechenkern im Paket; rekursive geprüfte Allowlist falls Unterpakete; Paketmanifest mit Dateihashes/Commit; Inputhash im Job, Resultschema und Evidencehash in Antwort. Alte Worker nur für kompatible alte Jobs zulassen. Ergebnispersistenz und Jobabschluss idempotent, Restart/late result geprüft.

**Abnahme:** Cloud und Worker rechnen denselben Pilotdatensatz/Release identisch innerhalb deklarierter numerischer Toleranz. Unpassende Version liefert verständliche Ablehnung, kein „done“ mit unbrauchbaren Daten. Neue Dateien liegen wirklich im Paket. `.env`, private Workerconfigs und Datenbankdumps werden nie eingebündelt.

## AP12 – Gestufte Abnahme und Freigabe

**Priorität:** P1 · **Größe:** M + Beobachtungszeit · **Abhängigkeit:** relevante AP01–AP11 bestanden.

1. Offline-Replay und Fault-Injection vollständig.
2. Shadowmodus auf aktuellen Daten ohne Orders; Determinismus und tatsächliche Planunterschiede beobachten.
3. Paper mit realistischer Fill-/Kosten-/Stornosemantik, ausreichender unabhängiger Zeit-/Regimeabdeckung.
4. Menschliche Freigabe für engen Live-Scope, nur mit gültiger Broker-/Risikokonfiguration und aktuellen Schutz-/Reconciliationdaten. **Nicht Teil des vorliegenden Analyseauftrags.**
5. Vorab genehmigte Abbruchgrenzen, neue Entries pausieren, bestehende Positionen weiter betreuen.

**Abnahme:** Beweispaket aus Dataset/Release/Reports/Tests/Approval/Runtimehealth. Kein beliebiger Profitfaktor oder wenige gute Trades als alleinige Freigabe.

## AP13 – Gezielte Bereinigung und spätere Erweiterung

**Priorität:** P2 · **Größe:** variabel · **Nur nach Pilotnachweis.**

- Große Fachmodule entlang bereits stabilisierter Verantwortlichkeiten herauslösen; Facades/Imports behalten.
- Veraltete Experimental-/Reviewdateien katalogisieren und dokumentiert archivieren, nicht blind löschen.
- Indikator-Ablationen, Assetgruppenpooling und Unsicherheitskalibrierung erweitern.
- Erst bei nachgewiesenem Bedarf HMM/ML-Regimemodelle, Soft-Routing oder zusätzliche Datenquellen untersuchen.
- Performanceoptimierung anhand messbarer Engpässe, keine Kernlogikverdopplung zur Beschleunigung.

## Nichtziele der ersten Umsetzungsphase

Kein Redesign, keine komplette neue Trading-Engine, kein Austausch von React/FastAPI/Mongo, kein Brokerwechsel, kein neues RL-System, keine unkontrollierte LLM-Autonomie, keine Jagd nach maximaler Indikatorzahl. Keine Renditegarantie. Primäres Ergebnis ist eine nachvollziehbare, getestete und deutlich verlässlichere Entscheidungs-/Ausführungskette.

## Größenangaben bewusst ohne Scheingenauigkeit

S/M/L beschreiben Abhängigkeiten und Risiko, nicht garantiert benötigte Stunden oder Credits. AP02/AP03/AP06/AP07/AP09 sind jeweils mehrere kleine Changes. Produktive historische Datenqualität, aktive Konfigurationsvarianten und Broker-Sandboxantworten können zusätzliche Arbeit zeigen; dies wird erst nach Freigabe dieser nächsten Phase erhoben.

---

<!-- Quelldokument: 05_REGRESSION_ABNAHME_MIGRATION.md -->

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

---

<!-- Quelldokument: 06_KI_HANDOFF_STARTPROMPT.md -->

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

---

<!-- Quelldokument: 07_GRENZEN_ENTSCHEIDUNGEN_QUELLEN.md -->

# 07 – Prüfgrenzen, offene Entscheidungen und Fachquellen

## 1. Umfang der tatsächlichen Prüfung

Analysiert wurde eine separate Quellkopie des öffentlichen Branches am Commit `792ff0ac44861df447d3e619042ecef1704c37c5`. Die produktive externe Website wurde nicht gestartet, verändert oder mit Zugangsdaten aufgerufen. Die Analyseumgebung enthält weiterhin ihren unabhängigen Starter; sie ist kein Ersatz für deine Anwendung.

### Tief geprüft

- Regime Lab: Datenabruf, Train-/Holdoutverwendung, Final-/Live-Segmente, Suche, Assign/Keep/Build/Walkforward.
- Dynamische Strategien: Parametereffekt, Definitionstransport, Fallback, Transition-Scope, Last-State/Retry und Confirm/Delete.
- Backtest: Entry-/Exit-/Teil-TP-/Endzustand, segmentweise Verwendung, Kennzahlen.
- KI Trader: relevante Context-/Regime-/Decision-/Setup-Gatepfade, Policyfingerprint, Governancegrenzen und Lernoutcomeprozess.
- Order-/Risiko-Hotspots: Watchdog-Adoption, Timeoutmengen, Stopverifikation, Registry, Portfolio-/Clusterbudget.
- Worker-Paketvertrag und betroffene UI-/API-Statusdarstellung im Quelltext.

### Nur teilweise geprüft

Vollständige `ai_trade_manager`-Aktionsmenge, alle Fast-Sim-Spezialzweige, komplette Key-Level-Limit-Lebenszyklen, alle Governance-/Housekeeping-Automationen, ML-Modelltraining/Validierung, kompletter IBKR-Sync, alle Brokergebühren-/Währungsfälle, Worker-Reconnect/Claim-Persistenz und sämtliche frontendseitigen Interaktionen.

### Nicht geprüft / nicht behauptet

- Tatsächliche aktive Settings oder gespeicherte Daten deiner Produktionsinstanz.
- Ob Regime-Discovery, `close_open`, Setup-Bypass oder betroffene Legacy-K-Means-Dokumente bei dir aktuell genutzt werden.
- Brokervertragsdetails, Netzwerk-/Gatewaylatenz, tatsächliche Gebühren/Slippage, Live-Fills, Reconnects und Stoporderabdeckung.
- Langfristige Profitabilität, aktuelle Model-Providerqualität oder behauptete Renditewerte in älteren Dokumenten.
- Vollständiger Sicherheits-/Penetrationstest. Die Credentialwarnung folgt der Offenlegung im Chat; sie ist kein separates Sicherheitsaudit.
- Vollständiger Test aller280 bestehenden Backend-Testdateien oder jeder der532 Python-Dateien.

## 2. Wichtige zurückgenommene/verfeinerte Annahmen

1. **Segmentende:** Ein Kommentar behauptet erzwungenes Schließen. Tatsächlicher Backtester verliert offene Positionen. Der finale Katalog verwendet den Codebefund, nicht den Kommentar.
2. **R05:** Kausaler Labeler wurde nicht als generell lookaheadbehaftet bestätigt. Verkabelung mit Final-Segmenten ist belegt; die konkrete getestete Future-Append-Probe blieb ohne Finaländerung.
3. **TP1/TPFull:** Erste Fixture hatte identische Ziele und zeigte nur das fehlende TP1-Flag. Die finale Probe verwendet verschiedene Ziele und belegt +6 statt+4 tatsächlich numerisch.
4. **T02/T03:** Erste Branchmodelle waren schwächer als Originalcodeausführung. Die finale Testdatei führt AST-extrahierte Originalbranches mit kontrollierten Abhängigkeiten aus. Weiterhin kein vollständiger Brokerflow.
5. **Frontend:** Ein früher Testagent sah die allgemeine Startervorschau. Das ist kein Befund über die Nutzerwebsite; nicht in den Produktfehlerkatalog übernommen.
6. **Testgrün:** Bestehende Tests bestätigen teilweise ausdrücklich das heutige fail-open-Verhalten. Ein grüner Test ist nicht automatisch ein gutes fachliches Design.

## 3. Vor späterer Umsetzung zu entscheidende Produktregeln

Diese Fragen blockieren die Analyse nicht. Für einen Implementierungsauftrag sind begründete Defaults vorgeschlagen, aber nicht als aktive Nutzerkonfiguration auszugeben.

| Entscheidung | Empfohlener Default für neue Releases | Warum |
|---|---|---|
| Bestehende Position bei Regimewechsel | Unter ursprünglichem Exitplan weiterführen | Verhindert implizite Risikoänderung und unrealistischen Backtest |
| Fehlendes/unklares Regime | Keine neuen Entries | Fehlender Kontext soll nicht heimlich irgendeine Basis handeln |
| Unknown-Stop/Equity/Brokerzustand | Neue Risikoerhöhung sperren, Betreuung fortsetzen | Kein Erfolg aus fehlender Information ableiten |
| Manuelle Fremdpositionen | Keine automatische Änderung ohne Ownership/Freigabe | Schutzmodule dürfen nicht fremde Entscheidungen überschreiben |
| Erlaubte Dynamik | Zuerst kleiner Parameter-/Regimefilter, dann bewiesene Strategiewahl | Weniger Freiheitsgrade, bessere statistische Beurteilbarkeit |
| Rolle des LLM | Hypothesen/Erklärungen, strukturierte Vorschläge | Deterministische Grenzen und Nachweisbarkeit bleiben erhalten |
| Erstfreigabe Live | Explizit menschlich, eng begrenzter Scope | Forschungserfolg allein genügt nicht |
| Historische Altreports | Weiter lesbar, ungebunden/veraltet markieren | Rückwärtskompatibel und wahrheitsgemäß |
| Neue Datenquellen/Modelle | Erst bei nachgewiesenem Nutzen | Keine angrenzende Plattform neu bauen |

Später nötige Informationen: bevorzugte Assetuniversen/Handelshorizonte, tatsächlich aktive Modi, Umgang mit manuellen Positionen, Kapital-/Risikotoleranz, verfügbare Historie und Sandboxmöglichkeiten. Keine Zugangsdaten erneut in den Plan oder Chat kopieren.

## 4. Wissenschaftliche Einordnung

### Was ein Regime ist — und was nicht

Regime sind Modellkategorien zur Beschreibung bedingter Marktverteilungen. Eine nachträglich schön segmentierte Kurve ist nicht automatisch eine vorher handelbare Zustandsklassifikation. Ein Indikator erklärt Historie nicht deshalb kausal, weil seine Linie glatt aussieht.

Gute Forschung trennt:

- **Beschreibung:** Phasen, Charakteristika und rückblickende Referenzen.
- **Prognose:** Welche Information stand bei der Entscheidung zur Verfügung?
- **Entscheidung:** Verbessert die bedingte Strategie nach Kosten und Risiko die Ergebnisse?
- **Ausführung:** War der modellierte Trade mit realer Liquidität/Orderlogik überhaupt möglich?

Ein Detektor kann seine Labels korrekt/kausal berechnen und trotzdem keinen Tradingnutzen haben. Umgekehrt kann ein einfacher grober Kontextfilter sinnvoll sein, ohne besonders schöne Final-Übereinstimmung zu liefern.

### Suchvielfalt und Unabhängigkeit

Wer viele Indikatoren, Schwellen, Zeitfenster, Assets und Exitregeln ausprobiert, findet zwangsläufig überzeugende Zufallsergebnisse. Deshalb alle Suchversuche/Abstammungen dokumentieren, Auswahlvalidierung von finalem Test trennen und zeitliche/assetübergreifende Abhängigkeit berücksichtigen.

Ein stationäres/i.i.d.-Bootstrap über einzelne Trades kann bei Regimeclustern falsche Sicherheit geben. Block-/Session-/Episodeblöcke und paarweise gleiche Chancen sind besser begründete Ausgangspunkte. Keine einzelne statistische Kennzahl garantiert Generalisierung; reale Strukturbrüche bleiben möglich.

## 5. Fachquellen zur Vertiefung

Diese Quellen dienen der Methodik, nicht als Beleg für konkrete Codefehler. Codebefunde sind direkt am gepinnten Repository und den beigefügten Tests nachvollziehbar. Externe Referenzen wurden per Webrecherche identifiziert, die Paper nicht in dieser Analyse vollständig repliziert.

1. **Bailey, Borwein, López de Prado, Zhu – The Probability of Backtest Overfitting.** Begründung, weshalb die Auswahl vieler Backtests außerhalb der Stichprobe scheitern kann; PBO/CSCV.  
   https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
2. **Bailey, López de Prado – The Deflated Sharpe Ratio.** Korrektur für Selektionsbias und nichtnormal verteilte Renditen; keine Aufforderung, einen Sharpegrenzwert ungeprüft zur Livefreigabe zu machen.  
   https://davidhbailey.com/dhbpapers/deflated-sharpe.pdf  
   https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
3. **scikit-learn: TimeSeriesSplit.** Zeitgeordnete Splits und `gap`; wichtig: der Splitter kennt nicht automatisch Label-/Positionsüberlappung, Assetasynchronität oder reale Informationsverfügbarkeit.  
   https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
4. **Gepinnte Quellbasis:**  
   https://github.com/dean06greif-ai/KI-Trader/tree/792ff0ac44861df447d3e619042ecef1704c37c5

Nicht empfohlen: allein aufgrund dieser Links neue Bibliotheken installieren oder die vorhandene Engine ersetzen. Erst den fachlichen Vertrag und kleine Goldenszenarien stabilisieren; dann ein passendes geprüftes Werkzeug auswählen.

## 6. Schlussbewertung

Die erfolgversprechende Weiterentwicklung ist nicht „noch mehr KI“. Sie ist eine **einheitliche, überprüfbare Verbindung zwischen Daten, Experiment, Strategieversion, Freigabe, tatsächlichem Fill und daraus gelerntem Ergebnis**. Die vorhandene Architektur bietet genug Material dafür. Die richtigen nächsten Schritte sind kleine fachlich abgesicherte Korrekturen, danach ein begrenzter Laborpilot mit der ehrlichen Möglichkeit, die statische Basis als bessere Lösung beizubehalten.

---

<!-- Quelldokument: 08_TESTNACHWEISE.md -->

# 08 Testnachweise – Agentenprüfung und abschließende Nachprüfung

Stand: Commit-Quelle `/app/review_source @792ff0ac44861df447d3e619042ecef1704c37c5`  
Scope: **Backend-only / offline / isoliert** (kein Frontend, kein Serverstart, keine DB/Broker/LLM-Calls)

## Maßgeblicher finaler Stand

Nach der zweiten Agenteniteration wurden T02/T03 von nachmodellierten Branches auf **AST-extrahierte Originalbranches** umgestellt. Zwei fehlende Fake-Abhängigkeiten bzw. eine zunächst zu unspezifische Branchauswahl wurden ausschließlich im Testharness korrigiert. Die finale Wiederholung ist erfolgreich; keine Produktdatei wurde geändert.

- Gezielte Suite: **25 passed, 3 xfailed, 0 failed**; `test_reports/final_offline.xml`.
- Erweiterte Baseline: **59 passed, 3 deselected, 0 failed**; `test_reports/baseline_additional.xml`.
- Somit **84 bestandene Prüfungen** in diesen zwei maßgeblichen Läufen. Die früher separat ausgeführten4 Einzeltests sind Teil der59 und werden nicht doppelt gezählt.
- Die3 xfails sind2 erwartungsgemäß nicht erfüllte TP-Solltests und1 offene R05-Forschungsprobe.
- T02/T03 führen nun Originalcodebranches aus, weiterhin **nicht** die gesamte Order-/Brokerkette.
- Die Floating-Loss-Fixture besitzt im finalen Test konsistente OHLC-Werte.
- `dotenv` wird auch neutralisiert, wenn ein Plugin es bereits importiert hatte. Finale Läufe deaktivieren automatisches Pytest-Pluginladen und aktivieren nur den benötigten AnyIO-Runner.

### Finale Befehle (in der Analyseumgebung)

```bash
KI_TRADER_SOURCE_DIR=/app/review_source/backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p anyio.pytest_plugin \
 /app/analysis/tests/test_offline_extended_findings.py \
 /app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py \
 --confcutdir=/app/analysis/tests -q --tb=short \
 --junitxml=/app/test_reports/pytest/final_offline.xml

KI_TRADER_SOURCE_DIR=/app/review_source/backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p anyio.pytest_plugin /app/analysis/tests/baseline \
 --confcutdir=/app/analysis/tests -q \
 -k 'not performance_on_intraday_history and not scenario_bank_all_plausible and not wiring_decision_signal_trade' \
 --junitxml=/app/test_reports/pytest/baseline_additional.xml
```

### Erweiterte bestehende Baseline

Unveränderte Kopien von `test_regime_engine.py`, `test_regime_v2_and_gate_domain.py`, `test_regime_gate.py`, `test_policy_fingerprint.py`, `test_risk_budget.py` unter `tests/baseline/`.

Bewusst nicht ausgewählt: großer Performancefall, Szenariobank mit zusätzlicher Fixtureabhängigkeit, ein breiter Import-/Verdrahtungstest. Dies ist kein vollständiger Lauf aller bestehenden Unit-Tests.

Warnungen: Legacy-K-Means erzeugte `invalid value encountered in divide` (`regime.py:301`) → als **R16** aufgenommen, nicht ignoriert. In der gezielten Suite gab es zusätzlich einen Bibliotheks-Deprecationhinweis zur `python_multipart`-Importweise; keine Änderung von Bibliotheksversionen im Produkt vorgenommen.

Testumgebung (keine Empfehlung für ein Produktupgrade): Python3.11-Umgebung; pytest9.1.1, anyio4.15.1, numpy2.4.6, pandas3.0.5, fastapi0.110.1, motor3.3.1, aiohttp3.14.3. Ergebnisse gelten für diese lokale Testumgebung, nicht als Beweis identischer Produktivabhängigkeiten.

Die nachfolgenden Abschnitte dokumentieren die vorherige Agenteniteration; bei Abweichungen gilt der finale Stand oben.

## Isolations-Setup
- Tests ausschließlich unter `/app/analysis/tests` ausgeführt.
- `pytest --confcutdir=/app/analysis/tests` verwendet.
- Import-/Laufzeit-Netzwerk im Testprozess via Socket-Blocker gesperrt.
- Umgebungsvariablen mit Credential-Mustern im Testprozess entfernt.
- `dotenv.load_dotenv` im Testprozess neutralisiert (keine `.env`-Nutzung durch Testharness).
- Verlagerung durchgeführt:  
  `/app/review_source/backend/tests/test_regime_dynamic_r01_r13_regressions.py` → `/app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py`

## Lauf A – Isolierte Regressionssuite
Command:
`pytest -v /app/analysis/tests --confcutdir=/app/analysis/tests --junitxml=/app/test_reports/pytest/iteration_2_offline.xml`

Ergebnis: **25 passed, 3 xfailed, 0 failed**

### Neue Befunde (T01–T05, R08, R05, R14)

#### T01 – Watchdog-Adoption kann fremde Vollposition schließen
- Test: `test_t01_watchdog_adopt_closes_full_foreign_position_without_identity_proof`
- Status: **passed** (Reproduktionsbeweis)
- Repro-Werte:
  - kürzlich geschlossener BTC LONG vorhanden
  - neue Position `position_id=new-foreign-pos-99`, `qty=5.0`, `reg=None`, `registry.find_match=None`
- Beobachtung:
  - `flash_close(..., full=True)` wird für die neue Position aufgerufen, ohne Identitäts-/Dust-Nachweis.

#### T02 – Recovery >=50% untracked akzeptiert, Qty-Semantik bleibt voll
- Test: `test_t02_exception_recovery_branch_accepts_half_fill_but_keeps_planned_qty_semantics`
- Status: **passed** (Branch-Repro)
- Repro-Werte:
  - `qty=10`, `untracked=6` → recovered `{"code":0}`
  - `qty=10`, `untracked=4` → reject-Pfad
- Beobachtung:
  - Bei 60% untracked wird Erfolgsantwort gesetzt, geplante qty bleibt 10.

#### T03 – SL-Check: `_ensure_live_sl == None` setzt `sl_exchange_missing` nicht
- Test: `test_t03_sl_verification_none_does_not_set_sl_exchange_missing_and_countercases`
- Status: **passed**
- Gegenfälle:
  - `sl_ok=None` → `sl_exchange_missing=False`
  - `sl_ok=True` → `False`
  - `sl_ok=False` + Close-Fehler → `True`

#### T04 – `_setup_live_gate` lässt in mehreren Pfaden durch
- Test: `test_t04_setup_live_gate_allows_missing_setup_exception_path_and_default_bypass`
- Status: **passed**
- Repro-Werte:
  - missing `setup` → `None` (durchgelassen)
  - Exception in `cached_setup_stats` → `None` (durchgelassen)
  - Bypass default: confidence 70, min_conf 65, opened_today 0 → `live_gate_bypass=True`

#### T05 – Policy-Fingerprint unvollständige Policy-Identität
- Test: `test_t05_policy_fingerprint_ignores_min_conf_fee_guard_and_regime_config_but_tracks_real_sizing_change`
- Status: **passed**
- Beobachtung:
  - Änderungen an `min_confidence`, `fee_guard_enabled`, `regime_mode` ändern `sizing_hash`/`combined` nicht.
  - Echter Sizing-Change (`risk_per_trade_pct`) ändert Hash.

#### R08 – Optimistischer Same-Bar-TP-PnL + offene Trades fallen aus Metrics
- Tests:
  - `test_r08_same_bar_tp1_tpf_characterizes_optimistic_pnl[...]` (LONG/SHORT)
  - `test_r08_open_trade_with_floating_loss_and_entry_fee_still_reported_as_zero_metrics`
  - Soll-Abnahme: `test_r08_acceptance_same_bar_tp_should_be_sequential_not_optimistic[...]`
- Status:
  - Charakterisierung: **passed**
  - Soll-Abnahme: **xfail (strict)**
- Repro-Werte:
  - LONG: entry 100, TP1 101, TPF 103, SL 90, qty 2, partial 50%, fee 0, bar high 104/low 99
  - Beobachtet: aktueller PnL 6 (statt sequenziell 4)
  - SHORT symmetrisch: ebenfalls 6 statt 4
  - Offener Trade-Endfall mit Floating-Loss und Entry-Fee-Kontext berichtet `trades=0, pnl=0, fees=0`

#### R05 – Prefix-Invarianz (kausal) vs. retrospektive Final-Labels
- Test: `test_r05_prefix_invariance_causal_vs_retrospective_labels_with_fixed_model_and_config`
- Status: **xfail**
- Ergebnis:
  - Kausale Labels (`classify_series`) sind prefix-invariant bei fixem Modell/Config (nachgewiesen).
  - Forschungsanteil „final_labels ändern sich durch Future-Append“ war auf diesem synthetischen Datensatz nicht robust nachweisbar → xfail markiert, **nicht als behoben gewertet**.

#### R14 – Candle-Cache Tail-Start kann letzte offene Minute nicht aktualisieren
- Test: `test_r14_candle_cache_tail_start_skips_last_open_minute_update`
- Status: **passed**
- Repro-Werte:
  - Cache-Ende `ts=70000`, Tail-Start wird `70000+60000`
  - Update derselben letzten Minute (`ts=70000`) wird nicht erneut angefordert
- Beobachtung:
  - Letzte Kerze bleibt mit altem Wert im Cache.

### Re-Run der 16 bestehenden Charakterisierungstests (R01–R13-Datei)
- Datei: `/app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py`
- Status: **16/16 passed** (Reproduktionsbeweise, keine Qualitätsfreigabe)

## Lauf B – Auswahl bestehender reiner Unit-Tests (ohne conftest)
Command:
`PYTHONPATH=/app/review_source/backend pytest -v --noconftest ...`

Ausgeführt:
- `test_policy_fingerprint.py::test_short_hash_key_order_stable` → passed
- `test_policy_fingerprint.py::test_sizing_hash_reacts_only_on_sizing_keys` → passed
- `test_risk_budget.py::test_trade_risk_usdt` → passed
- `test_risk_budget.py::test_open_risk_filters_mode_and_collection` → passed

Ergebnis: **4 passed**

## Grenzen / Stub-Grenzen
- T02 und T03 enthielten in der Agenteniteration nachmodellierte branch-nahe Reproduktion; der finale Stand oben ersetzt sie durch AST-extrahierte Originalbranches. Der vollständige Live-Order-Flow bleibt außerhalb Scope.
- T04 wurde per AST-extrahierter Originalmethodik mit injizierten Fake-Abhängigkeiten geprüft (kein Import von `ai_engine`-Modul notwendig).
- R05-Forschungsanteil (final_labels-Änderung) ist als **unbewiesen auf diesem Datensatz** markiert (xfail).

## Frontend-Hinweis (Scope-Korrektur)
- Frontend war hier explizit out-of-scope.
- Der frühere Placeholder-Befund aus iteration_1 wird als **false positive für die Nutzerwebsite** zurückgenommen.


---

<!-- Quelldokument: 09_ZWEITPRUEFUNG_EINORDNUNG.md -->

# 09 – Unabhängige funktionale Zweitprüfung und Einordnung

Eine separate funktionale Codeprüfung wurde auf derselben Quellkopie angefordert. Sie war **read-only**, kein Sicherheitsaudit, ohne Produktionsdaten oder Brokerzugriff. Schwerpunkt waren KI-Entscheidung, Orderausführung und Backtest-/Live-Abweichung. Dies ist eine redaktionelle Zusammenfassung und Nachprüfung, kein unverändertes Volltranskript des Agentenoutputs.

## Wesentliche zusätzliche Hinweise

| Hinweis der Zweitprüfung | Eigene Verifikation | Finaler Umgang |
|---|---|---|
| Watchdog kann fremde gleichseitige Vollposition nach kürzlichem Bot-Close schließen | Quellpfad geprüft; AST-extrahierte Originalmethode mit anderer Positions-ID/Qty ausgeführt | T01, P0, Originalfunktionsnachweis |
| Timeout-Recovery übernimmt geplante Qty bei nur teilweiser Börsenmenge | Marketbranch gelesen, gegenüber Maker differenziert; schließlich Originalbranch per AST geprüft | T02, P0, begrenzter Branchnachweis |
| Stop-None wird nicht als missing markiert | Original-If-Branch mit None/True/False geprüft | T03, P0; dreistufiger Status statt blindem Booleanfix |
| Backtest-Reifeboost und Ausführungsmodell sind nicht gleichbedeutend mit realer Live-Reife | `setup_backtest/weights.py`, `policy_promotion.py`, Setup-Gate gelesen | W02/T04: Vorhandene Caps/Mindest-Paperbasis ausdrücklich anerkannt; statistisches Risiko statt pauschal fehlender Guards |

## Warum die Zweitprüfung nicht unverändert als Wahrheit übernommen wurde

- Ein Codepfad kann gute Vorbedingungen haben, die das Fehlerfenster begrenzen. Beispielsweise prüft der Watchdog zunächst eine Entry-Registry; dies wurde im Finalbefund berücksichtigt.
- Ein möglicher Fehler ist kein Beleg real eingetretener Verluste. Ohne tatsächliche Konfiguration/Fills bleibt die Produktivhäufigkeit unbekannt.
- Der im Code beschriebene Segment-End-Close existiert nicht in der tatsächlich aufgerufenen Simulation. Eigene Prüfung und numerische Tests hatten Vorrang vor Kommentaren.
- Teilweise oberflächen-/sandboxbezogene Aussagen sind nicht belastbar ohne die tatsächliche externe Anwendung. Die unbenutzte Startervorschau wurde ausdrücklich aus dem Produktbefund ausgeschlossen.
- Testerempfehlungen, jetzt Codefixes vorzunehmen, wurden **nicht ausgeführt**, weil der Nutzer nur Analyse und Plan freigegeben hat.

## Lieferumfang nach Nachprüfung

Der finale Katalog enthält getrennte Evidenzgrade. Die Tests liegen außerhalb des Quellrepositories. Die Quellkopie entspricht weiterhin dem gepinnten Commit. Weitere Analysedateien können für die nächste KI direkt als Arbeitsgrundlage genutzt werden, ohne Zugangsdaten oder produktive Konfigurationen zu übertragen.