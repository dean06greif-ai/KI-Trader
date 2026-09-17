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
