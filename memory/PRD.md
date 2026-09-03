# PRD – KI-Trader (externes Repo, Render-Deploy)

## Original-Problemstellung
Produktive Daytrading-Website (github.com/dean06greif-ai/KI-Trader, Branch MEIN-NEUER-BRANCH,
FastAPI + React + MongoDB, Bitunix Futures) soll sauber, modular, rückwärtskompatibel
verbessert werden (Originalstruktur beibehalten, Render-Deploy). Vier Punkte:
1. Watchdog legt bei KI-Limit-Orders den Trade zusätzlich als „Manuell (Bitunix)“ an.
2. PnL nach Trade-Schluss soll dem echten Bitunix-Ergebnis entsprechen (BE-SL → −3 USDT, nicht 0).
3. Strategie-Copilot las 1600 USDT Drawdown als 1600 %.
4. Setups sollen nicht gesperrt, sondern nur in die Paper-Datensammlung zurückgestuft werden
   (automatische Re-Promotion).

## Nutzer-Entscheidungen
- Repo in /app/kitrader geklont, Änderungen in Originalstruktur, User pusht/deployt selbst.
- Duplikate: nicht doppelt anlegen, KI-Trade bleibt führend.
- PnL-Abgleich inkl. Fees/Funding aus Bitunix-History überschreibt den lokalen Wert.
- Keine harten Sperren mehr – nur Paper-Rückstufung mit automatischer Re-Promotion.
- Live-Tests mit echten Bitunix-Keys, Mini-Orders erlaubt.

## Architektur / Umsetzung (Stand 2026-06)
- `backend/services/entry_inflight.py` (neu): In-Flight-Registry für laufende Entries,
  Duplikat-Erkennung/-Bereinigung. `AutoTradeManager.on_signal` ist jetzt Wrapper um
  `_on_signal_impl`.
- `backend/services/position_watchdog.py`: ctime-Parsing, `adopt_grace_sec` (90 s),
  `_adoption_allowed`, `_dedupe_bound`, Registry vor Rest-Bereinigung, Mengen-Ranking.
- `backend/services/bitunix_trade.py`: `pick_new_position`, `_resolve_new_position_id`,
  `_bound_position_ids`; `_after_close` ruft den PnL-Abgleich VOR Telegram/Rewards/Guard.
- `backend/services/pnl_reconcile.py` (neu): Abgleich mit `get_history_positions`, Loop alle 5 min,
  API `/api/autotrade/pnl-reconcile/{status,run}`.
- `backend/services/strategy_copilot.py`: `describe_metrics`, `metrics_summary`,
  `sanity_check(metrics, capital)`, Einheiten-Regel im System-Prompt; `Backtester.js` liefert
  `per_strategy`-Metriken in den Kontext.
- `backend/services/ai_playbook.py`: `disabled` immer leer (Migration → `live_blocked`),
  `demotion_candidates`, `migrate_disabled`, `eval_since`, `live_ready_for` (Reife-Cache),
  genutzt in `ai_engine._setup_live_gate` und `ai_diagnosis`.
- UI: SettingsPanel (Watchdog-Zähler/Hinweis), AIDiagnosisPanel („↩ rückgestuft“).
- Doku: `backend/README_FIX_WATCHDOG_PNL_SETUPS.md`; Live-Skript
  `backend/scripts/live_e2e_watchdog_dedupe.py` (echte Mini-Order, nur DEV-DB).

## Verifikation
- Neue Unit-Tests (59) + Testing-Agent-API-Suite (16) grün; gesamte Unit-Suite 915 grün,
  3 vorbestehende Fehler unabhängig (`test_fix_custom_ai_trades`, `test_iter49_empty_response_retry`,
  `test_strategy_insights`).
- Live gegen Bitunix: Mini-Order XRPUSDT → in-flight nicht übernommen → Karenz → Übernahme →
  Dedupe → Close → echter PnL −0.0017625 USDT übernommen. Copilot-Antwort: „16 % des Startkapitals“.

## Iteration 2 (2026-06) – Nacht-Serie + Paper-Statistik
- Nacht-Serie/Job-Warteschlange: `services/job_series.py`, `routers/job_series.py`, UI `JobSeriesPanel.js`,
  `SeriesResultDetail.js`, „+ Serie“-Buttons in Backtester/Optimizer/Regime-Lab, Telegram-Toggle `job_series`.
- Bugfix: Datensammel-Trades (data_collection) nicht mehr in Paper-Statistik (strategy-comparison, balance-Overlay,
  performance, Analyse-Filter mit neuem „Sammlung“-Filter); Karteileichen im Strategie-Vergleich ausgeblendet
  (include_stale/include_collection Flags, Badge „gelöscht“).
- Tests: `test_job_series.py`, `test_paper_stats_collection_stale.py`, `test_iter39_*` (Testing-Agent) – alle grün.

## Backlog / Nächste Schritte
- P2: Karteileichen-Ausblendung auch in der Strategie-Performance-Liste des Analyse-Panels.
- P1: `test_iter37_lifecycle_resources_api` erwartet 10 Setups (jetzt 13) – Test aktualisieren.
- P1: Vorbestehende Unit-Fehler (3) prüfen.
- P2: Watchdog-Karenz (`adopt_grace_sec`) als Eingabefeld im SettingsPanel.
- P2: PnL-Abgleich-Status (letzter Lauf, korrigierte Trades) im Settings-/Analytics-Panel anzeigen.
- P2: Alte geschlossene Live-Trades (>48 h) optional per Admin-Endpoint nachziehen.

## Iteration 3 (2026-06) – Render-Deploy-Fix (Root-Template)
- Render-Logs zeigten Fehler aus dem Emergent-Root (`/app/frontend`, `/app/backend`), nicht aus `/app/kitrader`.
- `/app/frontend/package.json`: react-day-picker 8.10.1 → 9.14.0 (React-19-Peer), date-fns 4.1.0 bleibt; `ui/calendar.jsx` auf v9-API
  (identisch zu kitrader). `npm install` löst jetzt ohne `--legacy-peer-deps` auf; `yarn build` grün.
- `/app/backend/requirements.txt`: `emergentintegrations==0.2.0` entfernt (nicht genutzt, privater Index).
- Nutzer-Aufgabe: Render „Root Directory“ auf `kitrader/backend` bzw. `kitrader/frontend` prüfen, falls Repo den Unterordner enthält.
- Backlog verschoben („Später“): Karteileichen-Filter in Strategie-Performance-Liste des Analyse-Panels.


## 2026-06 – Repo-Struktur wiederhergestellt
- `/app/kitrader/*` (echte App) 1:1 nach `/app` verschoben; Emergent-Platzhalter `backend/`, `frontend/` ersetzt; Git-Submodul-Link `kitrader` entfernt.
- Render Root Directory bleibt `frontend` bzw. `backend` (wie im main-Branch).
- Supervisor läuft jetzt direkt aus `/app/backend` + `/app/frontend`.
- pytest: 986 passed; vorbestehende Failures: test_strategy_insights::test_daily_quota_cooldown_until_utc_midnight, test_iter49_empty_response_retry::test_empty_response_single_retry_succeeds, test_fix_custom_ai_trades::test_reject_deregisters_strategy_and_closes_trades. iter38-API-Tests benötigen KITRADER_BASE_URL=http://localhost:8001.
- Offen (P1): Gelöschte Strategien ("Karteileichen") in Performance-Analytics ausblenden (routers/ai.py get_performance/get_daily_analytics, PerformanceAnalytics.js).

## 2026-06 – Karteileichen + Paper-Badge
- `/api/autotrade/balance`: Primär-Stats nur noch für aktuellen Modus, ohne data_collection und ohne Karteileichen (is_stale_strategy); Paper-Overlay ebenso.
- `/api/autotrade/trades`: neues Feld `stale_strategy` je Trade.
- PerformanceAnalytics.js: `filterFn` blendet `stale_strategy` überall aus; PnL-Summe immer aus gefilterten Trades.
