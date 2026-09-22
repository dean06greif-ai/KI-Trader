# PRD – KI-Trader / Crypto Scanner (externes Repo, Render-Deploy)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader, Branch `conflict_220926_1600`).
Verbesserungen sauber, modular, rückwärtskompatibel; Originalstruktur für Render-Deploy beibehalten.
Produktions-DB: MongoDB Atlas (crypto_scanner), echte Keys (Bitunix, IBKR, LLM-Provider).

## Architektur
- Backend: FastAPI (`backend/server.py`, core/, routers/, services/, strategies/), MongoDB Atlas, Supabase-Spiegel
- Frontend: React (`frontend/src`), TradingView-Charts, Admin-Login (Admin / Dean06Greif!/Admin)
- Local Worker (`local_worker/worker.py`) für Regime-Lab-Jobs auf dem PC des Users
- Deploy: Render (Frontend + Backend getrennt)

## Umgesetzt (22.06.2026)
1. **Regime-Lab Endlos-Autopilot-Balken** = Bestwert-Score statt fix 95 %
   - `core/utils.py::_job_public`: Read-time-Override (progress = best.score, keine ETA) – wirkt auch bei veralteten lokalen Workern
   - `RegimeJobProgress.js`: 100 %-Text („läuft weiter, sucht robustere/bessere Edges"), params im Poll
   - Regressionstests in `backend/tests/test_job_control_pause.py`
2. **Timeframe-spezifische Kalibrierungs-Übernahme**
   - `frontend/src/lib/regimeCalibration.js::calibrationDecision`: anderes Zeitfenster => `other_timeframe` => übernehmen (wie Erst-Kalibrierung); Altbestand ohne Timeframe unverändert
   - `RegimeAutopilot.js`: Banner-/REASON-Texte angepasst; Tests in `regimeLabHelpers.test.js`
3. **Sammlung-Trades-Transparenz** (data_collection=true, Gründe live_blocked/below_live_conf/guard_shadow)
   - `ai_chat_commands.py` + `ai_engine_context.py`: SAMMLUNG-Markierung + Hinweis im KI-Kontext (KI meldet sie nicht mehr als normale Paper-Trades)
   - `PerformanceAnalytics.js`: klickbarer „+N in Sammlung"-Hinweis neben OFFENE TRADES

## Getestet
- Testing-Agent iteration_16: 100 % Backend/Frontend, strikt read-only gegen Produktion
- Unit: test_job_control_pause.py (10), test_ai_chat_commands_and_fixes.py (12), regimeLabHelpers.test.js (24)

## Wichtige Regeln fürs Testen
- NUR read-only gegen Produktions-DB; niemals Trades öffnen/schließen oder Jobs starten

## Backlog / Ideen (nicht beauftragt)
- P1: Aktive Kalibrierung pro Timeframe getrennt speichern (Slots statt Einzel-Slot)
- P2: Sammlung-Trades optional im Chart anders markieren (eigene Marker-Farbe)
- P2: Live-Gate-Status („warum live_blocked?") je Setup direkt im Signal-Panel anzeigen
