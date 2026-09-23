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

## Umgesetzt (23.09.2026) – Regime-Lab Prüfung (Branch-Basis `conflict_230926_0659`)
Vollständiger Bericht: `/app/REGIME_LAB_PRUEFBERICHT_2309.md`.
1. **P0 Brücke Lab→KI-Trader**: Struktur-Regime lief auf 30 Tagen Historie → bis 55 % andere Regime-IDs als im Lab
   (echte BTC-Daten). Fix: `regime_engine.required_history_*`, `structural_regime` lädt Detektor-Warmup, `stale` bei zu
   wenig Kerzen, Health-Check `structural_short_history`. Nachweis: 0 % Abweichung (ema), 3 % (reactive).
2. **P1 Qualitätsmetrik**: „Live=Final“ ist Selbst-Übereinstimmung (EMA ~98 %), Referenz-Treffer real 51–70 %.
   Neu `services/regime_reference.py`; `reference` je Symbol in neuen Analysen; Note durch Referenz gedeckelt;
   EMA-Vergleich wählt nach Referenz (Kette). UI: `RegimeQualityCard`, `RegimeDetectorTools`, `RegimeBridgeHealth`.
3. Tests: `backend/tests/test_regime_pruefung_2309.py` (13, unit) + `test_regime_readonly_iter17.py` (lesend, live).
4. Preview läuft mit `AI_TRADER_LOCAL_DISABLE=1` gegen Produktiv-Atlas (keine Trades aus der Preview).

## Backlog (priorisiert)
- P1: Kombi-Kalibrierung + `_profile_quality` auf Referenz umstellen (Baustein `regime_reference`).
- P1: Neue 1h-Analyse mit EMA-Vergleich 5/9/14/21 nach Referenz; erst dann Shadow-Freigabe.
- P2: Brücke optional bis `bounds.start_ts` ankern (reactive 0 %); `regime_gate` Quelle `own` als Legacy kennzeichnen;
  `per_coin` bei Engine v2 als redundant ausblenden; reaktiver Warmup (EMA-Anker/Vola) in den Lab-Labels maskieren.
