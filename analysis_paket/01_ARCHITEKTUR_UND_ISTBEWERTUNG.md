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