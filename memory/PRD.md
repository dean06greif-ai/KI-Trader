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

## Tests
- iteration_11.json: Backend 7/7 (Panel-Isolation, DELETE?panel, Overviews), Frontend alle Checks grün.
- Bestands-Tests des Users unter `backend/tests/` (pytest -n 2). Neuer Test: `test_iter11_copilot_panels.py`.

## Backlog / Nächstes
- P1: Cancel-Endpoint für Event-Backtest-Job (aktuell läuft er bis zum Abschluss).
- P2: `_chat_jobs` in routers/copilot.py mit bounded LRU statt nur TTL.
- P2: AITraderSeeding.js ggf. aufteilen (Review-Hinweis, >450 Zeilen).
- P2: Legacy-Copilot-Nachrichten (ohne panel) optional migrieren statt Clean-Cut.
