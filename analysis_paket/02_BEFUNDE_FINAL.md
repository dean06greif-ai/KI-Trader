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