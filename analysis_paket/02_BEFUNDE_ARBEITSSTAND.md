# Arbeitsstand der Befunde (noch vor abschließender Testvalidierung)

Basis: Commit `792ff0ac44861df447d3e619042ecef1704c37c5`. Alle Pfade relativ zum unveränderten Quellrepo. Keine Aussage über tatsächliche Produktivkonfiguration oder real eingetretene Verluste.

## Regime und dynamische Strategien – eigene Quelltextprüfung

### R01 – Discovery-Substrategien werden nicht als Live-Regeln angewandt
`backend/routers/regime_lab.py:389–438` erzeugt bei Discovery nur aus der ersten Definition eine Basisstrategie; speichert weitere Definitionen unter `sub_strategies`. `backend/services/dynamic_live.py:107–108` liest deren Regeln für die Anzeige, `273–278` wählt aber nur `apply_regime_strategies` oder `apply_configs`; letzteres schreibt ausschließlich Trade-Overrides. Repositoryweite Verwendungs-Suche zeigt keinen ausführenden Verbraucher für `active_sub_strategy`. Folgerung: Backtest kann verschiedene Definitionen simulieren (`regime_opt.py:451–479`), Live handelt weiter die Basisdefinition. Gemischte Registry-/Discovery-Zuordnungen verlieren zusätzlich den vollständigen `regime_strategies`-Pfad. Gezielte Tests erforderlich.

### R02 – Baseline-Rückkehr und partielle Overrides hinterlassen vorherige Regimewerte
`dynamic_live.py:171–185`: leere `active_config` wird als Baseline protokolliert und übersprungen, ohne vorangegangene Overrides zu entfernen. Bei nichtleeren Configs werden nur enthaltene Keys überschrieben. `207–218,245–257` hat dasselbe Merge-Problem für Strategieparameter. Neuer Zustand ist damit pfadabhängig, statt vollständig aus Basis + freigegebenem Regime abgeleitet.

### R03 – Übergangsschutz wirkt über die eigene dynamische Strategie hinaus
`dynamic_live.py:294–328`: `close_open` sucht alle offenen Trades der Symbole, ohne strategy_id/dynamic_id/mode einzugrenzen. `check_one:340–348` führt den Übergangsschutz selbst ohne Auto-Apply bzw. vor einer erforderlichen Bestätigung aus. Prüfen: damit kann reines Aktualisieren unerwartet andere Positionen schließen. Vorhandener `trade_guard.py` konsumiert die Sperren ebenfalls symbolbezogen (noch im Detail prüfen).

### R04 – Apply-Fehler wird nach Fortschreiben des erkannten Zustands nicht erneut versucht
`dynamic_live.py:349–367`: persistiert `last_state`, dann Apply in try/except; beim nächsten Zyklus sind keine `switches` mehr vorhanden und Apply wird nicht wiederholt. Beobachteter Zustand und tatsächlich angewandter Zustand sind nicht getrennt.

### R05 – Rückblick-Labelauswahl für Strategie-Suche und Holdout-Grenze
`regime_lab.py:255–279` speichert `segments` aus `final_labels` der gesamten Datenreihe. `890–910` und `regime_opt.py:79–95,165–173` verwenden diese Segmente für Training; nur Zeitgrenzen werden abgeschnitten. Eine nach Trainingsende bestätigte Umkehr kann somit rückwirkend Trainingslabels ändern. Final-Labels sind als diagnostische Referenz sinnvoll, aber nicht gleichbedeutend mit handelbaren Regimen. End-Walkforward benutzt kausale Labels (`regime_opt.py:421–433`); diese gute Trennung am Ende behebt weder Trainingskontamination noch Such-/Live-Unterschied allein.

### R06 – Gespeicherte Historie ist durch Wiederabruf relativ zu jetzt nicht reproduzierbar
`regime_lab.py:55–78`: ruft `fetch_history(... days ...)` ohne historischen Start/Endanker auf; anschließend wird nur `<= end_ts` gefiltert. Spätere Optimierung oder Holdout-Auswertung verliert damit den vorderen Teil des ursprünglichen Fensters. Keine Daten-Snapshot-ID/Prüfsumme im Analysedokument `407–427`.

### R07 – Regime-Zeitintervalle enthalten die erste Kerze des Folgeregimes
`_segments_payload:120–126` speichert als `to_ts` den exklusiven Endindex `e`, während `segments_from_ranges:919–925` `bisect_right` benutzt. Damit wird diese Kerze in der wiederhergestellten vorherigen Phase eingeschlossen. Mindestlänge 10 bei Testfixtures berücksichtigen.

### R08 – Walkforward bildet andere Positionsübergänge ab als Live
KORREKTUR nach Prüfung der tatsächlichen Simulation: `dynamic_strategy.py:12–13` BEHAUPTET Schließen am Regimeende, aber `backtester.py:404–460,462–535` lässt `open_t` am Datenende vollständig aus Trades/PnL/Fees fallen. Keine Zwangsschließung vorhanden! `dynamic_strategy.py:237–247` verwirft Warmup-Trades erst nach Simulation. Live standardmäßig `transition_mode=block_new` (`dynamic_live.py:301`) und hält bestehende Positionen. Zusätzlich ignoriert `backtester.py:347–370` TP1, wenn TPFull in derselben Bar erreicht wird. Erster Test belegt übersprungenes TP1; numerische Prüfung mit VERSCHIEDENEN TP1-/TPFull-Preisen wird nachgefordert. Unzugeordnetes Regime: `eval_dynamic:274–277` handelt Basis; Multi-Strategie live deaktiviert alle (`239–245`).

### R09 – Freigabe/Build ist nicht an aktuelle bestandene Evidenz gebunden
`routers/regime_lab.py:340–371` akzeptiert Kandidatendaten und ersetzt Zuordnungen ohne bestehendes Walkforward-Ergebnis zu invalidieren; `376–448` kopiert vorhandenen Verdict ohne Pflicht auf bestandenen aktuellen Test. Auto-Check und Auto-Apply sind zum Glück standardmäßig aus. Kein Plan für blindes Auto-Go-Live, sondern versionsgebundenes Promotion-Gate erforderlich.

## Unabhängige Zweitprüfung – noch selbst zu verifizieren
- T01: `position_watchdog.py:658–681` kann gleichseitige fremde Position binnen 30 Minuten eines Bot-Close als Rest behandeln und voll schließen; keine Mengenschwelle/Positionsidentität.
- T02: `bitunix_trade.py:2395–2399` übernimmt nach Timeout bei >=50% untracked Füllmenge weiterhin geplante statt gefüllte qty.
- T03: `bitunix_trade.py:2510–2512,2540–2547,2867–2891`: unbestätigter SL (`None`) wird nicht als unbestätigt markiert.
- T04: Backtest-Reifeboost trotz unterschiedlicher Backtest-/Paper-Fillmodelle. Vorhandene Mindestanzahl echter Paper-Trades und Caps ausdrücklich anerkennen.

## Noch offene Prüfungen
KI-Entscheidungs-/Lernkette, Kerzenabschluss und Timeframe-Alignment, Kalibrierung/Regimefeatures, UI-Zustände, Gate-Domänen, Worker-Provenienz, statistische Robustheit, vollständige Testbelege und finale Priorisierung.

## Zweiter Zwischenstand / erste Testiteration
- 16 gezielte Charakterisierungstests bestehen und reproduzieren die gemeldeten Problemstellen. Das ist KEIN grüner Qualitätsstatus der Handelsanwendung.
- Noch keine numerische vollständige Live-/Final-Prefixprüfung: erster Test stubbt Labeler und beweist lediglich die Verkabelung. Strikt als solche kennzeichnen.
- Der Tester hat unbeabsichtigt die generische Arbeitsumgebungs-Vorschau angesehen. Deren Startseite ist NICHT das externe Nutzerprodukt; dies wird ausdrücklich NICHT als Fehler der Nutzerwebsite gewertet.
- Tests wurden zunächst als zusätzliche Datei in der separaten Quellkopie angelegt. Sie werden vor Abschluss nach `/app/analysis/tests/` ausgelagert, sodass auch diese Kopie wieder dem Commit entspricht.
- Zusätzliche Belege: `regime_lab.py:531–535` wählt EMA-Periode anhand Holdout; `638–665` optimiert Kombi-Score ebenfalls darauf. Dieses Fenster ist dadurch Validierung, kein unberührter Abschlusstest.
- `timeframes.py:75` hat `drop_partial=False`; `dynamic_live.py:41,97` und `regime_gate.py:65` nutzen diesen Standard. Unfertige aktuelle Timeframe-Kerzen werden einbezogen.
- `ai_market_observer.py:154–185` liefert eine ANDERE Regime-Taxonomie (`trend_up`, `trend_down`, `range`, `breakout`, `drift` × Volatilität), unabhängig von Lab-Konfiguration. `ai_engine.py:2149–2157` filtert auf diesem Observer-Regime.
- `ai_engine.py:1956–2034`: Setup-Live-Gate lässt fehlendes Setup und Ausnahme durch; Default `live_gate_bypass_enabled=True` erlaubt begrenzt unreife Setups anhand hoher, nicht als Wahrscheinlichkeit kalibrierter LLM-Konfidenz. Nicht mit absoluter Live-Reifesicherheit gleichsetzen.