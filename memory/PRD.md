# PRD – KI-Trader (externe Daytrading-Website, Render-Deployment)

## Original-Problemstellung (16.06.2026)
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader, Branch conflict_160926_1325) verbessern. Grundsatz: sauber, modular, rückwärtskompatibel, Originalstruktur für Render-Deploy beibehalten.
1. KI-Labor Space-Quota-Problem (Atlas 512 MB, Writes blockiert)
2. KI-Modelle nicht mehr auswählbar, KI-Chat nicht schreibbar
3. WARNUNG-Punkt im Header erklären / KI-Team-Modellwechsel geht nicht
4. Event-Backtest in den Backtester verschieben (Button, Event-Auswahl, KI-Schleife wie bei Setups); alter Ort nur noch Info-Badge/Zeitplan
5. News-Wächter: Gemini nötig oder reicht Ministral/Groq bei 3-Min-Takt? (nur Analyse)

## Architektur
- Backend: FastAPI (backend/server.py, routers/, services/), MongoDB Atlas (motor), Bitunix/IBKR-Anbindung, KI-Team über Groq/Gemini/Mistral/OpenRouter
- Frontend: React (CRA/craco), Komponenten unter frontend/src/components
- Deployment: Render (Struktur unverändert gelassen)

## Umgesetzt (16.06.2026)
1. **Atlas-Quota gelöst**: `sample_mflix` (181 MB Atlas-Beispieldaten) + 2 Test-DBs gedroppt → Writes wieder frei (388/512 MB, 76 %). NEU: `db_storage`-Check in services/safety_status.py (warn ≥85 %, critical ≥97 %, Env `ATLAS_QUOTA_MB`, Default 512) – sichtbar in der Header-Ampel BEVOR Atlas blockt.
2. **Globaler Mongo-Fehlerhandler** in server.py: Quota-Fehler (Code 8000/"space quota") → HTTP 507 mit klarer deutscher Meldung; UI-Toasts zeigen jetzt `detail` (AITradingPanel: saveRole/resetRole/Chat).
3. **Modellwechsel funktioniert wieder** (war Folge des Write-Blocks) – verifiziert via POST /api/ai/roles.
4. **Header-Sicherheitsampel klickbar** (Header.js SafetyLight): zeigt Detail-Checks auch mobil (data-testid safety-status-details).
5. **Event-Backtests in Backtester verschoben**:
   - Neuer Tab „Event-Setups" (bt-tab-events) mit EventBacktestPanel.js: Event-Auswahl (FOMC/CPI/NFP/PPI/PCE), Zeitraum, KI-Schleife (Revision durch Forschungs-Analyst innerhalb PARAM_BOUNDS + Re-Test), Live-Opt-in, Reset auf Basis-Regeln.
   - Backend: services/event_backtest_loop.py (Batch-Job, KI-Revision, Params-Persistenz je Event in db.settings `{key}_event_params_ai`), routers/event_setups.py (/api/event-setups/overview|backtest|{key}/live|{key}/reset-params). fomc_backtest/econ_backtest um optionalen `params`-Override erweitert. Alte /api/fomc/* und /api/econ/*-Endpunkte unverändert (rückwärtskompatibel).
   - AITradingPanel „Events"-Sektion: nur noch EventScheduleBadges.js (Zeitplan/Phase/validiert/live, Info-only).
   - Overfitting-Schutz erhalten: Validierung verlangt weiterhin positives Gesamt- UND OOS-PnL.
6. **Tests**: backend/tests/test_event_backtest_loop.py (15 Unit-Tests, grün) + E2E backend/tests/test_iter9_event_setups_e2e.py (11/11 grün, Testing-Agent). CPI-Backtest validiert (38T, WR 63 %).

## News-Wächter-Analyse (Empfehlung, noch NICHT umgestellt – User entscheidet)
- Ist-Zustand: Primär Groq `openai/gpt-oss-20b`, Fallback1 Gemini `gemini-3.1-flash-lite`, Fallback2 Mistral `ministral-8b-latest`. Im Event-Fenster automatisch 3-Min-Takt (FOMC_INTERVAL_MIN=3, gilt auch CPI/NFP/PPI/PCE).
- Empfehlung: Kette so lassen. Gemini ist NICHT primär nötig – nur Ausfall-Fallback (separate Quota). Groq ist schnellstes/günstigstes Modell für die simple JSON-Relevanzbewertung; Ministral-8b reicht fachlich auch, ist aber langsamer als Groq. Umstellbar jederzeit im KI-Team-Panel (funktioniert wieder).

## Backlog / Nächste Schritte
- P1: Kosmetische React-Warnung `<span>` in `<option>` (pre-existing, Backtester-Selects)
- P1: Automatik-Schedule für Event-Backtests (wie seed_auto beim Setup-Backtest)
- P2: db_storage-Check: automatische Bereinigung alter Backtests/Logs bei warn-Level
- P2: ATLAS_QUOTA_MB in Render-Env setzen, falls Cluster-Upgrade erfolgt (z. B. 5120 bei Flex)
