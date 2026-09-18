# PRD – KI-Trader (externe Daytrading-Website, Render-Deployment)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (Repo: dean06greif-ai/KI-Trader, Branch conflict_160926_1839).
Grundsatz: Stabilität, Rückwärtskompatibilität, saubere modulare Integration; Originalstruktur beibehalten
(externes Render-Deployment). Verbesserungen sollen in die bestehende Architektur eingepflegt werden.

## Architektur (Bestand, unverändert)
- Backend: FastAPI (`backend/server.py` + `routers/` + `services/`), MongoDB Atlas (MONGO_URL), Supabase-Spiegel,
  Bitunix-Marktdaten, OpenRouter/Groq/Mistral-LLM-Stack, Telegram.
- Frontend: React (CRA/craco), Komponenten unter `frontend/src/components/`.
- Deployment: Render (Ordnerstruktur unangetastet lassen!). Lokal: supervisor (backend 8001, frontend 3000).

## Nutzer-Personas
- Admin/Trader (Dean): einziger Nutzer, Admin-Login (siehe /app/memory/test_credentials.md).

## Umgesetzt (16.06.2026 – nach User-Wahl: dauerhafte Verläufe je Reiter, Spezial-Prompt + Kontext der anderen Reiter, Ampel auch mobil neben Titel)
1. Strategie-Copilot: eigener, dauerhaft gespeicherter Verlauf PRO REITER (optimizer/backtester/regime_lab/builder).
   - `services/strategy_copilot.py`: PANELS/PANEL_PROMPTS/PANEL_SHARED, `history(panel)`, `clear_history(panel)`,
     `_store(panel)`, `_other_panels_digest()` (Kurzfassung der anderen Reiter im Prompt), Chat nutzt panel-System-Prompt.
   - `routers/copilot.py`: GET/DELETE `/api/copilot/history?panel=` (ohne panel abwärtskompatibel: alle).
   - `StrategyCopilot.js`: lädt/löscht Verlauf je panel-Prop.
   - Alt-Nachrichten ohne panel-Feld erscheinen nur noch in der ungefilterten API-Ansicht (bewusster Clean-Cut).
2. Sicherheits-Ampel (SafetyLight) im Header: jetzt direkt NEBEN der Überschrift „CRYPTO SCANNER" (gleiche Zeile,
   Desktop + Mobil). `Header.js` (`.header-title-row`), `Header.css`.
3. Backtester „KI Trader · Setups": Event-Setups (FOMC/CPI/NFP/PPI/PCE) kein separater Prozess mehr in der UI:
   - Gemeinsamer Fortschrittsbalken (Setup-Job + Event-Job kombiniert), Events als Zeilen (Klasse „Events") in der
     gemeinsamen Ergebnis-Tabelle inkl. Live-Toggle + KI-Param-Reset.
   - `EventSetupSeeding.js`: nur noch Chips + onState-Callback/ref (toggleLive/resetParams/start).
   - `AITraderSeeding.js`: kombinierter Fortschritt + Event-Zeilen. Backend-APIs UNVERÄNDERT (event_backtest_loop,
     setup_backtest) – reine UI-Zusammenführung, kein Risiko für Render-Jobs.

## Fakten Event-Backtests (Frage des Users beantwortet, Stand im Code)
- Regeln: A) Whipsaw-Fade (Spike über Pre-Range der zurück schließt → Gegenposition), B) Drift (nachhaltiger
  Schluss jenseits der Pre-Range → Trendrichtung). Feste Basis-Regeln, KI-Revision nur in PARAM_BOUNDS.
- Daten: ~2 Jahre Event-Historie, 5m-Kerzen von Bitunix rund um den Event-Zeitpunkt, Symbole BTCUSDT/ETHUSDT/SOLUSDT
  (nur Krypto – Bitunix liefert keine Aktien-/Index-Historie). Validierung: Gesamt- UND OOS-PnL positiv.

## Umgesetzt (17.09.2026 – Edge-Register für den Setup-Backtester, Plan ARBEITSSTAND_E1 abgeschlossen + gelöscht)
Befund aus dem Prod-Stand (read-only Probe): bestätigte Edges (z. B. crypto/session_open KI-Rev.17, OOS 222T/60 %/+23)
wurden durch spätere Fehlläufe (verkürztes Datenfenster) auf `exhausted` überschrieben, `tuned` + OOS-Trades gelöscht;
schmale 12-Trade-Sätze ersetzten breit bestätigte. Umsetzung (modular, rückwärtskompatibel, Details in
`BACKTEST_SEEDING_PLAN.md` → Phase 1c):
- `backend/services/setup_backtest/edges.py` (neu): Collection `setup_backtest_edges`, genau ein aktiver Edge je
  Klasse × Setup; reine Funktionen `overfit_flags`, `robust_score`, `better`, `decide`; `activate` (Rollback inkl.
  OOS-Trades fürs Reife-Gate), `recover_from_history` (rückwirkend aus dem State-Verlauf, idempotent).
- `runner._evaluate_setup`: aktiver Edge wird zuerst geprüft; Schleifen-Modi prüfen trotzdem alle Varianten + bis zu
  2 gespeicherte Herausforderer; Fehllauf → `stale` 1..3 (Edge bleibt, Trades bleiben), erst danach Status `stale`;
  Ersetzen nur per `better()` (≥ +10 % Robustheit, ≥ 70 % OOS-Trades, keine harten Overfitting-Signale).
  `_optimize_setup` mit Overfitting-Bremse; `reset` deaktiviert Edges nur.
- Boot-Migration `setup_backtest_edges_v1` (einmalig, auf Render automatisch): importiert Verlauf, reaktiviert
  robustesten Edge für Setups ohne Edge (Prod-Probe: 20 importiert, 6 reaktiviert).
- API: `GET /api/ai/playbook/backtest/edges`, `POST …/edges/activate`, `POST …/edges/recover` (Admin).
- UI: `frontend/src/components/EdgeRegister.js` im Reiter „KI Trader · Setups“ + Edge-Entscheidung je Ergebniszeile.
- Tests: `backend/tests/test_setup_backtest_edges.py` (10, unit), `backend/tests/test_edges_api.py` (11, live);
  stale Tests angepasst (`test_fomc_event` Event-Setups in allen Klassen, `test_iter38…` Setup-Anzahl dynamisch).
  Unit-Suite: 1747 passed; verbleibende 5 Fehler in `test_iter38…` brauchen Bitunix-Keys (lokal bewusst nicht gesetzt).

## Tests
- iteration_2.json (17.09.2026): Backend 11/11, Frontend-Flows (Register, Verlauf, Admin-Login, Rollback) grün.
- iteration_11.json: Backend 7/7 (Panel-Isolation, DELETE?panel, Overviews), Frontend alle Checks grün.
- Bestands-Tests des Users unter `backend/tests/` (pytest -n 2). Neuer Test: `test_iter11_copilot_panels.py`.

## Backlog / Nächstes
- P1: Edge-Register – Herausforderer-Anzahl/Margen ggf. per Auto-Config einstellbar machen (aktuell Konstanten).
- P2: Optimierer-Modus zusätzlich gegen die gespeicherten Kandidaten laufen lassen (heute nur Basis-Satz).
- P1: Cancel-Endpoint für Event-Backtest-Job (aktuell läuft er bis zum Abschluss).
- P2: `_chat_jobs` in routers/copilot.py mit bounded LRU statt nur TTL.
- P2: AITraderSeeding.js ggf. aufteilen (Review-Hinweis, >450 Zeilen).
- P2: Legacy-Copilot-Nachrichten (ohne panel) optional migrieren statt Clean-Cut.

## Umgesetzt (17.09.2026 – Branch conflict_170926_1506: Policy-Versionen verständlich, Event-Setup-Tabelle stabil, Analyse Lektionen/Regime-Lab)
Lokale Umgebung: `backend/.env` -> lokale Mongo `crypto_scanner_dev` (Prod-Subset read-only kopiert: auto_trades ai_trader,
ai_decisions, settings), `AI_TRADER_LOCAL_DISABLE=1`; KEINE Bitunix-/IBKR-/Telegram-Keys lokal (kein Doppel-Betrieb zu Render).
1. Policy-Versionen (`PolicyReportCard.js`, CSS in `PerformanceAnalytics.css`): Kopf „Policy-Versionen – Welcher Stand des
   KI-Traders hat netto Geld verdient?", dezentes ⓘ-Icon klappt Erklärtext aus, Karte standardmäßig eingeklappt.
   Versionsnamen statt Hash: „Version 12 · aktuell · 17.09.–17.09. · Modell · ML-Gate v77 · geändert: ML-Gate".
   Backend additiv: `setup_variant.annotate_policy_versions/policy_changes` -> Felder `version_no`, `is_current`, `changed`
   (Test `backend/tests/test_policy_report_versions.py`). Bestehende Felder/Sortierung unverändert.
2. Backtester „KI Trader": Playbook-Zeilen werden beim Start/Abbruch/Fehler eines Laufs NICHT mehr geleert
   (`AITraderSeeding.js`: kein `setResult(null)`, `refreshInfo` stellt `last_result` wieder her, Hinweis „letzter Stand
   (neuer Lauf aktiv)") -> Event-Setups rutschen nicht mehr nach oben.
3. `ANALYSE_LEKTIONEN_UND_REGIME_LAB.md`: Antworten (Policy-Versionen, Lektionen-Validierung + Vorschlag „Lektions-Bilanz"
   A/B/C, Regime-Lab ↔ KI-Trader inkl. Einstellungsempfehlungen). Befund: 14 Policy-Versionen in 4 Tagen (ML-Gate/Playbook
   drehen Fingerprint zu oft) -> Empfehlung Fingerprint-Granularität.

## Backlog / Nächste Schritte
- P1: Lektions-Bilanz Baustein A (applied_lessons in ai_decisions/auto_trades) -> B (HOLD-Gegenprobe) -> C (Impact-API + UI).
- P1: Fingerprint-Granularität (ML-Gate-Retrain/Playbook-Statistik ohne neue Policy-Version) oder Grob-Gruppierung im Bericht.
- P2: regime_gate optional auf freigegebene Lab-Analyse + `regime_artifact` im Fingerprint befüllen.

## Pläne verfasst (17.09.2026, noch NICHT umgesetzt)
- `PLAN_LEKTIONS_BILANZ.md`: Attribution (applied_lessons/would_be) -> HOLD-Counterfactual (`lesson_counterfactual.py`, Coll. `ai_lesson_cf`)
  -> Impact (`lesson_impact.py`, `GET /api/ai/lessons/impact`, Prompt-Block, UI-Badges). Flags, Tests, Rollout je Phase. ~3,5 Tage.
- `PLAN_REGIME_BRUECKE_LAB_KI_TRADER.md`: Lab-Freigabe (`/approve`) -> `structural_regime.py` Resolver -> Prompt-Block „Struktur"
  -> `regime_artifact` im Fingerprint -> Gate-Quelle `lab` optional. Flags default aus. ~3,5 Tage.
- Empfohlene Reihenfolge: Lektions-Bilanz A (Datensammlung) zuerst, dann Regime-Brücke 1–4, dann Lektions-Bilanz B/C.
- 17.09. Rev. 2 Regime-Plan: Freigabe zweistufig (shadow -> active) und nachweisgebunden (Kalibrierung + Ablation + Mindestabschnitte,
  Evidence-Hash, history), kein TTL-Feld (Frische-Regel im Resolver), KI-Trader kann Umschaltung vorschlagen (suggest) oder mit
  24 h Karenz selbst vollziehen (auto) über bestehenden Proposal-Mechanismus; Trader kann jederzeit manuell schalten/widerrufen.

## Umgesetzt (17.09.2026 – Branch `dein-branch-name3`: PLAN_REGIME_BRUECKE komplett, PLAN_LEKTIONS_BILANZ verifiziert)
- Logbuch: `FORTSCHRITT_UMSETZUNG.md` (Repo-Root) – Stand je Baustein, Live-Checks, Regressionsläufe.
- PLAN_LEKTIONS_BILANZ (A/B/C): war bereits im Branch fertig; Tests grün, Endpunkt + UI vorhanden → bestätigt.
- PLAN_REGIME_BRUECKE B1–B4: Backend war vorhanden (`services/regime_release.py`, `services/structural_regime.py`,
  Endpunkte in `routers/regime_lab.py`, Gate-Quelle in `regime_gate.py`, Prompt/Snapshot/Fingerprint in `ai_engine.py`),
  aber ohne Frontend. Ergänzt:
  - Backend: Scope `both` → `combined` bei Freigabe; Trader-„Übernehmen“ eines `regime_release`-Vorschlags prüft das
    Stichproben-Gate erneut (400 statt Umgehung); `regime_gate_source="own"` als Default in `bitunix_trade.DEFAULT`;
    `/api/autotrade/regime_phase/{sym}` liefert `lab{stage,phase,state}`; `scripts/live_check_regime_bridge.py` (lesend).
  - Frontend NEU `components/RegimeRelease.js/.css`: Badge + Knöpfe Shadow/Wirksam/Widerruf + History (RegimeLab),
    `StructuralStagePanel` + Select `structural_regime_autonomy` im KI-Trader-Setup, lesbare Vorschlagskarte,
    Regime-Quelle-Select im Auto-Trade-Modal, Rewards-Tabelle nach Struktur-Regime, Policy-Untertitel „Lab-Modell …“.
  - Tests: `tests/test_regime_release.py` (+2), Testing-Agent Iteration 4 (Backend 10/10, Frontend alle Kriterien).
- Umgebung Preview: `AI_TRADER_LOCAL_DISABLE=1` gegen Produktiv-Atlas; `memory/test_credentials.md` angelegt.

## Backlog / Nächste Schritte (Stand 17.09.2026)
- Rollout Regime-Brücke Phase 1–2 (Trader): 1h-Krypto-Analyse anlegen, Regime „behalten“, Kalibrierung + Ablation
  laufen lassen → Knopf „Beobachten (Shadow)“ grün → Shadow aktivieren; ≥ 30 Trades sammeln.
- Optional: Marktphasen-Filter-Block im Auto-Trade-Modal auch für paper/off anzeigen (heute nur LIVE, Bestand).
- B5 Strukturelle Lektionen erst nach 4 Wochen Shadow-Daten.

## Umgesetzt (18.09.2026 – Backup, Entry-Modus, Historie, Panel-Bugs; Branch conflict_170926_2220)
- **Backup nach Supabase Storage**: `services/supabase_storage.py` (gemeinsamer REST-Client), `services/backup.py`
  (täglicher Dump kritischer Collections als json_util+gzip in Bucket `mongo-backups`, 30 Tage Aufbewahrung,
  State `settings.backup_state`, Config `settings.backup_config`; Restore = Trockenlauf/`apply` per `_id`-Upsert,
  löscht nie). API `GET/POST /api/maintenance/backup[/list|/run|/config|/restore]` (Admin für Schreibzugriffe).
  CLI `backend/scripts/backup_restore.py list|backup|download|restore [--apply]`. UI: KI-Labor → Speicher →
  `BackupCard.js`. Loop-Start in `server.py` (15 min nach Boot, dann alle 24 h).
- **Retention verschärft** (`services/retention.py`): ai_decisions 14 d, ai_chat_archive 30 d, Backtest-/Optimizer-
  Rohdaten 21 d, Ghost-Trades 60 d, local_jobs 7 d; neu gedeckelt: job_series, ai_token_usage, copilot_chat,
  pending_entry_orders. Untergrenzen unverändert.
- **Entry-Modus** (`services/entry_policy.py`, Setting `entry_mode`: ai | aggressive | conservative, Default ai =
  bisheriges Verhalten): Setup→Entry-Stil-Tabelle (level/confirm/either), Prompt-Zusatz je Modus, Nachkontrolle
  in `ai_engine` (konservativ: Limit→Market). UI: KI-Setup → „Entry-Modus (Limit/Market)“ (`ai-entry-mode-select`).
  Befund: Engine konnte beides bereits (Key-Level-Limit + Sweep-Trigger→Market); es fehlte der Setup-Bezug.
  Maker-Order-Modus (Post-Only, Fee) ist unabhängig davon.
- **Kerzen-Historie Backtester**: statische Deckel 110/150/180 in `core/instruments.py` → `BITUNIX_HIST_CAP_DAYS=365`
  (Bitunix liefert ab Listing, Stand 09/2026 XAU ~217 d, XAG ~249 d, CL/QQQ/SPY ~176-178 d, wächst täglich).
  `candle_cache`: erschöpfte Quelle 24 h merken (`_HEAD_EXHAUSTED`), Archiv-Rückfall; `runner.history_note` nennt
  die tatsächlich geladenen Tage. `services/candle_archive.py`: dauerhaftes `<symbol>.npy.gz` in Bucket
  `candle-archive` (Download bei Cache-Miss, gedrosselter Upload bei persist) – Render-Disk ist flüchtig.
  Kein freier Anbieter hat 365 d 1m/5m für diese Instrumente (Yahoo 1m 30 d / 5m 60 d / 1h 730 d).
- **Frontend**: `.ai-panel-header`/`.ai-status-row` `flex: 0 0 auto` (Schrumpfen bei Setup/Analyse behoben),
  `.ai-analysis-section` scrollt selbst, `PolicyReportCard` aus `PerformanceAnalytics` in den Analyse-Tab verschoben.
- Tests: `backend/tests/test_backup_entry_mode_history.py` (16 Unit), Testing-Agent Iteration 5 (Backend 13/13,
  Frontend alle Kriterien; `tests/test_iter5_backup_entry_mode_api.py`).

## Backlog (Stand 18.09.2026)
- P1 Bulk-Daten (Regime-Charts, Backtest-Rohdaten) aus Mongo nach Supabase (Audit 03) – Storage-Client liegt bereit.
- P1 Kerzen-Archiv auch aus dem lokalen Worker befüllen (Upload) und Render-Env `CANDLE_ARCHIVE=0` zum Abschalten.
- P2 Paper-Fill der KI-Limit-Orders auf Kerzen-Low/High statt 12-s-Tick-Preis (Paper/Live-Abweichung).
- P2 Atlas M10 erst, wenn Retention + Auslagerung nicht mehr reichen.

## Umgesetzt (18.09.2026 – Merge Basis `conflict_180926_1209` + Regime-Lab-Patch aus `conflict_180926_1455`)
- Analyse: `_1455` war nur ein leeres Template + `kitrader-changes/regime-lab-clarity.patch` (+ Kopien der Dateien).
  Der Patch passte konfliktfrei (`git apply --check`) auf den Basis-Branch → 1:1 eingepflegt, Basis hat Vorrang.
- Eingepflegt (Regime-Lab-Klarheit, Doku `REGIME_LAB_KLARHEIT_UND_KALIBRIERUNG.md`):
  `services/regime_truth.py` (detektor-spezifischer Kalibrier-Suchraum, `calibration_grid`, `config_changes`,
  Bericht mit `detector/tuned_keys/changes/improved`), NEU `services/regime_quality.py` (Erkennungs-Qualität je
  Anlageklasse), `routers/regime_lab.py` (Status-Fallback für Kalibrierung/Forschungs-Läufe nach Neustart,
  `GET /api/regime-lab/calibrations`, `quality` in `GET /{aid}`), Frontend NEU `RegimeCalibrationResult.js`,
  `RegimeCalibrationHistory.js`, `RegimeQualityCard.js`; `RegimeEngineSettings.js`, `RegimeLab.js/.css` erweitert.
  Tests NEU `backend/tests/test_regime_lab_clarity.py` (25 unit, grün).
- Zusätzlicher Fix (Befund im Prod-Atlas): `GET /api/regime-lab/list` lud `chart_emas` (~1,7 MB je Analyse) mit →
  >60 s. Projektion um `chart_emas` ergänzt (→ ~1 s); Alt-Analysen-Bereinigung in `persist_analysis` lädt nur `id`.
- Umgebung Preview: `backend/.env` = Render-Env des Users + `AI_TRADER_LOCAL_DISABLE=1` (kein Doppelbetrieb der
  KI-Engine gegen Render/Atlas). Backend-Start dauert gegen Atlas ~70–100 s.
- Tests: Testing-Agent Iteration 6 (Backend 14/14 lesend + 25 unit, Frontend alle Flows grün, `tests/test_regime_lab_regression.py`).
- Bekannter Alt-Drift in der großen Unit-Suite (nicht durch den Merge verursacht, 1812 passed): `test_instruments_universe`
  (IBKR-Routing/365-Tage-Deckel), `test_adaptive_modules` (Retention 30 statt 45 d), `test_playbook_backups_notify`
  (echte MISTRAL_BACKUP-Keys in .env), `test_iter38_*` (braucht Mock-Server :8055).

## Umgesetzt (18.09.2026 – PLAN_REGIME_COCKPIT abgeschlossen; Branch conflict_180926_1723)
- Ausgangslage: Backend-Bausteine B0/B1/B2/B4 sowie die UI-Bausteine (Orphan-Badge, RegimeValidation-Text,
  `RegimeCockpit.js` + Einbau in `AITradingPanel` Analyse-Sektion, Schalter `ai-regime-context-select`) waren im Branch
  bereits vorhanden, im Plan aber nicht abgehakt. Häkchen nachgezogen, Fortschritts-Log ergänzt.
- Neu: `frontend/src/components/RegimeCockpitOverview.js` – einklappbare Karte „Übersicht alle Symbole“ unter dem
  Detail-Cockpit (`GET /api/regime-cockpit/overview`, Klick auf Zeile wählt das Symbol; Styles in `RegimeCockpit.css`).
- Abnahme (B5): 20 unit `test_regime_cockpit.py` grün; Live-Check lesend gegen Prod-Atlas (`scripts/live_check_regime_context.py`
  mit PROD_MONGO_URL): Prompt-Block gefüllt (BTC 51 %/998 Punkte, ETH 48 % Vorwärts-Trefferquote; `structural_regime_history`
  in Prod = 0, erwartet ohne Lab-Freigabe). Testing-Agent Iteration 7: Backend 10/10, Frontend alle Flows grün
  (`backend/tests/test_regime_cockpit_api.py` als Live-Regressionstest übernommen).
- Alt-Drift-Tests `test_adaptive_modules` (Retention 30/14 statt 45/21 d) auf DEFAULT_POLICY-Referenz umgestellt (17/17 grün).
- Umgebung Preview: lokale Mongo (`MONGO_URL`), Prod-Atlas nur als `PROD_MONGO_URL` (lesend), `AI_TRADER_LOCAL_DISABLE=1`,
  Bitunix-/Telegram-/IBKR-Gateway-Keys bewusst NICHT gesetzt (kein Doppelbetrieb gegen Live-Börse). Für die UI-Prüfung
  wurden echte `ai_market_snapshots` (BTC/ETH, 15 d) und geschlossene KI-Trades lesend aus Prod in die lokale DB kopiert.

## Backlog (Stand 18.09.2026, nach PLAN_REGIME_COCKPIT)
- P0 Trader-Aktion: erste Lab-Freigabe (Shadow) – erst dann füllt sich die Struktur-Ebene im Cockpit und im Prompt.
- P1 Lokaler Worker: Paket neu herunterladen (enthält Kopie von `services/`), sonst alte Kalibrierung.
- P2 Vorwärts-Trefferquote als ML-Gate-Feature (Plan-Option).
- P2 Restliche Alt-Drift-Tests (`test_instruments_universe`, `test_playbook_backups_notify`, `test_iter38_*`) angleichen.
- P2 Admin-Only-Polling vor Login gaten (3 harmlose 401 in der Konsole).
