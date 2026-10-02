# KI-Trader / Crypto Scanner – PRD (Arbeitsstand)

## Ursprüngliche Aufgabe (09/2026)
Bestehende, produktive Daytrading-Website (extern auf Render, Repo `dean06greif-ai/KI-Trader`, Branch
`conflict_290926_1824`) sauber, modular und rückwärtskompatibel verbessern – Originalstruktur beibehalten:
1. Frontend-Build auf Render reparieren (Syntaxfehler RegimeLab.js:455).
2. Lokaler Worker: Kumpel bekam alten Datenordner-Pfad (C:\Users\dean0\…) → WinError 5.
3. Regime-Lab: 5 Regime auf 1h → Ø Phasendauer zu kurz; Bestscore sank über Zeit; Ladebalken = Score.
4. Walk-Forward-Ergebnis erklären.
5. Dynamische Strategien wie normale Strategien je Asset wählbar (Label), Blitz mit Live/Paper,
   Kapital & Einstellungen je Regime (Reiter + „auf alle übertragen“), Verlauf mit Label + Phasen-Banner.
6. Strategie-Optimizer: dynamische Strategien optimieren (gesamt / einzelne Regime, endlos),
   neu erstellen aus bestehenden Strategien + Regime-Erkennung, neue Strategie je Regime finden.

## Nutzer-Entscheidungen
- Entwicklung/Tests nur mit separater lokaler Test-DB.
- Regimewechsel bei offenem Trade: „was trading-technisch am meisten Sinn macht“ → Standard: offene Trades
  der dynamischen Strategie schließen (identisch zu Backtest/Walk-Forward), umschaltbar auf „weiterlaufen lassen“.
- Regime ohne Strategie: keine neuen Trades.

## Architektur (neu, modular)
- `backend/strategies/dynamic_regime_strategy.py` – DynamicRegimeStrategy (BaseStrategy): delegiert je Symbol an
  die Strategie des aktuellen Regimes über `strategy_plan.resolve_symbol_plan` (eine Quelle der Wahrheit).
- `backend/strategies/registry.py` – `upsert_dynamic / remove_dynamic / is_dynamic`.
- `backend/services/dynamic_runtime.py` – Registrierung beim Boot, Live-Regime-Zustand (RAM + `runtime_state`),
  Refresh-Loop nur für aktiv gehandelte dynamische Strategien, Regimewechsel-Handling, `regime_trade_cfg`.
- `backend/services/dynamic_workbench.py` + `/api/dynamic-workbench/*` – refine/create/discover über bestehende
  Regime-Lab-Bausteine (regime_opt, assign, walkforward, build); cloud oder lokaler Worker.
- `bitunix_trade._on_signal_impl` – regime-spezifische Blitz-Einstellungen (`regime_configs`), Trade-Tag `dynamic`.
- Backtester/Optimizer-Params schließen dynamische Strategien aus (eigene Test-Wege).
- Frontend: `DynamicTradeTag` (DYN-Label, Phasen-Banner), `DynamicRegimeTabs` (Blitz-Reiter), `DynamicWorkbench`
  (Optimizer → Dynamische Strategie), `WalkForwardExplain` (Regime-Lab).

## Umgesetzt (29.09.2026)
- Build-Fix RegimeLab.js (fehlendes `<div className="opt-compare">`), `CI=true yarn build` grün.
- Worker: kein Fallback mehr auf globalen Fremdpfad; Worker loggt nicht anlegbaren Pfad nur einmal (Worker 1.16.1).
- Autopilot: Phasen-Strafe nutzt das kürzere Maß (Regime- vs. Richtungs-Phase); Gleichstands-Übernahme nur bei
  gleichem/höherem Score (keine Drift nach unten); Balken = exakt Bestwert-Score, Limit-Fortschritt separat.
- Regime-Opt: optionaler `seed` (Endlos-Suche variiert je Runde).
- Dynamische Strategien als handelbare Strategien inkl. Blitz-Reiter je Regime, Wechsel-Verhalten, DYN-Label,
  Phasen-Banner in Signal-Panel, Trade-Karte und Deep-Analytics.
- Dynamik-Werkbank im Optimizer; Walk-Forward-Erklärung im Regime-Lab.
- Regressionstests: `backend/tests/test_dynamic_trading_0926.py` (12), Testagent iter30 (72/72).

## Backlog
- P1: Regime-Lab-UI: Werkbank-Ergebnis direkt als Walk-Forward-Karte verlinken; Optimizer-Werkbank für per-Coin-Scope.
- P1: Chart-Marker: DYN/Phase im Trade-Tooltip (MainChart).
- P2: Performance-Auswertung je Regime für live gehandelte dynamische Strategien.
- P2: Code-Splitting (Bundle 625 KB gz).

## Regime-Check KI-Trader (30.09.2026)
- Aktives Krypto-Regime „9 Regime Krypto 1h Beste Referenzz“ (ra_e866c510) geprüft: Richtungs-Phase Ø 7,4 T (Median 5,1),
  Sweet Spot 5–15 T erfüllt, Validation passed (Verstoß 1,21 %), 13/13 Coins Kalibrierung ok → behalten.
- 5-Regime-Variante: Richtungs-Phase Ø 5,36 T (Median 4,0) → knapp am unteren Rand, 64 % ein Seitwärts-Topf.
- Hinweis: Walk-Forward der Strategie-Zuordnung (NNFX überall) negativ (−659, Gebühren 1450) – betrifft Zuordnung, nicht Erkennung.
- Keine Codeänderung.

## Autopilot-Schutz innere Val. (30.09.2026)
- Bug: Rohstoff-Autopilot übernahm Jump-Modell mit innerer Val. 80,9 → 65,8 % (Score zählt Live=Final nur ~25 %).
- Fix: `regime_autopilot.MAX_INNER_DROP_PP = 5` – Varianten mit Einbruch > 5 Pkt. werden in der Suche verworfen
  (`guard_rejected`) und nie automatisch übernommen (`inner_regressed`, Backend + `autopilotDecision`).
- Sweet Spot vereinheitlicht auf Benchmark 5–15 Tage (`regime_quality.SWEET_SPOT_DAYS` = `RECOMMENDED_BAND`, UI-Default 5/15,
  einmalige Migration alter 4/14-Einstellungen via `phaseV`).
- Banner: Score vorher → nachher, „Selbst-Übereinstimmung (innere Val.)“, Ø Richtungs-/Live-Phase, verworfene Varianten.
- Tests: tests/test_autopilot_inner_guard_3009.py, jest regimeLabHelpers (iteration_31: 100 %).
- Backlog: Render-Deploy nötig; Nutzer setzt Rohstoff-Kalibrierung selbst zurück und startet Autopilot neu.

## Dynamik-Werkbank v2 + Dynamische Strategien im Backtester (30.09.2026, Branch conflict_300926_0800)
Anforderung: Werkbank optisch/strukturell an den Optimizer angleichen, klassischen Dynamik-Pfad aus der UI nehmen,
fehlende Einstellungen (Timeframe, Zeitraum, Zeitfenster, Kapital, Hebel, Gebühren, Ziel, Gruppen, Indikatoren,
Walk-Forward je Regime, Richtungs-Bias) ergänzen, Ergebnis unten wie bei den anderen Modi inkl. Aufteilung je Regime
+ Empfehlung (nicht handeln / andere Strategie), Equity je Regime auf Klick, Suchbalken nur im startenden Reiter,
Auswahl bleibt beim Reiter-Wechsel erhalten; dynamische Strategien im Backtester mit Aufschlüsselung je Regime.
- Backend neu: `services/dynamic_backtest.py` (simulate_dynamic: Regime rückblickend je Kerze, Sub-Strategie/Config
  je Regime über strategy_plan, Gesamt + je Regime + Alternativen-Check + `recommend()` + Equity-Punkte mit Regime).
- `services/backtester.run_backtest`: dynamische IDs laufen über dynamic_backtest, Ergebnis `dynamic_breakdown[did]`;
  statischer Pfad unverändert. `routers/backtest.run`: dynamische IDs erlaubt (nur Cloud).
- `services/dynamic_workbench`: `PASSTHROUGH_KEYS` (timeframe, days, max_capital, leverage, fee_percent, sessions,
  optimize, indicators, regime_walk_forward, regime_train_pct, deep_test) an regime_opt; Ergebnis enthält
  `walkforward.per_regime/switches` und `backtest` (Ergebnis-Backtest der gebauten Strategie, abschaltbar).
- `services/regime_opt`: `limit_segments_to_days` (Zeitraum-Parameter), `sessions` in cfg.
- Frontend: `DynamicWorkbench.js` (Reiter-State je Reiter, localStorage `dwb_ui_state_v2`), `DynamicWorkbenchFields.js`,
  `DynamicWorkbenchResult.js`, `DynamicRegimeBreakdown.js` (auch im Backtester), `IndicatorPicker.js` (Optimizer + Werkbank),
  `constants/optimizerOptions.js` (OBJECTIVES/OPT_GROUPS/DAY_OPTIONS geteilt). Optimizer: Dynamik-Modus zeigt nur noch
  die Werkbank (klassischer Block/Result ausgeblendet, Backend-Pfad bleibt kompatibel).
- Tests: `backend/tests/test_dynamic_backtest_workbench.py` (13 unit).
- Offen/Backlog: lokaler Worker kennt den Dynamik-Backtest nicht (Cloud-only); Workbench-Result nach Server-Neustart
  nur im RAM (JOBS).

## Prüfung Regime-Lab ↔ dynamische Strategien + Worker-Log (30.09.2026, Branch conflict_300926_1710)
Bericht: `REGIME_DYNAMIK_PRUEFBERICHT_3009.md`. Produktiv-DB nur lesend geprüft, Preview mit lokaler Test-DB.
- D1 (kritisch): Live-Regime dynamischer Strategien auf 30 statt 142 Tagen Warmup → 51–65 % andere Regime als im Test.
  Fix `dynamic_live.history_days` (Detektor-Warmup wie KI-Trader) in `refresh_state` (Runtime, Watcher, manuell).
- D2: Regime-Suche/Werkbank trainierte auf Rückblick-Phasen. Neu `label_basis` (Standard `live` = kausale
  `live_segments`, `final` = alt), UI-Auswahl in RegimeOptimizePanel + Werkbank (`LabelBasisSelect`).
- D3: Walk-Forward handelt nur noch bestätigte Regime (`untraded_bars`).
- D4: Bau immer expliziter Multi-Modus (`strategy_plan.regime_strategy_map`), Legacy-Apply schaltet Regeln um.
- D5: Dynamischer Backtest lädt Detektor-Warmup vor dem Fenster.
- Worker 1.16.2: gemeinsamer Verbindungszustand + 20 s Gnadenfrist + kurze Gründe; Manifest-Ausschluss/Dukascopy-Abbrüche
  gedrosselt (6 h). Verhalten unverändert.
- Tests: `backend/tests/test_regime_dynamic_consistency_3009.py` (18), iteration_33 (53/53 Unit, 5/5 E2E, Frontend ok).
- Backlog: P1 bestehende gemischte Dynamik-Dokumente optional migrieren; P1 Werkbank: Regime mit negativem Walk-Forward
  automatisch „nicht handeln“ vorschlagen; P2 Render-RAM beobachten (142 d Historie je Dynamik-Symbol, geteilt mit KI-Trader).

## Folge-Features (30.09.2026)
- F1 Datenlücken reparieren: Worker-Panel Button je Coin (`lw-data-repair-<SYM>`) → `POST /api/localworker/data/repair`
  (Primär- + zweite Quelle, Worker ≥ 1.17). UI-Fix: `local_exec.ui_data_info` formt Worker-Daten für die Tabelle
  (symbols als Zeilen, `dir`, `total_bytes`); Worker liefert Kerzen/Zeitraum via `candle_cache.disk_meta`.
- F2 Live-Ergebnis je Regime: `dynamic_performance.py`, `DynamicRegimePerformance.js` (Live vs. Walk-Forward).
- F3 Verlust-Regime abschalten: Werkbank/Build `skip_regimes` bei negativem Walk-Forward (`regime_opt.py`, `dynamic_workbench.py`).
- Tests: `test_regime_followups_3009.py`; iteration_34 (Backend 33/33), iteration_35 (Frontend 3/3).
- Testagent-Dateien dieser Sitzung (test_iter33_regime_lab_e2e, test_iter34_followups, pytest/iter33/34.xml) entfernt.
- Backlog: P2 React-Warnung `<span>` in `<option>` bereinigen; P2 Code-Splitting.

## MarketMaker (MM) · Verlauf · Lektionen · Regime-Copilot (01.10.2026, Branch conflict_300926_1955)
Anforderung: Lektionen prüfen + fixen; Verlauf-Reiter aufräumen (Mindest-Trade weg, ein Welt-Reiter für Chart +
Setup-Reife inkl. „Live-Logik gesamt“, doppelte Karten weg, Maus-Drag-Scroll); Umbenennung MarketMaker (MM);
Regime-Copilot mit aktuellem Regime-Wissen (Taxonomie, Freigabe, KI-Trader-Anbindung, dynamische Strategien).
- Bericht: `PRUEFBERICHT_LEKTIONEN_VERLAUF_0110.md`.
- Backend neu: `services/setup_world_stats.py` (Setup-Reife je Welt, `worlds` je Zeile), `services/regime_copilot_knowledge.py`
  (Wissen aus Code-Konstanten + Live-Ist-Stand). Fixes: Gegenprobe-Warteschlange (no_frame), Gegenprobe-Gebühr (Forex/konfiguriert),
  Bilanz-Beitrag bei wenigen angewendeten Trades, Equity-Kurve nur `closed` + ohne 5000-Kappung, `regime_key` 9er-Seitwärts eindeutig.
- Frontend: AIEquityPanel (ein Welt-Reiter), SetupMaturityTable (worldStats), `.drag-scroll-x`, MinTradeOverview entfernt, Branding.
- Tests: `backend/tests/test_mm_verlauf_regime_copilot_0110.py` (16), iteration_36 (Backend + Frontend 100 %).
- Backlog: P1 Lektions-Bilanz-Vergleichsgruppe zusätzlich nach Struktur-Regime; P2 Code-Splitting; P2 veraltete Alt-Tests aufräumen.

## Regime-Filter Lektionen, Regime-Risiko Shadow, Autopilot-Warmstart, Manifest-Fix (01.10.2026)
- Lektionen-Bilanz: Vergleichsgruppe nur im gleichen Struktur-Regime (`lesson_impact.regime_matched_control`, Fallback Klasse/Zeitraum, `control_basis`).
- Regime-Risiko Shadow: `services/regime_risk_shadow.py`, `GET /api/ai/regime-risk/shadow`, Panel `RegimeRiskShadow` im Verlauf – nur Beobachtung.
- Autopilot-Warmstart: `services/regime_warmstart.py` sammelt Seeds (frühere Läufe/Analysen), `regime_autopilot.warm_seeds`, Schalter im UI.
- Bug „Kerzenanzahl ≠ Manifest“ (HYPEUSDT 8632 vs 8759): Ursache = Tages-Deckel 365 d ab JETZT schnitt den Anfang gespeicherter Fenster ab.
  Fix: `candle_cache.get_candles(start_ms=…)` / `backtester.fetch_history(start_ms=…)`; `regime_lab.fetch_histories` reicht den Anker durch. Manifest-Prüfung bleibt streng.
- Worker 1.17.2 (Paket neu laden). Tests: tests/test_manifest_anchor_0110.py; Testagent iteration_37: 100 %.

## Dynamik-Werkbank: Asset-Auswahl + effizientere Suche (01.10.2026)
- Asset-Chips klickbar in allen 3 Reitern (`AssetToggle`, mind. 1 aktiv, Analyse-Wahl = alle aktiv, Refine-Default = Assets der Strategie).
- Teilmengen getrennt gespeichert: `subset_assignments` / `subset_walkforward` (Schlüssel `combined@BTCUSDT+ETHUSDT`, `lab.area_key/area_fields/norm_subset/dataset_for`);
  Manifest-Prüfung nur für gewählte Assets; gebaute Strategie: `symbols` = Teilmenge, `settings.subset_symbols`.
- Effizienz: Kerzen je Werkbank-Lauf einmal laden (`regime_opt._HISTORY_SLOT`, reuse_key = Job-ID, Freigabe am Job-Ende);
  Regime ohne Daten/fehlgeschlagene Suche bricht den Lauf nicht mehr ab; Plateau-Steuerung (2 Runden ohne Verbesserung → nur jede 4. Runde, früheres Ende ohne Endlos);
  Walk-Forward nutzt frisch gespeicherte Zuordnungen (vorher beim lokalen Worker veraltet); Warnhinweis bei unrealistischem Min. Trades.
- Worker 1.18.0 für Teilmengen lokal (`WB_SUBSET_WORKER`, 409 bei älteren). Tests: tests/test_workbench_assets_0110.py; Testagent iteration_38: 100 %.
- Backlog: Regime parallel rechnen (RAM auf Render beachten); Code-Splitting.

## Iteration 01.10.2026 (Branch conflict_300926_2220)
Aufgabe: Asset-Vorschlag je Regime (Werkbank), bis zu 2 lokale Worker parallel mit Job-Zuweisung,
Ursache „KI-Trader-Setups handeln kaum“ finden & fixen, KI-Chat soll Setup-Befehle selbst ausführen.
Nutzer-Entscheidungen: Vorschlag = bisherige Ergebnisse + Marktdaten; Job wird einem Worker zugewiesen;
Prod-DB nur lesend; Chat führt Befehle direkt aus.
Umgesetzt (Details: /app/VERBESSERUNGEN_0110_MULTIWORKER_KITRADER.md):
- Funding-Einheit-Bug (100× überschätzt) → Fee-Wächter blockte 207 Krypto-Signale/14 Tage
- Mindest-SL: erweitern statt verwerfen (433 Entscheidungen) + Drift-Toleranz bei Ausführung
- Playbook: revert_setup / revision_history / auto_revert_due (Selbstkorrektur toter Revisionen)
- Chat: setup_revert / setup_revise, kein Leaken interner Prompt-Blöcke
- local_exec: MAX_ACTIVE_WORKERS=2, worker_id-Ziel, parallel_allowed; UI WorkerTargetSelect
- asset_suggest + /api/dynamic-workbench/suggest-assets/{aid}; UI DynamicAssetSuggest
Backlog: P1 Asset-Vorschlag zusätzlich mit Kerzen-Korrelation/Volatilität (Worker-Cache);
P1 Regime-Lab-Hauptbalken je Worker getrennt anzeigen; P2 Worker-Paket-Versionstest (1.18 vs 1.13) bereinigen.

## Iteration 01.10.2026 (b) – Autopilot-Verlauf zurück + Referenz-Start
- Ursache „Verlauf weg“: Retention hielt nur 12 regime_lab_runs über ALLE Arten → regime_opt verdrängte Autopilot-Läufe.
  Fix: je result.kind (Autopilot 40), gemerkte (pinned) nie löschen.
- Verlauf: Ampel (sehr gut/gut/mittel/schwach, rate_result), ★ Bester je Coins/Timeframe, Filter, Merken (pin).
- Aktionen: übernehmen (Engine-Einstellungen), „Regime suchen“ (übernehmen + sofort Analyse mit Coins/TF des Laufs),
  „Referenz“ (nächster Autopilot startet von dieser Erkennung + Top-Varianten, auch anderer Timeframe; result.reference = Abstammung).
- Tests: backend/tests/test_autopilot_history_reference.py, tests/test_autopilot_history_api.py; iteration_40 100 %.

## Session 01.10.2026 – Regime-Autopilot: Timeframe-Kette
- User choices: Scoring + Fallback, fixed chain (not configurable), default ALWAYS OFF (not persisted), local test DB only.
- `backend/services/regime_tf_chain.py` (new, pure): fixed ladder 15m…1d, chain = selected TF + 1 finer + 2 coarser;
  hysteresis 1.0 points for TF changes; fallback switch after 60 stale rounds; evidence filter (holdout bars).
- `regime_autopilot.run_autopilot`: one data context per TF (`_split_train`); chain off = identical behaviour.
  Baseline = selected TF; result.timeframe = best TF (+ selected_timeframe, tf_chain summary, history[].timeframe).
- Router: `tf_chain` param, `GET /api/regime-lab/autopilot/tf-chain`; local worker needs >= 1.19.0 (VERSION bumped).
- Frontend: `RegimeTfChain.js` (TfChainToggle / TfChainResult), wired into RegimeAutopilot/RegimeLab/History.
- Tests: `backend/tests/test_autopilot_tf_chain.py` (25 offline tests); unit regression identical to baseline
  (18 failed / 5 errors pre-existing). Testing agent iteration_41: 100% pass.
- Backlog: Copilot knowledge text about the chain; per-asset-class stats of winning TFs.

## Session 01.10.2026 (b) – Klick-Feedback, Aufräumen, +400 entfernt
- Prod measurement (read-only GETs): /api/autotrade/trades?limit=200 took 8–24 s / ~1 MB, polled by chart markers every 15 s;
  /api/health ~0.2 s (loop not blocked at that moment). Root cause of slow start clicks in prod not proven yet.
- frontend/src/lib/startFeedback.js (installed in index.js): instant "Wird gestartet …" toast for all start POSTs,
  slow hint after 3 s, visible rejection reason, double-click dedupe. Jest tests: src/__tests__/startFeedback.test.js.
- backend/core/loop_watchdog.py + GET /api/system/loop-health: detects event-loop blocks > 1 s with file:line culprit.
- /api/autotrade/trades: TRADE_LIST_EXCLUDE projection (~30 % less data), full=true returns everything.
- Header.js: +400 USDT display offset removed.
- Dead code: 4 unused frontend components -> archive/frontend_unused (ARCHIV_KATALOG.md); ~25 unreferenced backend
  functions/classes removed (vulture + grep verified). services/price_watch.py is never started -> ask the user.
- Regression: unchanged versus baseline; testing agent iteration_42: 100 %.

## Iteration 02.10.2026 (Branch conflict_011026_1749)
Aufgabe: Werkbank-Ladebalken mit Pause & Co. (wie Discovery), Werkbank und Regime-Lab-Balken entkoppeln, „Bestehende optimieren“ mit Strategie je Phase (nicht handeln / andere Ausgangs-Strategie / optimierte reaktivieren, mit Bestätigung, neue Version, Verlauf + Wiederherstellen), Dynamik-Backtest auf dem lokalen Worker.
- Umgesetzt: siehe `VERBESSERUNGEN_0210_WERKBANK_PHASEN.md`. Neue Module: `services/dynamic_versions.py`, `DynamicPhaseEditor.js`, `DynamicVersionHistory.js`, `lib/postJson.js`. Worker 1.20.0.
- Tests: `backend/tests/test_workbench_phases_1010.py` (14) und Testing-Agent iteration_43 grün.
- Backlog: P1 Live-Pause eines echten Werkbank-Laufs am Worker prüfen. P1 Phasen-Editor: Mini-Backtest direkt nach der Änderung. P2 Basis-Strategie für Discovery-Regeln ist eine Built-in-Strategie (Regeln greifen live nur bei Custom-Basis, Altverhalten).

## Iteration 02.10.2026 (Teil 2–3)
- Umgesetzt:
  - Überanpassungs-Warnung im Autopilot und Badge im Verlauf
  - Kurze Feinsuche (`services/regime_finetune.py`, `fine_mode`)
  - Versionsvergleich (`/versions/compare`)
  - Unter-Reiter der Werkbank als Segment-Leiste
  - je Phase: Optimieren-Häkchen, „Nur diese Phase“, „Neue Regeln für diese Phase“, Verlauf je Phase über die Linie (`refined_from`), Variante zurückholen
  - Fix: Refine übernimmt die Phasen-Anpassungen nicht verbesserter Phasen
- Tests: test_workbench_phases_1010.py (17) und test_finetune_compare_1010.py (7), Testing-Agent iteration_44 und iteration_45 grün.
- Backlog: P1 kompletten Refine-Lauf mit echten Kerzen prüfen (Übernahme der Anpassungen live). P2 Kennzahlen je Variante aus einem eigenen Mini-Backtest.

## Iteration 02.10.2026 (c) – Branch conflict_021026_1321
Nutzer-Wunsch (wörtlich, gekürzt): "Autopilot Verlauf … da sind nur neue drin, kannst du dort auch noch alte wie z.B. von meiner Regime Analyse mit sehr gut (9 Regime … 25.09.2026, 13:40) mit rein machen" · "ich verstehe nicht das walkforward der regimerkennung … werden da random Strategie darauf gemacht" · "normale Strategien … importieren und exportieren, das bitte auch für dynamische Strategien … mit der verbundenen Regime Analyse … duplizieren".
- Autopilot-Verlauf: alte Analysen automatisch als gemerkte Referenzen (Boot-Migration `regime_history_import_v1`; `persist_analysis` sichert bei jedem Speichern und vor der Limit-Löschung über `regime_history_import.import_one`). Zeile zeigt "Analyse: sehr gut, 9 Regime".
- Walk-Forward: `WalkForwardScope.js` erklärt vor dem Start, was getestet wird (Regime → zugeordnete Strategie → Herkunft).
- Dynamische Strategien: `services/dynamic_backup.py` + `GET /api/dynamic/{id}/export`, `POST /api/dynamic/import`, `POST /api/dynamic/{id}/duplicate`; UI `DynamicBackupActions.js` (Export, Duplizieren, Backup importieren). Import überschreibt nie, Auto-Prüfung/-Übernahme aus.
- Tests: `tests/test_dynamic_backup.py` (neu), Mock-Fix in `test_autopilot_history_import.py`; iteration_46: 100 % bestanden.
- Backlog: P1 Import-Vorschau vor Bestätigung · P2 Sammel-Backup mehrerer dynamischer Strategien.



## Iteration 02.10.2026 (d) – Feedback Werkbank/Backtester/Note/Last
- „Bestehende optimieren“: Dropdown „– Strategie ändern –“, „Übernehmen…“ erst nach Auswahl, Phasen erst nach „Phasen-Parameter anzeigen“, Tooltip „Optimierte Strategie“ (DynamicPhaseEditor.js).
- Backtester: dynamische Strategien wahlweise Live-Sicht oder Rückblick (ideale Phasen) – `dynamic_label_basis`, `dynamic_backtest.phase_labels`.
- Eine Note: Autopilot-Ampel = Erkennungsqualität (`regime_quality.grade_from_metrics`, Import = Analyse-Note); Such-Score nur Rangliste (Tooltip). Accumulator sammelt missed_pct + Validierung.
- Last: Job-Starts laden Analysen ohne chart/chart_emas (`lab.NO_CHART`), Worker-Payload ohne chart_emas.
- Tests: `tests/test_grade_unify_and_dyn_basis.py`; iteration_47 grün (2 bereits vorher rote Alt-Tests in test_ap01_dynamic_apply.py).
- Offen: Prod-Diagnose /api/system/loop-health für weitere Last-Optimierung.

## Iteration 02.10.2026 (e) – Zeitraum über Analyse hinaus, Ergebnis-Backtest lokal, Last-Messung
- Werkbank-Zeitraum länger als die Analyse: nur Live-Sicht (`regime_opt.extra_days_for`, `_extended_live_segments` – Daten VOR der Analyse, kausal eingeteilt, Holdout unberührt); Rückblick darüber hinaus gesperrt (UI-Hinweis + Start gesperrt + Backend-Fehler).
- Ergebnis-Backtest am Ende der Werkbank läuft bei Ausführung „lokal“ auf dem Worker (`dynamic_workbench._local_result_backtest`), Cloud nur als Fallback.
- Last-Messung lokal mit KI-Keys (ohne Börsen-/Telegram-Keys, lokale DB): KI-Schleifen blockieren den Server nicht; einzige Blockade 2,6 s beim Start (ai_ml_lab._restore_model). Keys danach wieder entfernt.
- Tests: `tests/test_workbench_extended_and_local_bt.py`; iteration_48 grün.

