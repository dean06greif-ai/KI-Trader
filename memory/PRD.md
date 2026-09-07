# KI-Trader – PRD / Arbeitsstand

## Original-Problemstellung
Produktive, extern auf Render deployte Daytrading-Website (Repo `dean06greif-ai/KI-Trader`, Branch
`conflict_060926_1414`; Ordnerstruktur bleibt, Änderungen als Patch/Dateien zum Selbst-Pushen). Grundsatz:
sauber, modular, rückwärtskompatibel, Regressionstests vor größeren Änderungen.
Aufträge 06.09.2026: (1) verlorene Daten (Paper-Trades, MasterPrompt-Regeln, Lektions-Regeln) wiederherstellen,
(2) KI-Trader kritisch prüfen (zu viel / sinnlos / fehlt) und wichtigste Punkte umsetzen,
(3) Backtest-Seeding der Setups: „Testen → verbessern → erneut testen“ als echter Prozess/Automatik.

## Nutzer-Entscheidungen
- Datenquelle unbekannt → im Code analysieren; Produktiv-DB read-only prüfen und ggf. wiederherstellen.
- Datenwiederherstellung + Backtest/Seeding-Prozess + KI-Trader-Analyse mit Umsetzung der wichtigsten Punkte.
- Änderungen auf Branch `conflict_060926_1414` vorbereiten, User pusht selbst.

## Architektur-Erkenntnisse
- Daten: MongoDB Atlas `crypto_scanner` (Supabase nur ai_memory-Tabelle). MasterPrompt/Lektionen in
  `settings` (`ai_master_prompt`, `ai_lessons`), Paper-Trades in `auto_trades` (mode=paper).
- RCA Datenverlust: Testing-Agent-Läufe (GCP-IPs, python-requests) gegen Preview, die an der PRODUKTIV-DB hing
  → `ai/trader/reset` (134 Trades), 23× `analytics/clear`, MasterPrompt 20× mit Test-Text überschrieben,
  Test-Lektion angelegt. Details: `KI_TRADER_ANALYSE_0609.md`.

## Umgesetzt (06.09.2026)
- `services/data_recovery.py` + `routers/recovery.py` + Boot-Migration `data_recovery_0906_v1`
  (MasterPrompt aus History, Test-Lektionen raus, 160 Paper-Trades aus `ai_trade_actions` rekonstruiert,
  `recovered: True`; lokal gegen Prod-Kopie verifiziert).
- Papierkorb `services/trade_trash.py` (`auto_trades_trash`) für `analytics/clear` + `ai/trader/reset`;
  API `/api/analytics/trash[...]`, UI `TradeTrashPanel` im Lösch-Dialog.
- MasterPrompt-History 50 + `POST /api/ai/master-prompt/restore`; UI „Frühere Versionen“.
- Lektions-History `ai_lessons_history` + `GET /api/ai/lessons/history`, `POST /api/ai/lessons/restore/{id}`.
- Seeding: Feintuning (`detectors.tune_candidates`, `runner.tune`, Status `tuned`), Automatik
  (`setup_backtest/auto.py`, `GET/POST /api/ai/playbook/backtest/auto`, Loop in `server.py`), UI im
  Seeding-Panel. Default aus.
- Tests: `tests/test_data_recovery_and_trash.py`, `tests/test_seed_auto_and_tuning.py`, bestehende
  `test_setup_backtest_seeding.py` grün; Testing-Agent Iteration 46: 17/17 Backend, Frontend ok.
- Prozess: `tests/README_TESTING.md` (nie gegen Prod-DB testen); Dev/Preview läuft auf lokaler DB
  `crypto_scanner_dev`. Patch: `KI_TRADER_CHANGES_0609.patch` (28 Dateien, keine Secrets).

## Umgesetzt (06.09.2026, 2. Runde)
- Strategie-Labor → Playbook (`migrate_to_playbook`, Boot-Migration `strategy_lab_to_playbook_v1`): 32 Test-
  Kandidaten gelöscht, 1 KI-Idee = Alias von trend_follow (geschlossen mit Hinweis), Labor stillgelegt
  (allow_ai_create/auto_develop aus, Prompt-Block „STILLGELEGT“), Test `tests/test_strategy_lab_to_playbook.py`.
- Markt-Beobachter LLM-Kurz-Einschätzung aus (`observer_llm_off_v1`); Tages-Reporter bewusst beim LLM belassen.
- Seeding-Panel: Automatik-Verlauf-Tabelle (auto.history). Testing-Agent Iteration 47 grün.

## Backlog (Vorschläge – erst nach Freigabe)
- P1: ML-Findings (2 481) aus `ai_knowledge` heraushalten (nur ML-Gate-Report).
- P2: Analyst-Budget (80 % der Tokens): Intervall/Prompt-Variante prüfen.
- P2: Chat-Archiv-Housekeeping; Alt-Artefakte (`custom_14f031fftest`-Signale, Collection
  `db.ai_lesson_candidates`) aufräumen.

## Nächste Schritte für den User
1. Patch anwenden/pushen, Render deployt → Log `Boot-Migration Data-Recovery` bzw. `GET /api/admin/recovery/report`.
2. MasterPrompt im UI prüfen (Hebel-Deckel 50 = letzter echter Stand; falls anders gewünscht, anpassen).
3. Backtester → „KI Trader · Setups“ → Automatik einschalten (z. B. 48 h, 90 Tage, gewünschte Klassen).

## Runde 07.09.2026 – Branch `conflict_070926_0126`
### Auftrag
(1) „Runner-Ziele“ erklären, (2) Margin-Trick (Marge raus + Hebel max) & Runner „ins Unendliche“ mit
Key-Level-Trailing (News-Trades) inkl. SL↔Kurs/Liq-Abstand, (3) Nachanalyse nach Trade-Close
(TP/SL später? Limit später/nicht gefüllt?) mit starkem Overfitting-Schutz + Backtester-Bezug,
(4) KI darf Setups live in kleinen Schritten anpassen & zu besseren Versionen zurück.
Nutzer-Entscheidungen: Repo 1:1 nach /app, Struktur bleibt; Runde 1 = Analyse + Runner-Erklärung +
Nachanalyse; Margin-Trick später auch live (Bitunix/IBKR freigegeben); echte Keys lokal, ABER lokale
Mongo (nie Prod-DB, siehe RCA oben) und Telegram lokal aus.

### Architektur-Erkenntnisse
- Runner = Swing-Restposition nach TP1 ohne festes Ziel (`ai_trade_manager.py`; `bitunix_trade.py`
  Runner-Secure: Gewinnsicherung, Margen-Freisetzung, Max-Hebel, SL-Liq-Guard, Key-Level-Trail) →
  Margin-Trick existiert bereits, aber nur für `horizon=swing`; Datensammel-Trades bewusst unangetastet.
- Setup-Versionierung/Rollback existiert: `services/setup_lifecycle.py` (`evolve_versions`,
  `should_rollback`, `TUNE_MAX_STEP` ±20 %).

### Umgesetzt (07.09.2026)
- Nachanalyse `services/trade_postmortem.py` (reine Regeln): TP ×0.75/1.25/1.5/2.0, SL ×0.75/1.25/1.5,
  Runner, Nachlauf-MFE/MAE (2× Dauer, 30 min–24 h), Urteile; verfallene Limit-Orders (Bewegung
  verpasst / Verlust vermieden). Overfitting-Schutz: ≥8 Trades, Median, ≥60 % Konsistenz, Split-Half,
  ≥0.15R, Schritt ±20 %, Backtest-Bestätigung, 30 Tage. Collections `trade_reviews`, `limit_reviews`,
  Flag `postmortem` auf `auto_trades`/`ai_limit_orders`; Loop alle 10 min (`server.py`).
- `routers/postmortem.py`: `GET /api/ai/postmortem/summary|trade/{id}|context`, `POST /run` (Admin).
- Prompt-Block „NACHANALYSE“ in `ai_engine_context._analysis_extra_blocks` (nur robuste Befunde).
- UI `AIPostmortemPanel` im KI-Trader-Panel → „Lernen“ (unter Belohnungssystem).
- Tests `tests/test_trade_postmortem.py` (17 grün), Seed `tests/_seed_postmortem.py`, Testagent
  Iteration 49 grün. Bestands-Suite: 14 identische umgebungsbedingte Fehler wie Original → keine Regression.

### Umgesetzt (07.09.2026, Runde 2)
- `services/runner_policy.py` (rein): Runner auch für Scalp-/News-Trades (`runner_scalp_enabled`,
  `runner_scalp_news_only`, Endziel 8R/max 15 %), `noise_safe_sl` (Mindestabstand SL↔Kurs =
  max(ATR, 0.1 %/Swing 0.3 %)), `liq_safe_sl` (SL vor der Liq nach Margen-Freisetzung),
  `trail_candidate` im Key-Level-Trailing (`bitunix_trade.py`). Hooks in `ai_engine.py`
  (`runner_allowed`, `scalp_runner_tpf`, Prompt-Text) und `ai_trade_manager.py`.
- Gewinnschutz für Datensammel-Trades per Policy-Schalter (`collection_enabled`, Default aus,
  `collection_trigger_pct` 60 %) – `PATCH /api/autotrade/ai-protection`.
- Nachanalyse → Setup-Version: `trade_postmortem.proposals()` (nur Backtest-bestätigt) →
  `setup_lifecycle.evolve_versions(hint=…)`: genau ein Parameter, ±20 %, Note „Nachanalyse: …“,
  nicht doppelt, Auto-Rollback greift. UI: Schalter „Runner bei News-Scalps“ im KI-Panel.
- Tests `tests/test_runner_policy.py` (11), Gesamt-Suite ohne neue Fehler.

- Runde 3 (07.09.): `runner_policy.trail_decision` (Grund: Rauschen/Liq) → Trade-Events
  „TRAIL-SKIP …“ / „Rausch-Schutz …“ (dedupliziert über `trail_reject_note`); Trade-Chart-Endpoint
  liefert `review` (Nachlauf-Fenster + Extreme) → `TradeChart.js` zeichnet „Nachlauf ±R“-Linien und
  Nachanalyse-Notiz; `GET /api/ai/runner-stats` + `runner_policy.runner_stats` (Runner vs. voller TP)
  im Nachanalyse-Panel. Tests: `test_runner_policy.py` 13 grün.

### Backlog (nächste Runden)
- P0: Runner/Margin-Trick auch für News-/Scalp-Trades (Flag), Trailing an Key-Levels + Margen-
  Freisetzung kombiniert, Mindestabstand SL↔Kurs (ATR-Rauschen) und SL↔Liq prüfen.
- P1: Robuste Nachanalyse-Befunde automatisch als Profil-Versionsvorschlag an `setup_lifecycle`
  (ein Parameter, ±20 %, Rollback bei Verschlechterung) – aktuell nur Prompt-Hinweis.
- P1: Nachanalyse für IBKR/Aktien (Kerzenquelle IBKR); P2: Befunde als OOS-Check im Setup-Backtester.


## Runde 07.09.2026 – Branch `conflict_070926_1157` (Runde 4)
### Auftrag
(1) Paper-Badge + Strategie-Vergleich: nur Trades von Strategie×Asset-Kombinationen, die derzeit
aktiv sind (paper/live); (2) KI-Rollen/Keys prüfen (Backup-Bedarf, weggefallener OpenRouter-Free-GPT
→ nur Empfehlung); (3) KI-Trader-Einstellungen, MasterPrompt-Regeln, Lektions-Regeln kritisch prüfen,
Verbesserungen direkt einbauen (konservativ). Nutzer: Repo 1:1 in /app, Original-Struktur bleibt.

### Umgesetzt
- `services/active_scope.py` (rein) + Integration in `GET /api/autotrade/balance` (Paper-Werte,
  `active_only`) und `GET /api/analytics/strategy-comparison` (`only_active` Default an,
  `inactive_hidden`); UI-Schalter in `StrategyComparison`, Header-Tooltip.
- `ai_providers`: tote OpenRouter-Slugs (`openai/gpt-oss-20b:free`, `nemotron-nano-9b-v2:free`)
  entfernt + `MODEL_MIGRATIONS` → `groq/openai/gpt-oss-20b`. `ai_roles`: Presets ohne Cerebras
  (402) und Groq (7k Budget) nur als letzte Stufe großer Prompts; `_fill_missing_fallback2` für
  kritische Rollen; Migrationen werden persistiert.
- `ai_master_prompt`: Regel `block_coin_ranking_lessons` (+ `is_coin_ranking`), Lektions-Policy v2
  (Regel 4/9-Widerspruch, Regel 6 schärfen statt doppeln, Regel 10 Coin-Ranglisten, Regel 11 keine
  Meta-Lektionen), automatisches Anheben nie geänderter Alt-Policy (`LEGACY_LESSON_POLICIES`).
  UI `AIGovernancePanel`: drei Lektions-Qualitätsregeln schaltbar.
- Bericht `KI_TRADER_REVIEW_0709.md` (Keys, Rollen, Einstellungs-Widersprüche, Empfehlungen).
- Tests: `tests/test_active_scope_and_ai_review.py` (13), `tests/test_active_scope_api.py` (4 E2E);
  Unit-Suite 1 108 grün, Alt-Fehler identisch zum Original.
- Dev-Setup: `scripts/dev_copy_prod_db.py` (Prod → lokale Dev-DB, read-only Quelle).

### Wichtigste Befunde (User-Entscheidung)
- OpenRouter-Guthaben ~4 $ übrig (~1,10 $/Tag) → Trade-Manager/Lern-Modul-Primärmodelle bezahlt.
- Gemini nur 1 Key (Fallback in 7 Rollen); Cerebras 16 Keys alle 402 (Free-Tier weg).
- Hebel-Deckel inkonsistent (MasterPrompt 50 vs. Trade-Manager/Runner 200 vs. Sizing 15);
  Fee-Wächter aus, Korrelations-Guard aus (max_same_direction 8), keine Tages-Reißleine.

### Backlog
- P2: Recovery-Trades (`strategy_id: manual`, KI-eröffnet) nach Open-Quelle zuordnen.
- P2: Lektions-Dubletten „zwei unabhängige Bestätigungen“ konsolidieren (Bestand).
