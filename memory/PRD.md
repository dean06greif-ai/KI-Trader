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
