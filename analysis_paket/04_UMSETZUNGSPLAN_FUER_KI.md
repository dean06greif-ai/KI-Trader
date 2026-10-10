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