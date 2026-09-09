# KI-Trader (Crypto Scanner) – Iteration Log

## Ursprung
Bestehende, produktive Daytrading-Website (extern auf Render deployt). Repo:
https://github.com/dean06greif-ai/KI-Trader (branch conflict_080926_1721).
Stack: FastAPI (backend/) + React (frontend/) + MongoDB Atlas. Zusatzmodule:
local_worker/ (Rechen-Worker), ibeam_gateway/ (IBKR). Original-Ordnerstruktur
MUSS für den Render-Deploy erhalten bleiben (nur bestehende Dateien editiert).

## Grundsatz
Sauber, modular, rückwärtskompatibel. Keine Schnelllösungen. Stabilität vor
aggressiven Änderungen.

## Umgesetzt (2026-06 / diese Iteration)
1. **Lokaler-Worker-Token kopierbar** (`frontend/src/components/LocalWorkerPanel.js`):
   - `loadToken()` mit Retry (Cold-Start/Admin-Session), robustes `copy()` mit
     `execCommand`-Fallback, eigenes MARKIERBARES Token-Feld (`lw-token-field`)
     + Button `lw-token-copy`, `lw-cmd-copy` nicht mehr dauerhaft disabled.
   - Worker-Code (`local_worker/worker.py`) bewusst UNVERÄNDERT gelassen.
2. **Setup-Reife lädt automatisch** beim Öffnen des Verlauf-Panels
   (`AIEquityPanel.js`): `loadMaturity()` mit Retry-wenn-leer, Reload-Button
   aktualisiert auch die Reife-Tabelle.
3. **Live-Logik ↔ Datensammel-Modus-Umschalter** für die Setup-Reife
   (`AIEquityPanel.js` + `SetupMaturityTable.js`), analog zum Equity-Umschalter.
   Backend liefert neue Felder `collect_trades/collect_winrate/collect_pnl`
   (`ai_playbook.py`: `setup_stats(paper_only=True)` je Klasse + global,
   `maturity_overview` erweitert).
4. **Reset bei starker Setup-Änderung** (`setup_lifecycle.py` + `ai_playbook.py`):
   Neue reine Funktion `is_strong_change` (>=30 % SL/TP/Hebel ODER TF-Wechsel).
   `evolve_versions` markiert starke Versionen mit `strong`. `_refresh_scope`
   setzt bei starker Änderung `eval_since[setup]` der Anlageklasse zurück →
   Validierung startet neu. Kleine Tunings (<30 %, gedeckeltes ±20 %) lösen
   das NICHT aus.

## Verifikation
- Backend: `/api/ai/playbook` liefert collect_*-Felder (curl bestätigt);
  Reset end-to-end bestätigt (`eval_since` wird gesetzt); 3 Unit-Tests grün
  (`backend/tests/test_strong_change_reset.py`).
- Frontend: Testing-Agent 3/3 PASS (Token kopierbar, Reife auto-load, Umschalter).

## Deploy-Hinweise
- `.env` ist gitignored → lokale Test-`.env` (Paper-Modus, lokale Mongo) gelangt
  NICHT ins Repo; Render-Env-Variablen des Nutzers bleiben unberührt.
- Lokale Testumgebung nutzt bewusst KEINE echten Bitunix/LLM-Keys (kein Live-Trade-Risiko).

## Umgesetzt (09/2026 – Iteration 53: Fallback-Spam & Ursachen)
Auslöser: Glocke voll mit "KI-Warnung … Fallback-Kette übernimmt" (Nemotron leere
Antwort, Mistral 429 Code 1300). Diagnose: KEIN Key-Problem.
- **A) Nemotron/OpenRouter Leerantwort** (`ai_providers.py`): Reasoning-Modelle
  legen Antwort in `message.reasoning` ab / Denken frisst Ausgabe-Budget.
  Fix: `max_tokens=8192` für OpenRouter, JSON aus Reasoning-Feld übernehmen
  (`_reasoning_text`, `_json_from_reasoning`), Retry nach Leerantwort mit
  `reasoning: {enabled: false}` (nur Retry-Pfad → keine Qualitätseinbuße im
  Normalfall). Analyst-Preset bewusst bei nemotron-3-super belassen.
- **B) Mistral 1-RPS-Limit** (Code 1300 = pro Workspace, Backup-Keys nutzlos):
  Retry nach 1,5 s auf demselben Key (`is_mistral_rps_limit`), Cooldown 65 s
  statt 10 min (`_quota_cooldown_s` erkennt `'code': '1300'`).
- **C) Glocke nur noch bei KOMPLETT-Ausfall einer Rolle** (Trader-Entscheid):
  `record_result` löst keine `notify_model_failure` mehr aus; erfolgreicher
  Fallback (auch Notfall außerhalb Team) meldet nicht mehr – bleibt im
  KI-Status (`health_status.active_fallbacks`, `_recent_failures`) sichtbar.
  `notify_model_failure`/`summarize_model_failures` bleiben als Funktionen erhalten.
- Tests: `backend/tests/test_iter53_nemotron_reasoning_mistral_rps.py` (9 grün),
  Regression 69/69 in den betroffenen Provider-/Notify-Tests.

## Umgesetzt (09/2026 – Iteration 54: Strategien umbenennen/duplizieren, KI-Trader-Reiter im Backtester, KI-Revision, Lektionen)
Repo-Stand: Branch `conflict_080926_2051` 1:1 nach /app übernommen (lokale .env, lokale Mongo).
- **Rename/Duplicate für ALLE Strategien** (`routers/strategies.py`, `strategies/registry.py`, `base_strategy.py`, `server.py`):
  - `POST /api/strategies/{id}/rename {name}`: Custom (Definition), Variante (strategy_variants) und
    Built-in (Override `scanner.settings.strategy_names`, beim Boot via `apply_name_overrides`).
  - `POST /api/strategies/{id}/duplicate`: Custom -> Kopie der Regel-Definition; Built-in -> **Variante**
    (`variant_<id>`, Collection `strategy_variants`, `registry.upsert_variant`: gleiche Klasse, eigene ID/Name/
    Timeframe/Parameter/Trade-/Backtest-Einstellungen). `ai_trader` nicht duplizierbar (400).
    Metadaten: `is_variant`, `base_id`. Export/Import/Delete unterstützen Varianten.
  - Worker-Kompatibilität ohne Worker-Änderung: `list_custom_definitions()` liefert Varianten mit
    `kind='variant'`, `load_custom()` baut sie wieder auf.
  - UI (`StrategyBuilder.js`): Umbenennen-Button (`rename-strategy-<id>`, window.prompt) und Duplizieren
    (`duplicate-strategy-<id>`) für alle Zeilen, Badge VARIANTE.
- **Backtester-Reiter** (`Backtester.js`, `Backtester.css`, `AITraderSeeding.js`): Reiter „Strategien" |
  „KI Trader · Setups" (`bt-tab-strategies`/`bt-tab-ai`, in localStorage gemerkt). KI-Reiter hat eigene
  Anlageklassen-Chips, Zeitraum, Modi Einmal/Auto-Schleife/**KI-Schleife**, KI-Optionen, eigenen Start-Button;
  alter Doppel-Chip `bt-strat-ai_trader` entfernt (ai_trader wird aus der Strategie-Liste gefiltert).
- **KI-Revision im Setup-Backtest** (`services/setup_backtest/revise.py` NEU, `runner.py`, `auto.py`,
  `routers/setup_backtest.py`): nach Setup ohne Edge schlägt die KI (Rolle research_analyst) einen neuen
  Detektor-Parameter-Satz vor (`sanitize` klemmt in [0.5×min, 2×max] der Varianten, Ganzzahlen erhalten,
  mind. eine Änderung). Modus `ai_loop`: sofort testen, bis `target_passed` Setups je Klasse bestehen oder
  `ai_rounds` (max 10) aufgebraucht. Modi single/loop: Vorschlag als `ai_proposal` vormerken, beim nächsten Lauf
  zuerst getestet. Textrevision geht zusätzlich an `ai_playbook.revise_setup` (nur wenn dort erlaubt).
  API: `/api/ai/playbook/backtest` liefert `modes`, `ai_defaults`; run/auto akzeptieren `mode`, `ai_revise`,
  `ai_rounds`, `target_passed`. Ohne LLM-Key wird die Revision übersprungen (Log).
- **Lektionen** (`services/ai_learning.py`): Trade-Zeilen im Lernprompt enthalten jetzt Setup, TF und Exit-Art
  (`closed_by`); neuer Block „SETUP-REIFE & BACKTEST-BEFUNDE" (`ai_playbook.context_text`) mit Hinweis,
  Lektionen setup-bezogen zu formulieren. Bestehende Schutzmechanismen (Validation-Gate, MasterPrompt-Audit,
  Dedupe, dormant, Absolut-/Sizing-Filter) unverändert.
- Tests: `tests/test_strategy_variants_rename.py` (6), `tests/test_setup_backtest_ai_revision.py` (8, inkl.
  gemockter LLM-Antwort), Testing-Agent `tests/test_iter53_variants_setup_backtest.py` (9 API) – alle grün;
  Frontend-Flows per Playwright bestätigt.

## Backlog / offen
- P2: Sammel-Statistik ggf. zusätzlich in der globalen Equity-Kurve spiegeln.
- P2: Frühere Fetch-Fehler beim Cold-Start durch Retry/ErrorBoundary glätten (Log-Noise).

## Iteration 2026-09-09 – KI-Revision mit Tiefen-Diagnose, Lernschleife, mehr Stellschrauben (Phase 1b)
Problem: „KI-Revisionen vorgemerkt (35)" brachten kaum Verbesserung – die KI sah nur Trades/WR/PnL, durfte nur
3–5 Basis-Parameter drehen und startete jede Runde vom letzten (ggf. schlechteren) Vorschlag.
Nutzer-Entscheidungen: alles (Analyse + Stellschrauben + Lernschleife); Ziel langfristig profitable Trades;
Arbeit im 1:1-Repo unter /app (Nutzer pusht selbst); echte LLM-Keys lokal (Exchange/Telegram/IBKR lokal NICHT).
- **`services/setup_backtest/analysis.py` (neu, rein)**: `diagnose()` (Exit-Verteilung, Long/Short, Symbole,
  Uhrzeit Berlin, PF, Payoff, Erwartungswert, Gebühren, Equity-Kurve, max. DD, Verlustserie, MFE/MAE in R),
  `compact()`, `describe()`, `score()` (PnL/Trade, OOS 60 %), `best_entry()`, `lessons()`, `param_diff()`.
- **`detectors.py`**: optionale Schlüssel mit Default = Altverhalten: `tp1_r`, `max_bars`, `sides`, `vol_min`,
  `vol_max`, `hour_from`, `hour_to`, `htf_trend` (1h EMA20/50) + `body_atr` (breakout), `sl_atr`
  (trend_follow/divergence), `rsi_lo`/`rsi_hi` (divergence); `apply_filters()` nur im `run_detector_params`-Pfad;
  `optional_params()/param_defaults()/param_help()`.
- **`revise.py`**: Prompt = Historie mit Score + Diagnose der Basis + Lernschleife + Parameter-Hilfe (Bereich,
  Default, Bedeutung); Antwort zusätzlich `expect`; `changes`/`base` gespeichert; Basis = bester Eintrag nach
  Score; Overfitting-Bremse `MAX_CHANGES = 4`.
- **`runner.py`**: `run_variant` → `diag` + `effective_params`, `max_bars` an Simulator; `_hist` mit
  params/diag/reason/expect/base; Rows/State mit `diag`, `lessons`; `overview()` mit `param_help`.
- **UI `AITraderSeeding.js`**: Änderung + Erwartung je vorgemerkter Revision, OOS-Diagnose in der Tabelle,
  letzte Lektion je Setup. Doku: BACKTEST_SEEDING_PLAN.md (Phase 1b).
- Tests: `backend/tests/test_setup_backtest_analysis.py` (18), `test_setup_backtest_ai_revision.py` angepasst,
  `test_ai_playbook_backtest_api.py` (Testing-Agent, 3 API) – 57/57 grün; 2 echte ai_loop-Läufe (Krypto) ok:
  trend_follow IS von −2.15 auf +6.94 nach KI-Rev.3, KI wählt Basis KI-Rev.3, kappt auf 3–4 Änderungen.
- Pre-existing, nicht durch diese Iteration: test_fix_custom_ai_trades (1), test_strategy_insights (1),
  test_iter38_* (brauchen Dev-Server :8055).

### Backlog (neu)
- P1: Score mit Drawdown-Strafe; P1: Lessons der Backtest-Revisionen in den Live-Prompt (ai_lessons).
- P2: Walk-Forward mit mehreren OOS-Fenstern; P2: Phase-2-Detektoren (SMC, liquidity_sweep, funding_fade).
