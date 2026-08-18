# PRD – Crypto Scanner / Daytrading-Website (extern deployt auf Render)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: AntonHeinrich05/ml-implementation-ML_REBUILD_STATUS.md, Branch 0.5) soll verbessert werden, ohne Struktur/Workflows zu brechen (Render-Deploy muss weiter funktionieren). Grundsatz: sauber, modular, rückwärtskompatibel, mit Regressionstests.

## Architektur
- FastAPI-Backend (`/app/backend`, Port 8001, Router in `routers/`, Services in `services/`, Kern in `core/`)
- React-Frontend (`/app/frontend`, CRA/craco, deutschsprachige UI)
- MongoDB (`MONGO_URL`/`DB_NAME` aus env), Supabase-Spiegel fürs KI-Gedächtnis, Telegram-Notify
- Externe Integrationen: Bitunix Futures (Live-Trading), diverse LLM-Provider (Cerebras, Groq, OpenRouter, Gemini, Mistral) mit Failover-Kette
- Bestehende pytest-Suite in `backend/tests/` (teilweise env-abhängig: Failures ohne MISTRAL/GITHUB/GEMINI-Keys sind vorbestehend)

## User-Persona
Einzelner Admin-Trader (deutsch), der die Seite parallel zu manuellem Bitunix-Trading nutzt; KI-Trader läuft autonom.

## Umgesetzt (13.06.2026, „0.6-Verbesserungen")
1. **Unbegrenzte Backup-Keys**: `services/ai_providers.py::_backup_env_names` scannt env dynamisch – `X_API_KEY_BACKUP`, `_BACKUP2`, `_BACKUP3`, … beliebig hoch, numerisch sortiert; gilt für alle Provider (Cerebras, OpenRouter, Groq, …).
2. **Watchdog**: An/Aus-Toggle + Verlauf/Trades-Löschen waren in 0.5 bereits in den Haupteinstellungen vorhanden; übernommene manuelle Bitunix-Positionen heißen jetzt klar **„Manuell (Bitunix)"** (`manual_trade: true`, `strategy_id` bleibt „external" für Rückwärtskompatibilität), inkl. einmaliger DB-Migration alter Trades beim Start; Default `manage_external=false` (Watchdog fasst manuelle Trades nicht an).
3. **PnL-Genauigkeit**: `core/utils.py::_enrich_trade` akzeptiert echte Bitunix-Positionsdaten; `routers/autotrade.py` holt via 10s-Cache (`_live_position_map`) den echten uPnL der Börse für offene Live-Trades (Fix für Gold-Bug: Scanner nutzte Yahoo GC=F statt Bitunix XAUUSDT). Feld `live_pnl_source` = „bitunix"/„scanner".
4. **UI-Cut-Fix**: `.tdc-pnl-pct`/`.tdc-pnl` mit `flex-shrink:0; white-space:nowrap` – Prozentwert in Klammern wird nie mehr abgeschnitten (Coin-Name bekommt Ellipsis).
5. **Glocke↔Blitz-Kopplung**: `StrategyTabs.js` – Glocke kann nur an sein, wenn Auto-Trade aktiv ist; Klick bei Blitz-aus zeigt Info-Toast „Glocke nur möglich, wenn Auto-Trade (Blitz) aktiv ist".
6. **KI-Trader**: neues Modul `services/session_levels.py` (Asia/London/NY Session-H/L + Umverteilungszonen via Volumen-Cluster); Snapshot enthält jetzt 5m-RSI (Headline-RSI = 5m statt 1m-„Gambling"), Session-Levels & Zonen; System-Prompts erweitert (Timeframe-Disziplin, Sweep-/Breakout-Trigger an Session-Levels/Zonen, Konfidenz-Kalibrierung 70–85 für A-Setups gegen Dauer-HOLD); Playbook sperrt schwache Setups weiterhin automatisch.
7. **Regressionstests**: `tests/test_improvements_0_6.py` (13 Tests) + bestehende Watchdog/Backup-Tests grün (44/44 in den Zieldateien).

## Umgesetzt (13.06.2026, Iteration 2)
1. **Strategie-Vergleich Reiter-Reihenfolge**: Mode-Tabs jetzt ALLE → LIVE → PAPER, Zeit-Tabs Gesamt → 30 Tage → 7 Tage (`StrategyComparison.js`).
2. **Watchdog raus aus dem Vergleich**: `strategy-comparison` filtert `strategy_id='external'`/`manual_trade` bereits in der Mongo-Query + doppelte Absicherung im Loop (auch alte 'Extern (Watchdog)'-Trades via `external_adopted`).
3. **RAM-Reduktion (512-MB-Render, ohne Funktionsverlust)**:
   - Mongo-Projektionen: `strategy_comparison` (nur 13 Felder statt kompletter Trade-Dokumente mit manage_log/KI-Feldern), `rebuild_performance` (200k-Signals-Query auf 6 Felder), `_aggregate_ai_stats` (5k-Signals auf 4 Felder) → große transienten RAM-Spitzen beseitigt.
   - Candle-Cache-RAM-Budget: Default 2M → 500k Kerzen (~24 MB), env-übersteuerbar via `CANDLE_CACHE_MAX_CANDLES`; ältere Symbole liegen als `.npy` auf Disk (Reload in ms, kein Leistungsverlust). Lokaler Worker setzt sein Budget weiterhin selbst nach echtem RAM.
4. **Tests**: `tests/test_comparison_and_ram_iter2.py` (4 Tests) + Testing-Agent-Verifikation (iteration_15.json, 100%).

## Umgesetzt (14.06.2026, Multi-Timeframe pro Regel)
Detailplan/Design: **/app/MULTI_TIMEFRAME_PLAN.md** (vom User gewünschte „Plan-Datei in git").
- Hybrid-Ansatz: Basis-TF pro Strategie bleibt, optionales `"timeframe"`-Override je Regel (Stufen 1m…1d; muss ≥ Basis-TF & Vielfaches sein, sonst 422). Default = Basis-TF → 100% rückwärtskompatibel.
- Kern: `fast_sim.FastSeries.htf()` + `_rule_cond_htf()` (letzte GESCHLOSSENE HTF-Kerze, kein Lookahead); Live-Scanner, Backtester, Optimizer, rule-preview nutzen denselben Codepfad → Parität garantiert. `aggregate_candles(..., base_ms=)` für Nicht-1m-Quellen gefixt; `"1d"` in TIMEFRAMES ergänzt.
- Scanner-`buffer_limit` berücksichtigt Regel-TFs (60 Kerzen des höchsten Regel-TF, Deckel 30 Tage/43200 wegen Render-RAM).
- Optimizer: Request-Option `rule_timeframes {enabled,min,max}` (Rahmen z.B. 1m–4h) für params-Modus (Suchraum-Keys `long1_tf`…, via `custom_params.rule_timeframe_space`/`apply_params`, `CustomStrategy.get_params` reicht `*_tf` durch) und Discovery/Combo/Deep/Explore (`build_candidates(..., tf_options)` erzeugt „@TF"-Kandidaten-Varianten). Wird nie erzwungen – Basis-TF bleibt immer Option.
- UI: TF-Dropdown pro Regel im StrategyBuilder (`rule-tf-{long|short}-{i}`, ungültige Stufen disabled), Optimizer-Chip „Regel-Timeframes optimieren" + von/bis (`opt-rule-tf-toggle/-min/-max`). KI-Strategie-Labor-Prompt erklärt das Feld (Filter ja, Cross-Trigger nein) → KI-Trader kann MTF selbst nutzen.
- Tests: `tests/test_rule_timeframes.py` (25 Unit-Tests) + `tests/test_rule_tf_api.py` (9 E2E, vom Testing-Agent) grün; Regressionssuites grün (einziger Fail: `TestOptimizerDynamic::test_per_regime_true` = 900s-Poll-Timeout auf langsamem Test-Pod, dynamic-Modus von Änderungen unberührt).

## Umgesetzt (14.06.2026, Iteration: QQQ-Fix / Chart-Signal-Overlay / KI-Range-Trading)
1. **QQQ „ORDER ABGEBROCHEN"-Spam behoben** (Root Cause: KI-Trade-Manager managte die manuell auf Bitunix eröffnete QQQ-Position; QQQ ist nicht per OpenAPI handelbar → Close/Adjust schlug bei jedem Review fehl → Telegram-Meldung): `ai_trade_manager.review()` schließt manuelle/externe Trades (manual_trade / external_adopted / strategy_id 'external') aus; `apply_action()` blockt sie hart; `bitunix_trade._notify_reject()` hat 30-min-Anti-Spam-Cooldown pro (Symbol+Seite+Grund). Watchdog war korrekt (manage_external=False).
2. **Chart-Signal-Overlay**: Klick auf die Signal-Box im SignalPanel zeigt im Chart Entry/SL/TP-Preislinien + Overlay mit allen Regeln inkl. Timeframe-Badge je Regel (`chart-signal-panel`, `chart-signal-rule-*`). Signale speichern dafür `rules_snapshot` + `strategy_timeframe` (scanner `_maybe_signal`); Regel-States enthalten `timeframe`. SignalPanel zeigt live `@tf`-Badges pro Regel.
3. **KI-Trader Range-Trading**: neues Modul `services/range_analysis.py` (Range-Erkennung mit Touch-Zählung + Wick-Rejections auf 15m/1h, rein/testbar) fließt als „Range-Check"-Zeile in den KI-Snapshot; beide System-Prompts erklären range_fade (Entry an Range-Grenze nach Wick-Rejection, SL dahinter, TP Mitte/Gegenseite). Setup-Typen range_fade/mean_reversion existierten bereits im Schema.
4. **„kein Edge"-Klärung (UI)**: Das Badge erscheint bei JEDER HOLD-Entscheidung (HOLD = bewusst kein Trade); Tooltip präzisiert, HOLD zeigt jetzt zusätzlich die Konfidenz.
- Tests: `tests/test_qqq_range_chart_fixes.py` (10) + `tests/test_qqq_review_query_regression.py` (Testing-Agent) grün; Watchdog-/Rule-Engine-Regressionen grün; UI e2e verifiziert (iteration_17.json, 100%).

## Env-Hinweise (lokale Preview vs. Render)
- Lokal: `MONGO_URL=mongodb://localhost:27017`, Admin/admin123; Bitunix-Key vom User maskiert geliefert → `trade_client.configured()==false` lokal (erwartet)
- Auf Render nutzt der User seine echte env (unverändert kompatibel)

## ML-Umbau Fortsetzung (14.08., 3. Handover)
- Phase 3 = Fix 0.6 ERLEDIGT (14.08.): Rebuild-on-Boot Candle-Backfill (services/boot_backfill.py, non-blocking, 22 Symbole × 14d in ~3 Min, 0 Fehler), Downtime-Signal-Labeling (P1-Tech-Debt geschlossen, trade_pnl-Vorrang), Audit-Log für Löschungen (core/audit.py, GET /api/audit-log, Anzeige im Clear-Modal). Tests: 9 Unit + 14 API + Frontend 100%.
- RCA Trade-Schwund 167→96: analytics/clear ("Verlauf löschen") am 13.08. für Zeitraum 11.–12.08., nur mit Admin-Login möglich → Kumpel sehr wahrscheinlich; kein Deploy-Nebeneffekt. Details in ML_REBUILD_STATUS.md.
- Repo 14.8-timeframe-Update-try nach /app übernommen (alle Fixes 0.1–0.5 + ai_rewards-Fix enthalten), .env selektiv (LLM-Keys, ADMIN, PROD_MONGO_URL nur lesend; Bitunix/Telegram/Supabase bewusst NICHT), App lauffähig verifiziert (Health, Admin-Login, Frontend, Prod-Lesezugriff).
- Phase 2 dokumentiert in /app/memory/ML_REBUILD_STATUS.md: Prod-Freigabe (Migration 0.5 → --apply → Reward-Backfill, Reihenfolge zwingend), RCA-Notiz (User hat nicht gelöscht, Ursache unbestätigt), Gate-Aktivierungskriterium (4 Wochen, ≥150 Entscheidungen, 3 Kriterien gleichzeitig).
- Auffälligkeit Prod (14.08.): auto_trades 167→96 (71 Trades weg), ai_rewards=3 (Hook zeichnet live auf).
- Nächste Phasen (je mit User-Freigabe): 3 = Fix 0.6 Rebuild-on-Boot Candle-Backfill, 4 = Paper-Datensammel-Modus (data_collection=true), 5 = Gate v1 Shadow + kontrafaktisches Logging.

## ML-Umbau Fortsetzung (14.08., 4. Handover)
- RCA "KI-Trader macht kaum Trades": Self-Tuning-Ratchet – die KI hatte per autonomy=auto
  ihre min_confidence 70→75→80→85 hochgeschraubt (Prompts kalibrieren A-Setups auf 70–85 →
  Schwelle 85 filtert fast alles). Dazu: analytics/clear (scope=strategy) am 13.08. löschte
  ALLE ai_trader-Signale/Trades vor dem 14.08. Details: ML_REBUILD_STATUS.md.
- Self-Tuning-Guard: einstellbare Autonomie-Spanne (tune_conf_min/max 55–75, tune_cooldown_max
  45); außerhalb wird jede KI-Engine-Änderung nur Vorschlag; Boot-Heilung setzt KI-gesetzte
  Out-of-Range-Werte einmalig zurück (heilt Prod nach Deploy: 85→75).
- Phase 4 Paper-Datensammel-Modus: collection_enabled/min_confidence 60/cooldown 30/
  max_same_direction 5/max_per_coin 2; Sammel-Trades immer Paper (auch auf AUS-Coins),
  data_collection=true + collection_reason am Trade/Signal, eigene Slots, kein Telegram,
  Badge "DATEN" in der Trade-Karte. Erwartung: ~30–60 Paper-Trades/Tag fürs ML.
- Tests: tests/test_phase4_collection_mode.py (7 PASS) + Regressionen grün.

## ML-Umbau Fortsetzung (14.06.2026, 5. Handover — Phase 5 Gate v1 Shadow)
- Neues Modul `services/ml_gate.py` + `routers/ml_gate.py` (/api/ml/gate/*): Meta-Labeler
  (XGBoost, konservative Fixparams), krypto-only, Datenbasis Prod-Signale+Ghost+Decisions
  (PROD_MONGO_URL nur lesend; auf Render = eigene DB). Purged Walk-Forward (5 Blöcke) +
  24h-Embargo, Platt-Kalibrierung auf OOS, Brier vs. Baseline, Modelle versioniert in
  ml_gate_models (nie überschrieben).
- Shadow-Hook in ai_engine: jede LONG/SHORT-Decision trägt gate_shadow (p_win,
  model_version, threshold, would_block) — blockt NIE. Kontrafaktischer Report
  GET /api/ml/gate/report (Aktivierungskriterien + Threshold-Sweep).
- Erstes Prod-Training: v1, 970 Samples, OOS-Brier kalibriert 0.2393 < Baseline 0.2541,
  AUC 0.552 (ehrlich: noch schwach – Shadow-Phase sammelt Beweise, Collection-Mode
  vergrößert Datenbasis). Indizes decisions_ts/decisions_outcome_ts ergänzt.
- Tests: 7 Unit (test_phase5_gate_shadow.py) + 11 API (test_phase5_ml_gate_api.py,
  Testing-Agent) = 18/18 grün (iteration_20.json, 100%). Regressionen grün.

## ML-Umbau Phase 6 (14.06.2026 — Gate-Dashboard + Auto-Retrain)
- Auto-Retrain-Scheduler (MLGate.tick im Engine-Loop): Erst-Training ab 120 gelabelten
  Samples, Retrain nach 50 neuen Samples oder täglich 4 Uhr Berlin; Settings persistiert.
- UI-Tab "Gate v1" im KI-Labor (GateShadowPanel.js): SHADOW-Badge, Kriterien-Ampel,
  Kontrafaktik + Threshold-Sweep, Kalibrierungs-Chart, Versionsliste, Train-Button.
- Getestet: iteration_21 100% (Backend 17/17, Frontend 8/8), keine Bugs.

## Backlog / Nächste Aufgaben
- P0: User: Render-Deploy des OOM-Fixes + Trade-Manager-Guards (Branch pushen), danach in Prod: Migration 0.5 (Dry-Run → --apply) + Reward-Backfill
- P1: Phase 7 (User entscheidet): Regime-Brücke (=0.7) oder Shadow-Beweis abwarten
- P1: Prod-Befehle nach Deploy (Migration 0.5 Dry-Run → --apply → Reward-Backfill)
- P1: Backfill-Fortschritt als Statusleiste im Dashboard
- P1: Live-Verifikation der Bitunix-uPnL-Anzeige mit echtem API-Key (nur auf Render möglich)
- P2: Per-Setup-Timeframe-Statistik ins Playbook; Session-Levels optional im Chart
 kein Qualitätsverlust), Optuna gc_after_trial,
  gc.collect() nach Training (ml_lab + ml_gate).
- RCA merkwürdige KI-Trades: LLM-Trade-Manager micro-managte die Phase-4-Sammel-Trades
  (SL-Ratchet bis 0,007% an den Kurs, Hebel-Reflexe, 202× adjust_sl in 300 Aktionen).
  Fix: data_collection-Trades komplett vom KI-Management ausgenommen (Review-Filter +
  apply_action-Block, manuell bleibt erlaubt) + SL-Ratchet-Guard min_sl_gap (neuer KI-SL
  mind. max(30% der initialen SL-Distanz, 0,1% vom Kurs) vom Kurs entfernt) + Prompt-Regel.
- Tests: tests/test_ram_and_manager_guards.py 9/9 + Testing-Agent iteration_22 (19/19,
  100% Bugfix-Scope); Regressionen test_swing_and_profit_lock + test_review_bugfix_e2e
  32/32 (nach Bereinigung von Dev-DB-Testresten: trade_guard_state paused_until).

## ML-Umbau Fortsetzung (15.06., 7. Handover — Fee-Wächter)
- Repo Branch 0.69 nach /app geklont; .env selektiv (LLM-Keys, ADMIN, PROD_* nur lesend).
  Vorheriger Agent starb mitten in der Fee-Wächter-Umsetzung ohne Push → sauber neu gebaut.
- **Fee-Wächter** (User: nur Option 1): KI-Trade wird nur eröffnet, wenn SL-Distanz ≥
  fee_guard_mult × Roundtrip-Fees (Default 4× × 0,12% = 0,48%). Enforcement an EINEM Punkt
  in bitunix_trade.on_signal (nach finaler SL-Bestimmung → deckt Live/Sammel/Panel-Trades ab),
  Engine-Keys fee_guard_enabled/fee_guard_mult (nicht KI-änderbar), Prompt-Hinweis im
  Trade-Rahmen, UI-Controls im KI-Setup (ai-fee-guard-enabled-select/-mult-select).
- Tests: tests/test_fee_guard.py 6/6; Regressionen Phase-4 7/7, Guards 9/9, Gate 11/11;
  Testing-Agent iteration_23: Backend 100% + Frontend 100%.

## ML-Umbau Fortsetzung (15.06., Fee-Feedback-Paket)
- **Fee-Lern-Feedback** (ai_rewards.py, kein neuer Malus): Reward-Docs tragen fees +
  fee_share_pct (Anteil der Gebühren am Verlust, gekappt 100%); Lern-Prompt-Block
  "GEBÜHREN-ANTEIL an Verlusten" → KI lernt selbst, weitere Stops zu wählen.
- **Fees-vs-Risiko-Anzeige** (PerformanceAnalytics.js): Meta-Zeile an jeder Trade-Karte
  (trade-fees-vs-risk-{id}), Roundtrip-Fees / geplantes Risiko, farbkodiert (rot ≥50%).
- **Blockier-Statistik**: fee_guard_blocks-Collection (60d-Retention, Index fee_guard_ts),
  GET /api/ai/fee-guard/stats?days=7, Anzeige "Geblockt (7 Tage)" im KI-Setup
  (ai-fee-guard-stats).
- Tests: tests/test_fee_feedback.py 4/4; Regressionen grün; Testing-Agent iteration_24:
  Backend 100% + Frontend 100%.

## ML-Umbau Fortsetzung (15.06., Fix 0.7a+0.7b + User-Wünsche, 8. Session)
- **0.7a Gate-Domain**: shadow_predict/shadow_report krypto-only (TOP_10_COINS) —
  B6-Fix, Shadow-Report wertet keine Out-of-Domain-Symbole mehr.
- **0.7b Regime v2** (ai_market_observer): Vol-Label per Symbol-Perzentil (48h-Historie,
  P80/P30, Fallback Fix-Schwellen), Trend-Hysterese (rein ±0.08 / raus ±0.05),
  breakout nur mit Bestätigung sonst drift, regime_v=2, Mehr-Horizont-Trend
  trend_1d_pct/trend_3d_pct + daily_bias im Snapshot & Prompt.
- **KI-Trader-Reset**: POST /api/ai/trader/reset (Admin-JWT + Passwort-Body, löscht nur
  ai_trader-Paper-Trades/Signale/Rewards, Live bleibt, audit_log); UI AITraderReset.js
  im KI-Setup (ai-trader-reset-open-btn).
- **Trade-Karte**: KI-BEGRÜNDUNG-Block (setup, ai_reasoning, ai_confidence,
  ai_news_impact — neue Felder am Trade-Doc) + Live-Chart-Button (trade-open-chart-{id},
  schaltet Haupt-Chart aufs Trade-Symbol).
- Tests: test_regime_v2_and_gate_domain.py 11/11, Testing-Agent iteration_25:
  Backend 100% (16/16) + Frontend 100%, keine Regressionen.

## ML-Umbau Fortsetzung (15.06., Paket 2: Erklärbarkeit + Kontrolle, 8. Session)
- **CRV-Deckel**: crv_max Standard 4 + einmalige Migration (gespeicherte 0 → 4),
  TP Full zusätzlich auf 2×crv_max gedeckelt (Fix für „TP unrealistisch weit weg")
- **Regime-Badge** über dem Haupt-Chart (GET /api/ai/regime/{symbol}, 60s-Refresh,
  ausführlicher Tooltip, Tages-Bias)
- **Trade-Erklärung**: „Details"-Modal (GET /api/autotrade/trade/{id}/explain, 0 LLM-Kosten):
  volle Begründung, warum Positionsgröße/SL/TP (neue LLM-Felder size_reason/levels_reason),
  Fees, Markt beim Entry; ausführliche Hover-Tooltips auf Setup-/Konfidenz-/News-Badges
- **„Trade überdenken"**: POST /api/autotrade/trade/{id}/rethink (Admin, 15min-Cooldown/Trade,
  1 kleiner LLM-Call, Aktionen laufen durch die geprüfte apply_action-Schicht)
- Tests: iteration_26 alles grün (Backend 9/9, Frontend 100%, echter LLM-Call verifiziert)

## ML-Umbau Fortsetzung (15.06., Paket 3: Werte-Tooltips + Detail-Zerlegung)
- Alle Meta-Werte der Trade-Karte mit ausführlichen Hover-Erklärungen
- Details-Modal: Sektion „AKTUELLER STAND"/„ERGEBNIS" mit PnL-Zerlegung (Brutto − Gebühren = Netto, R)
- „Überdenken" token-sparsamer (max. 1 Aktion, 2-Sätze-Note, weiterhin nur auf Klick)
- iteration_27: Backend 24/24, Frontend 100%

## Backlog / Nächste Aufgaben
- P0: User: Render-Deploy (aktiviert 0.7a/0.7b + Reset + Trade-Karte + Fee-Paket in Prod), danach Reset-Button 1× drücken und Migration 0.5 + Reward-Backfill
- P1: ~1 Woche Prod-Daten, dann Regime-v2-Verteilung nur lesend prüfen; erst danach über 0.7c (regime_engine-Brücke) entscheiden
- P1: Prod-Befehle nach Deploy (Migration 0.5 Dry-Run → --apply → Reward-Backfill)
- P1: Backfill-Fortschritt als Statusleiste im Dashboard
- P1: Live-Verifikation der Bitunix-uPnL-Anzeige mit echtem API-Key (nur auf Render möglich)
- P2: Per-Setup-Timeframe-Statistik ins Playbook; Session-Levels optional im Chart

## Bugfix-Session (Juni 2026, Branch 0.77): Manuelle Trades + Trade-Karten-UI
- BUG 1 (manuelle Bitunix-Trades unsichtbar), Root Cause doppelt:
  a) Positions-Watchdog war in den DB-Settings deaktiviert (enabled=false) → Sichtbarkeits-Sync (adopt_unknown) lief gar nicht mehr. Fix: `services/position_watchdog.py` – run_loop läuft jetzt auch bei enabled=false im **sync-only-Modus** (übernimmt fremde Positionen als "Manuell (Bitunix)", greift aber NICHT an der Börse ein: kein SL, kein Dust-/Notfall-Close). check(manage=...)/_check_position(manage=...), Status-Report enthält `mode: full|sync-only`. `/api/autotrade/watchdog/run` läuft im sync-only-Modus mit.
  b) GET /api/autotrade/trades: offene Trades fielen aus dem limit-Fenster (200 neueste), sobald viele neuere KI-Trades existierten. Fix: `routers/autotrade.py::get_trades` hängt ohne status-Filter IMMER alle offenen Trades an (dedupliziert, mode-Filter respektiert).
- BUG 2 (Coins fehlten in Trade-Karten, PnL/Caret rechts abgeschnitten): `PerformanceAnalytics.css` – `.tdc-main` flex-wrap:wrap + row-gap, `.tdc-coin` min-width:48px (kollabiert nie mehr auf 0).
- FEATURE Multi-Timeframe pro Regel: war in 0.77 bereits vollständig umgesetzt (siehe MULTI_TIMEFRAME_PLAN.md) – per Tests + E2E verifiziert (StrategyBuilder-Dropdown, Optimizer rule_timeframes, Discovery/Combo/Explore, 422 bei ungültigem TF).
- Neue Regressionstests: tests/test_trades_visibility.py (3), tests/test_watchdog_sync_only.py (3); test_improvements_0_6.py auf asyncio.run umgestellt (Loop-Robustheit). Testing-Agent iteration_28.json: alles grün.

## Iteration (Juni 2026): PnL-Sync, Chart-Pin, exakte Bitunix-Verbuchung, TF-Statistik, Filter
- PnL-SYNC: Chart-Badge + Trade-Verlauf-Header (offene Trades) zeigen jetzt EINE Quelle: computed.upnl_pct_margin (Live = echter Bitunix-uPnL, 15s-Poll). MainChart.js/PerformanceAnalytics.js.
- EXAKTE VERBUCHUNG: _book_external_close nutzt jetzt Bitunix GET get_history_positions (closePrice/realizedPNL/fee/funding) → echter Netto-PnL statt Mark-Preis-Schätzung; Flag pnl_exchange_exact; Schutz bei manuell aufgestockten Positionen (>5% qty-Abweichung → Schätzung). parse_closed_position pure + Tests (test_exchange_close_truth.py).
- CHART-PIN: Klick auf Trade-Badge oder Entry-Pfeil pinnt Entry/SL/TP1/TP als Preislinien (nur Preislinien, RAM-schonend; aktualisieren alle 15s). useTradeMarkers.js renderPinned/togglePin/pinAtTime, MainChart subscribeClick, CSS .chart-open-badge.pinned.
- PLAYBOOK TF-STATISTIK: ai_playbook.tf_stats/best_tf_per_setup/tf_context_lines – bester Timeframe pro Setup (echte KI-Trades) in GET /api/ai/playbook (tf_stats+best_tf) und im KI-Prompt-Kontext.
- TRADE-FILTER: Analyse>Trades: Strategie-Dropdown (inkl. 'Manuell (Bitunix)') + 'Nur <ausgewählter Coin>'-Toggle, nur Listen-Anzeige (Statistik unverändert).
- REGEL-TF UI-LÜCKEN (User-Bug): TF-Dropdown pro Regel jetzt auch im SettingsPanel-Regel-Editor (def-rule-long/short-tf-*) und 'Regel-Timeframes optimieren'-Chip im Optimizer-Dynamik-Modus; Backend: rule_timeframes → _run_dynamic → discover_regime_strategy/optimize_regime_rules → build_candidates(tf_options).
- Tests: test_exchange_close_truth.py (7), test_playbook_tf.py (3); test_qqq_range_chart_fixes auf asyncio.run umgestellt. Kombi-Lauf 90 Tests grün. Testing-Agent iteration_29.json: 100% grün, keine Issues.

## Iteration (Juni 2026, #30): Trade-Zeiten, Karten-Layout, Coin-Toggle, manuelle Website-Trades, Chart-Aufräumen
- Uhrzeiten in Trade-Karten: fmtTimeShort ('TT.MM. HH:MM', Berlin-Zeit) rechts in .tdc-strat-line, geschlossene mit '→' (testid trade-times-<id>).
- Karten-Layout 3-zeilig: Z1 Mode/Side/Coin/Result/$PnL/Caret · Z2 SWING/DATEN links + %-PnL rechts unter $PnL (.tdc-sub, padding-right 20px) · Z3 Strategie + Zeiten.
- Coin-Toggle 'Nur <Coin>' jetzt in Titelzeile OFFENE TRADES (filtert offen+geschlossen); Strategie-Dropdown entfernt.
- Chart: permanente Entry-Preislinien rechts entfernt (doppelt zu Badges + Klick-Pin); useTradeMarkers apply() ohne addLine-Block.
- BUG-FIX manuelle Website-Trades: source!='ki' → suppress_signal (kein db.signals-Insert, kein Eval/Telegram/Broadcast, keine update_performance) + Trade als strategy_id 'external' / 'Manuell (Website)' / manual_trade=true (core/pipeline.py, ai_trade_manager.open_trade, bitunix_trade._open_trade). Getrennt von 'Manuell (Bitunix)' (Watchdog-Adopt).
- Tests: tests/test_manual_web_trades.py (3); test_iter26 crv_max-Assertions entpinnt (Nutzer-Setting). Testing-Agent iteration_30.json: 100% grün (E2E inkl. echtem Paper-Manuell-Trade, sofort wieder geschlossen).

## Iteration (Juni 2026, #31): Fee-Doppelzählung, Trade-Karten kompakt, Chart-Polish, Chat-Fenster, neue Modelle, RAM-Guard
- FEE-BUG (User: ETH 15.08 19:11, -8,5$ wurde -12$): Bitunix liefert realizedPNL in get_history_positions in der Praxis BEREITS inkl. Fees (entgegen API-Doku) → parse_closed_position zog die Fee nochmal ab. Fix: Erkennung über reinen Preis-PnL (entryPrice/closePrice/maxQty/side); liegt realizedPNL näher an (Preis-PnL − fee), sind Fees enthalten → net = realizedPNL + funding. Neues Feld fee_included_in_pnl. Betroffener ETH-Trade in Prod-DB repariert (-12.578744 → -8.593632, pnl_repair_note gesetzt); einziger betroffener Trade (alle pnl_exchange_exact-Trades gescannt). Tests: test_exchange_close_truth.py +2 (fee-inkl./fee-exkl.).
- TRADE-KARTEN kompakt (PerformanceAnalytics): OFFEN/G/V/BEP-Badge direkt neben LONG/SHORT (PnL-$ + Caret passen in Zeile 1); Zeilenabstand Z1→Z2 minimiert (.tdc-head gap 1px, padding 6px); %-PnL zentriert unter dem $-PnL (min-width 52px, text-align center); SWING/DATEN → rahmenlose Mini-Tags "S" (·R bei Runner) / "D" (.tdc-mini-tag, Tooltip erklärt).
- CHART: "· N bars" aus Subtitle entfernt; ALLE-TRADES-Button zeigt un-markiert nur noch "ALLE TRADES" (ohne "· 1 offen"), markiert weiterhin Gesamtzahl; Zeit-Button ▾ bleibt neben dem Text (white-space nowrap); Regime-Badge (z.B. "Ausbruch · ruhig") cursor:help → default (kein Fragezeichen-Cursor mehr, Tooltip bleibt).
- KI-PANEL: KI-Chat ist jetzt vollwertiges Fenster wie MasterPrompt – "Chat"-Button in der Status-Row (ai-chat-toggle), Zustand persistiert (localStorage krypto_ai_chat_window_open), andere Panels schließen Chat und umgekehrt; Setup-Panel scrollt (.ai-setup max-height 55vh, overflow-y auto).
- NEUE MODELLE (ai_providers + lib/aiModels): openrouter nvidia/nemotron-3.5-lightning:free (neues Free-Flaggschiff, 1M ctx, Gewicht 3) + deepseek/deepseek-v4-flash (bezahlt ~0,06$/M in / 0,13$/M out, Gewicht 3, live verifiziert). Bezahlte Modelle stehen in PAID_MODELS_NO_FALLBACK und NIE in FALLBACK_ORDER (kein unbemerktes Ausweichen auf Kosten); Test test_fallback_order_and_weights_consistent entsprechend angepasst.
- RAM-GUARD (services/ram_guard.py, in server.py-lifespan): mallopt(M_ARENA_MAX=2) + malloc_trim(0)-Loop alle 180s (gc.collect davor) – glibc-Fragmentierung war Haupttreiber des RSS-Wachstums bei normalem Gebrauch (Render 512MB-Kill). Env: RAM_GUARD_ENABLED, RAM_TRIM_INTERVAL_S. Lokal: RSS 357MB → ~168MB nach Neustart, Trim hält frei gewordenen Heap ans OS zurückgegeben.
- Cerebras-Free-Limits (für Analyst-Betrieb): 5 RPM / 30k TPM / 1M Tokens/Tag PRO KEY (Stand 08/2026).

## Iteration (Juni 2026, #32/33): Feinschliff nach Nutzer-Feedback + Log-Analyse
- TRADE-KARTEN: $-PnL + Caret als feste Gruppe (.tdc-pnl-group) rechts in Zeile 1 – Caret bricht nie mehr in Zeile 2 um (Nutzer-Screenshot-Bug); %-PnL direkt (dy < 8px) zentriert unter dem $-Wert (.tdc-sub margin-top -2px, padding-right 19px); S/D jetzt als hochgestelltes <sup class="tdc-sup"> direkt am Asset-Namen (z.B. ETH^D), tdc-sub enthält nur noch den %-Wert.
- MOBILE (mobile.css ≤640px): .tdc-main flex-wrap nowrap (Zeile 1 bleibt EINE Zeile, Coin ellipsiert), kompakte Paddings, Strategie-Zeile bricht sauber um (Zeiten unter den Namen).
- STRATEGIE-VERGLEICH: Coin-Chips in der aufgeklappten Strategie sind jetzt Buttons; Klick öffnet Insights-Box pro Strategie×Coin (W/V/BE, Ø PnL, Profit-Faktor, Bester/Schlechtester Trade, Ø Dauer, L/S, Paper/Live, Gebühren). Backend routers/analytics.py by_symbol um diese Felder erweitert (gleicher Aggregations-Pass, kein Extra-Query). Bugfix nach Iteration 32: fehlender useState für coinExpanded ergänzt (Crash beim Expand behoben, Iteration 33 grün).
- RENDER-LOG-ANALYSE (Nutzer-Logs 15./16.08.): OOM-Neustart 15.08 17:17 bestätigt RAM-Problem (RAM-Guard adressiert das, muss deployt werden); IndexOptionsConflict-WARNINGs (ai_chat.ts_-1 / role_1_pinned_1_ts_-1 existieren unter altem Namen) → core/indexes.py behandelt Code 85 jetzt als ok (INFO statt WARNING, 18/18); Rest der Logs unauffällig (normale Polls, WS-Disconnect-Noise).
- Testing-Agent iteration_32 (7/8 PASS, 1 CRITICAL) + iteration_33 (Fix verifiziert, alles grün).

## Iteration (Juni 2026, #34): %-Bug geschlossener Trades, Sup direkt am Asset, feste Zeile 1, paid Modell-Katalog
- BUG %-ANZEIGE (User: DOT +10,18$ bei 99$ Kapital/Hebel 40 zeigte +0,26%): core/utils.py _enrich_trade – bei geschlossenen Trades ist qty_remaining=0 -> margin=0 -> pnl_pct_margin=None -> UI-Fallback auf Positions-%. Fix: margin_qty = qty_rem or qty. Verifiziert: DOT jetzt +10,28%, 0/148 geschlossene Trades ohne pnl_pct_margin.
- TRADE-KARTEN: .tdc-coin-wrap (Coin + <sup> mit gap 1px) – S/D klebt direkt am Asset; .tdc-main flex-wrap nowrap (Desktop) – PnL+%-+Caret-Block fest oben rechts in Zeile 1, Strategie/Uhrzeit rückt hoch, Karte einzeilig 27px.
- MODELL-KATALOG (alle via bestehende OpenRouter-Keys, nur Credits nötig; in PAID_MODELS_NO_FALLBACK, nie Auto-Fallback): deepseek/deepseek-v4-pro-0813 (~0,43$/M, Top-Reasoning), z-ai/glm-5.2 (~0,46$/M), qwen/qwen3.7-flash (~0,03$/M), x-ai/grok-4.20 (~1,25$/M, 2M ctx) + bereits deepseek-v4-flash (~0,06$/M).
- Rollen-Empfehlung (dokumentiert im Chat): Analyst=DeepSeek V4 Flash, Deep-/Forschungs-Analyst & Lernen=DeepSeek V4 Pro 0813 (Premium: Grok 4.20), Trade-Manager=GLM 5.2 (Fallback Groq gpt-oss-120b), News/Beobachter/Reporter=Qwen 3.7 Flash bzw. Gemini Flash-Lite (free), Chat=Gemini 3.5 Flash.
- Testing-Agent iteration_34.json: 6/6 grün.

## Iteration 36 (16.06.2026 lt. Umgebung / Juni 2026)
- BUGFIX (CRITICAL aus Testreport): Guard in services/ai_trade_manager.py umgebaut – external_adopted-Positionen bleiben fuer ALLE Quellen blockiert; Website-manuelle Trades (manual_trade/strategy_id=external OHNE external_adopted) sind fuer source=manual (Website-Aktionen wie partial_close/adjust_sl/adjust_tp) erlaubt, fuer source=ki weiterhin blockiert (User-Vorgabe: manuelle Trades verwaltet der Nutzer selbst).
- HTTP-Semantik routers/autotrade.py /trade/{id}/action: blocked/rejected -> 409, error -> 400 (vorher 502).
- watchdog/run liefert bei enabled=false zusaetzlich reason-Feld.
- Test tests/test_qqq_range_chart_fixes.py an neue Guard-Semantik angepasst; tests/test_guard_external_adopted.py neu (Testing-Agent). E2E test_partial_exit_e2e.py 6/6 + Testing-Agent 8/8 gruen.
- Doku: /app/KI_MODELL_GUIDE.md – Preis/Leistungs-Ranking + Fallback-Empfehlung fuer Haupt-Modell und alle 9 KI-Team-Rollen (nur Anleitung, keine Code-Defaults geaendert, User-Wahl).

## Iteration 37
- Rate-Limit-/Uebersprungen-Warnungen im Modell-Status jetzt PRO ANBIETER zusammengefasst (ai_providers.health_status: rate_limited_grouped/skipped_grouped inkl. betroffener Rollen aus _recent_failures; UI AITradingPanel gruppierte Zeilen + PROVIDER_LABEL, alte Felder bleiben fuer Rueckwaertskompatibilitaet).
- Loesch-Dialog Analyse (PerformanceAnalytics): .clear-modal max-height 88vh + overflow-y auto -> nicht mehr abgeschnitten, Buttons erreichbar.
- Frage Prompt~14215>Budget erklaert (Groq Free-Tier TPM, App skippt automatisch zu anderen Anbietern) – kein Codefix noetig.
- Testing-Agent iteration_37: alles gruen; neuer Regressionstest tests/test_ai_status_grouping.py.

## Iteration 38 (Settings-Review, nur Analyse/Doku)
- Read-only-Analyse der PROD-Settings (Atlas) + 48 geschlossener KI-Trades: Paper-Winrate 8% (1/12), DC 43% (15/35), Oe-Hebel 44,9x, Oe-PnL -4,18/Trade.
- Befunde: use_heatmap_data AN / use_liquidation_data AUS (invertiert vs. Code-RCA!), Hebel viel zu hoch (lev_mode coin, TM max_leverage 125), Learner-Rolle auf gpt-oss-20b:free (zu schwach), research_analyst auf gemini flash-lite, smart_skip_move_pct 0.02 wirkungslos.
- Empfehlungen dokumentiert in /app/KI_TRADER_SETTINGS_CHECK.md (keine Code-/DB-Aenderungen, User stellt im UI um).

## Iteration 39 (Prod-Settings-Flip)
- Auf ausdruecklichen User-Wunsch NUR die Liquiditaets-Schalter live in der Produktion umgedreht: use_liquidation_data=true, use_heatmap_data=false. Ausgefuehrt ueber offizielles Admin-API POST /api/ai/config auf crypto-scanner-backend-hil2.onrender.com (Engine hat es sofort im Speicher) und in Atlas verifiziert. Keine Code-Aenderungen.

## Iteration 40
- Learner-Rolle live in Produktion umgestellt (POST /api/ai/roles): openrouter/gpt-oss-20b:free -> groq/openai/gpt-oss-120b (Fallback-Kette cerebras gpt-oss-120b -> gemini flash-lite blieb erhalten). In Atlas verifiziert.

## Großer Check (17.08.2026, 9. Handover — nur Analyse, read-only)
- Repo 16.8-New nach /app, Umgebung lauffähig (Backend/Frontend/Chart/Prod-Lesezugriff ok).
- Skripte: backend/scripts/big_check_prod.py, backend/scripts/trade_review_chart.py (read-only);
  Trade-Review-Rohdaten: /app/memory/trade_review_raw.txt.
- Kernbefunde: (1) P0 Wochenend-Trading auf Nicht-Krypto mit stale Preisen (EURUSD LONG+SHORT
  gleichzeitig, identischer Entry; Zombie-Trades verstopfen Richtungs-Slots = das gemeldete
  "Limit guard"-Problem); (2) Fee-Wächter blockt 0, weil KI SLs ans 0,5%-Minimum klebt;
  (3) MFE-Verschenken durch be_mode=tp1 (BE erst bei TP1-Touch, TP1 bei CRV 2–3);
  (4) Analyst lief 16./17.08. per Fallback auf gpt-oss-20b:free (Rate-Limits);
  (5) Regime v2 aktiv, range_ruhig=10% Winrate (n=20), Statistik aber Weekend-verschmutzt;
  (6) Gate-Shadow would_block=94% @0.45; (7) Werte-Anzeigen (risk_usd, Fees-vs-Risiko,
  pnl_pct_margin) rechnen korrekt. Maßnahmen-Plan mit Prioritäten in ML_REBUILD_STATUS.md
  (wartet auf User-Freigabe). Emergent-Key in externer Render-App NICHT nutzbar (Support bestätigt).

## Iteration 41 — Guard-Fixes (17.08.2026, vom User freigegeben, testing_agent iteration_38 grün)
- Wochenend-Guard: NEU core/market_hours.py; Enforcement in ai_engine._emit_signal (Live+Collection),
  run_analysis (Symbole übersprungen, Token-Ersparnis), Observer-Snapshots (features.market_closed).
- Fee-Wächter V2: Mindest-SL = max(fee_mult × Fees, fee_guard_atr_mult × 1m-ATR%); neuer Config-Key
  fee_guard_atr_mult (Default 4.0, 0–30, UI-Dropdown ai-fee-guard-atr-select); Prompt-Regel
  "Minimum ist Untergrenze, kein Zielwert → Struktur-SL oder HOLD".
- be_mode=crv Default für ai_trader (BE ab +1R); explizite Settings + andere Strategien unberührt.
- Tests: tests/test_weekend_guard_and_fee_v2.py NEU (5 Blöcke); Regression 9 Suiten grün.
- Offen (P2, User-Entscheidung): Stale-Price-Guard vor Entry (EURUSD-Doppel-Trade mit 45-min-altem Preis).

## Iteration 42 — Stale-Price-Guard + Regime-Sperrfilter (17.08.2026, testing_agent iteration_40 grün)
- Stale-Price-Guard aktiv (stale_price_max_min=10, 0-120, UI ai-stale-price-select): _emit_signal lehnt
  Live- und Sammel-Einstiege auf Kursen > X min Alter ab; leerer Buffer blockt nicht.
- Regime-Sperrfilter eingebaut, Default AUS (regime_block_enabled=false, Liste ["range_ruhig"],
  UI ai-regime-block-select): wenn aktiv, blockt nur Live-Entries (+ Governance-Eintrag), Sammel-Trades
  laufen weiter. Aktivierung erst nach 1-2 Wochen sauberer Nach-Guard-Daten.
- Guard-Kette: Wochenende -> Stale -> Regime -> MasterPrompt -> Tages-Limits -> Diversifikation -> Cooldown.
- Tests: tests/test_stale_price_and_regime_filter.py NEU (5 Blöcke) + 3 Regressions-Suiten grün.

## Iteration 43 — Guard-Cockpit (17.08.2026, testing_agent iteration_41 grün)
- GET /api/ai/guard-stats?days=7: Block-Statistik je Wächter (10 Kategorien inkl. Fee-Wächter)
  + Regime-Schatten-Zähler (was der deaktivierte Filter geblockt hätte).
- Frontend: "Guard-Cockpit"-Block im KI-Setup (ai-guard-cockpit / ai-guard-row-<key> /
  ai-guard-regime-shadow), Tooltip = letzter Block-Grund.
- Regime-Qualität bewertet (read-only Prod): Sperre NICHT aktivieren — 14/20 range_ruhig-Verluste
  waren Nicht-Krypto nach Marktschluss (kontaminierte Statistik). Kriterium für später in
  ML_REBUILD_STATUS.md dokumentiert.

## Iteration 44 — Hebel-Bestätigung Prod + Verlauf-Reiter (17.08.2026, testing_agent iteration_42 grün)
- 6 reine Hebel-Senkungs-Vorschläge der KI in PROD per Admin-API bestätigt + in Atlas verifiziert
  (strategy_coin_configs ai_trader_<SYM>): BTC 8x, QQQ 8x, GBPUSD 6x, EURUSD 6x, ADA 7.2x, DOT 6x.
  Erhöhende (XRP 22x, SOL 14x) und gemischte/sl_mode-/min_confidence-Vorschläge bewusst unberührt.
- NEU Reiter "Verlauf": GET /api/ai/equity-curve (days/mode-Filter, Reset-sicher) + AIEquityPanel.js
  (SVG-Equity-Kurve, KPIs, Lazy-Load erst beim Tab-Klick, exklusiv zu anderen Reitern).

## Iteration 45 — Modell-Berater, Modell-Entdeckung, UX-Fixes (17.06.2026, testing_agent iteration_43 grün, 12/12)
- Eingabefeld-UX global (frontend/src/lib/inputUX.js, installiert in index.js): Fokus markiert
  kompletten Inhalt (direkt überschreibbar); beim Leeren bleibt der vorherige Wert als grauer
  Platzhalter. Opt-out per data-no-select="true" (z.B. Berater-Chat-Input).
- "unknown"-Strategie aus Strategie-Vergleich entfernt (routers/analytics.py: Skip in by_strat
  + open_counts).
- Erweiterter Tradeverlauf entrümpelt (PerformanceAnalytics.js TradeDetailCard): Zeilen
  "PnL % Positionsgröße", "PnL % Kapital", "Kursgewinn (reine Bewegung)", "TP1 getroffen" entfernt;
  alle Gebühren-Zeilen bleiben. Fee-Logik geprüft & korrekt: offen = nur gezahlte Fees (Entry/Teil-Exit)
  im Live-PnL, est_close_fee nur informativ; closed = echte Close-Fee im realized_pnl.
- Backup-Ketten: GROQ_API_KEY_BACKUP2 in env ergänzt; Bugfix _backup_env_names nahm _BACKUP1 nicht
  mit (OpenRouter hatte nur 4 statt 5 Keys) -> jetzt alle Nummern ab 1. Kette weiterhin unbegrenzt.
- Modell-Entdeckung (services/ai_model_watch.py + ai_providers.py): wöchentlicher Wächter-Lauf
  entdeckt jetzt auch NEUE Chat-Modelle je Provider (_interesting_new-Filter: openrouter nur :free,
  mistral nur -latest, gemini ohne datierte Previews/exp, TTS/Embed/Vision etc. ausgeschlossen),
  speichert sie in settings/model_watch.discovered, schaltet sie via DYNAMIC_MODELS sofort zur
  Auswahl frei (allowed_models/provider_for_model/same_provider_chain erweitert) und meldet neue
  per Website-Glocke + Telegram (erster Lauf = Baseline ohne Spam). Boot lädt discovered sofort
  (server.py: await model_watch.load_discovered). GET /api/ai/models/catalog liefert alles fürs UI.
- Modell-Berater (services/ai_model_advisor.py + POST /api/ai/team/advisor, SSE, admin): neutraler
  Chat im KI-Team-Reiter (AIModelAdvisor.js in AITeamSupervisor eingebunden); kennt Free-Tier-Caps,
  Token-Budgets (statisch + gelernt), Gewichte, Backup-Key-Anzahl, Team-Konfig, Live-Health;
  hinterfragt explizit das Haupt-Modell; läuft über die Haupt-Modell-Kette (role chat).
- AITradingPanel: entdeckte Modelle als "NEU · provider · modell" in allen Modell-Dropdowns
  (allModelOptions), Katalog-Chips aller Provider/Keys im Berater-Block.
- Maker/Taker geklärt: KI handelt mit Market-Orders -> Taker-Fee 0.06% (Bitunix); Maker 0.02% wäre
  günstiger, aber ohne Ausführungsgarantie (fee_percent=0.06 im Code korrekt).

## Iteration 46 — Papierkorb, Guard-Autonomie-Spanne, Reste-Fix, Advisor-Apply (17.06.2026, testing_agent iteration_44 grün, Backend 22/22)
Teil A (letzter Commit der Vorsession, jetzt verifiziert):
- Badge-Dedupe Marktanalyse: rechtes Duplikat (Hold/Short) entfernt; Tooltips erklären alle Badges
  (Action = KI-Entscheidung, SWING = Swing-Horizont, % = Konfidenz, Blitz = Signal ausgelöst).
- Watchdog-Reste-Fix: Rest-Positionen nach Bot-Close (<30 min) werden NICHT mehr als
  "Manuell (Bitunix)" adoptiert, sondern per flash_close an der Börse bereinigt; schlägt das fehl,
  Übernahme als "Rest nach Bot-Close" (leftover=true, manual_trade=false) + Telegram-Warnung.
  bitunix_trade: Voll-Close nutzt Börsen-Restmenge (kein Dust mehr).
- Modell-Berater "Empfehlung übernehmen": POST /api/ai/team/advisor/apply schreibt Empfehlung
  per Klick in die Rollen-Konfig (validiert Rolle + Modell, auditiert).
- Tote Groq-Modelle (llama-3.3-70b-versatile, llama-3.1-8b-instant) ersetzt: MODEL_MIGRATIONS
  mappt auf openai/gpt-oss-20b/-120b; Markt-Beobachter + News-Wächter laufen auf groq/openai/gpt-oss-20b.
Teil B (diese Session neu):
- Echter Papierkorb fürs KI-Labor: Forschungs-/ML-Reset verschiebt in Kollektion lab_trash
  (services/lab_trash.py, max. 10 Einträge/Art) statt endgültig zu löschen.
  GET /api/ai/lab/trash, POST /api/ai/lab/trash/restore (Admin); AILabPanel-Trash-Menü zeigt
  wiederherstellbare Stände mit "Zurück"-Button.
- KI-Spanne Richtungs-Guard: tune_guard_min/max (Default 1–6, FORBIDDEN für die KI selbst);
  _tuning_guard erlaubt max_same_direction-Änderungen der KI nur innerhalb der Spanne
  (0/außerhalb = nur Vorschlag an den Trader); Prompt nennt Spanne dynamisch; Setup-UI
  (ai-tune-guard-min/max-select) neben KI-Spanne Konfidenz; Datensammel-Modus weiterhin ausgenommen.
- Regressionstests NEU: tests/test_iter46_trash_and_guard_span.py (11),
  tests/test_iter46_watchdog_leftover.py (4), tests/test_iter47_advisor_apply_and_regression.py
  (testing_agent); test_ai_team_regression.py auf Nachfolger-Modelle aktualisiert
  (+ test_role_chain_dead_model_migrates).
- Offen für Render-EnvVars: GROQ_API_KEY_BACKUP2 (Key aus Vorsession, gsk_dbT5...ghgl) fehlt
  noch in Render UND in der Dev-.env – ohne ihn erwartet test_iter43 catalog 2 Groq-Backups (1 vorhanden).

## Iteration 47 — Maker-Order-Modus (18.06.2026, testing_agent iteration_45 grün, 13/13 Unit + E2E)
- Setup-Toggle "Maker-Order-Modus" (Default aus) + "Maker-Wartezeit" (15–120s, Default 45s):
  unkritische KI-Entries laufen als Post-Only-Limit (Maker-Fee, futures_maker_fee_pct Default 0,02%).
  Ausgenommen: Datensammel-Trades (sofortige Fills fürs Lernen) und manuelle Trades.
- Ausführung (bitunix_trade._maker_entry): Post-Only-Limit 0,02% passiv neben Mark, Poll auf Fill
  (get_order_detail), Timeout -> cancel_orders + Market-Fallback (Ausführung garantiert),
  Teil-Fill -> Rest storniert & Teilmenge übernommen, Post-Only-Reject/Exception -> sicherer
  Market-Fallback inkl. Orphan-Order-Cleanup (get_pending_orders) und Untracked-Recovery.
- Gebühren korrekt: Trade-Doc trägt order_kind (market|maker|taker_fallback) + entry_fee_percent;
  Entry-Fee mit Maker-Satz, Close bleibt immer Trigger/Market -> Taker (fee_percent unverändert).
  Paper-Trades simulieren den Maker-Fill (gleiche Kostenbasis).
- KI-Autonomie: virtueller Engine-Key maker_suspend_hours (1–72h direkt, 0/>72 nur als Vorschlag);
  maker_mode/maker_wait_sec/maker_suspended_until sind FORBIDDEN für die KI. Auto-Aussetzung:
  Fill-Quote < 30% über die letzten >=8 Maker-Versuche -> 24h aussetzen + Glocke/Telegram-Meldung
  (maker_mode_stats, letzte 30 Versuche). Trader reaktiviert per "Wieder aktivieren"-Button
  (gelbe Zeile im Setup) oder Toggle.
- Erweiterte Trade-Infos (Details-Modal): Zeile "Order: Market (Taker x%) / Limit Post-Only (Maker x%)
  / Limit→Market-Fallback" + Hinweis Close = Trigger/Market (Taker); Alt-Trades defaulten auf market.
- Bitunix-Client erweitert: place_order effect-Param (POST_ONLY), get_order_detail,
  get_pending_orders, cancel_orders.
- Tests NEU: tests/test_iter48_maker_mode.py (14: parse_order_fill, Fake-Börse Fill/Timeout/
  Partial/Reject/Exception, Engine-Aussetzung, Leitplanken, API-Clamps, Sparstatistik-Mathe).
- Maker-Sparstatistik (18.06.2026): GET /api/ai/maker-stats aggregiert real gesparte Entry-Gebühren
  (Taker- minus Maker-Satz) über alle order_kind=maker-Trades, getrennt live/paper, plus
  Fallback-Anzahl und Fill-Quote (maker_mode_stats). Anzeige als grüne Zeile im KI-Setup
  (data-testid ai-maker-savings-row), sichtbar wenn Modus an oder Ersparnis vorhanden.

## Iteration 48 — Papierkorb Endgültig-Löschen (18.06.2026, pytest 12/12 + UI-E2E grün)
- POST /api/ai/lab/trash/discard (Admin): Papierkorb-Eintrag endgültig löschen (lab_trash.discard).
- AILabPanel: X-Button je Eintrag (data-testid ai-lab-trash-discard-<id>) mit Confirm-Dialog,
  grau -> rot beim Hover; Toast "Endgültig gelöscht"; danach weder Restore noch Discard (404).
- Test erweitert: test_iter46_trash_and_guard_span.py::test_discard_forever (Auth, 200, 404-Kette).

## Iteration 49 — Leere-Antwort-Fix + 402-Log (18.06.2026, testing_agent iteration_46 grün)
- Bug: OpenRouter-Free-Modelle (gpt-oss-20b:free) liefern sporadisch 200 mit leeren choices
  ("Leere Antwort (keine choices)") -> Modell wurde sofort aufgegeben, Ausfall-Meldung "betroffen: Analyst".
- Fix (services/ai_providers.py generate_chain): is_empty_response_error(); bei leerer Antwort
  1x Retry (2s) auf demselben Key, dann Key-Rotation (andere Upstream-Route), erst wenn alle
  Keys leer -> nächstes Modell; KI-Team-Detail jetzt verständlich ("Free-Modell überlastet,
  Fallback übernimmt"). Harte Fehler springen weiterhin sofort zum nächsten Modell.
- is_payment_error(): Cerebras-402 (Payment Required) wird im Log korrekt als
  "Kontingent erschöpft/Guthaben nötig (402)" statt "rate-limited" gelabelt (Verhalten gleich).
- Tests NEU: tests/test_iter49_empty_response_retry.py (5, LLM gemockt).

## Iteration 50 — Guthaben-Schutz verifiziert & festgeschrieben (18.06.2026, 10/10 grün)
- User-Vorgabe: nur Free-Tiers; Guthaben (OpenRouter/Gemini) NUR für explizit gewählte
  Paid-Only-Modelle (DeepSeek & Co.), Limits fängt die Backup-/Fallback-Kette ab.
- Verifiziert (war bereits korrekt designt): PAID_MODELS_NO_FALLBACK nie in FALLBACK_ORDER;
  Rollen-Ketten aller Presets enthalten nie Paid-Modelle; Modell-Wächter schaltet bei
  OpenRouter nur :free-Modelle frei; Paid-Modelle in Dropdowns mit Preis + "bezahlt" gelabelt;
  explizite Paid-Auswahl bleibt möglich (nur Auto-Ausweichen verboten).
- 402-Label angepasst (User-Wunsch): "Free-Kontingent erschöpft (402) – Backup übernimmt"
  statt "Guthaben nötig" (Free-Tier-Sicht, Verhalten unverändert).
- Tests NEU: tests/test_iter50_free_tier_protection.py (5) schreiben die Garantien dauerhaft fest.

## Nächste Aufgaben (Kandidaten)
- Optional Limit-Order-Modus (Maker-Fee) für nicht-zeitkritische KI-Entries ✅ erledigt (Iter 47)
- Span-in-option React-Dev-Warning (kosmetisch, vorbestehend aus Iter42) bereinigen
- Papierkorb optional: Einträge endgültig löschen (Discard-Button)
