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
