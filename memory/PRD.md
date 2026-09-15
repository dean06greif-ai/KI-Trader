# PRD – KI-Trader (externe Daytrading-Website, Render-Deploy)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (Repo dean06greif-ai/KI-Trader) soll
verbessert werden: sauber, modular, rückwärtskompatibel, Originalstruktur beibehalten
(Render-Deploy). Verbindliche Quelle: Plan-Dateien im Repo (`KI_TRADER_AUDIT.md`,
`UMSETZUNGSPLAN_LIVE_QUALITAET.md`), lebendes Protokoll `UMSETZUNG_FORTSCHRITT.md`
(nach jedem Schritt aktualisieren – Nutzer pusht via Emergent "Save to GitHub").

## Architektur
- Backend: FastAPI (`backend/server.py`, routers/, services/, core/), MongoDB (Motor)
- Frontend: React (frontend/src/components), Trading-Dashboard (Scanner, KI-Trader, Analyse)
- Extern: Bitunix (Futures), IBKR via ibeam-Gateway, OpenRouter/Groq/Mistral/Gemini (LLM),
  Telegram, Supabase. Deploy: Render (Python 3.11). Lokal: lokale Mongo, keine Exchange-Keys,
  `AI_TRADER_LOCAL_DISABLE=1`.
- Test-Suiten: `backend/tests` (`-m unit`, xdist -n 2) + Root-`/tests` (T1, pytest-fähig)

## Nutzer-Persona
Einzelner Betreiber (Admin) – handelt live/paper mit KI-Unterstützung, will Stabilität,
ehrliche Messung und nachvollziehbare Policy-Entwicklung.

## Umgesetzt (Stand 26.06.2026)
- Phase 1 Geldschutz (1.1–1.9) ✅ · Phase 2 Ehrliche Messung (2.1–2.10) ✅
- Phase 3 Champion vs. Kandidat (3.1–3.5) ✅ · Phase T Test-Hygiene (T1–T4) ✅ → **Audit-Plan komplett**
- Diese Session: Repo-Import Branch `conflict_140926_1702`, Baseline bestätigt,
  Testing-Agent Smoke iteration_62 (15/15 grün), **T2** GitHub-Action, **T3** Prod-Probe,
  **3.5 Portfolio-Backtest** (services/portfolio_backtest.py + /api/portfolio-backtest/* +
  PortfolioBacktestCard im Backtester; 10 Unit-Tests, Testing-Agent iteration_63 100% grün,
  Unit-Suite 1403 passed).

## Umgesetzt (14.09.2026 – Session conflict_140926_2137)
- VERIFIZIERT (war bereits implementiert): News-Turbo (News-Wächter 3-min-Takt im
  Event-Fenster) + Playbook-Cache (stale-while-revalidate, Endpoint nie mehr ~34s)
- NEU Event-Setups im FOMC-Muster: services/econ_event.py (generische EconEvent-Klasse,
  BLS/BEA-Kalender 2024–2026) mit cpi_event/nfp_event/ppi_event/pce_event,
  services/econ_backtest.py (re-used pure fomc_backtest-Regeln, Overfitting-Schutz),
  routers/econ.py (/api/econ/...), EconEventPanel.js (Button „Events" im KI-Trader).
  Voll integriert: Entry-Gates, Prompt-Blöcke, 5-min-Fast-Takt, News-Turbo, Playbook,
  Asset-Klassen-Ausschlüsse, Live-Opt-in per Backtest-Validierung
- Impact-Backtests (2J, echte Bitunix-5m): CPI ✅ (+56.08, OOS+48.60), PPI ✅ (+39.15,
  OOS+24.75), PCE ✅ knapp (OOS nur +5.67), NFP ❌ (−44.55, bleibt Paper-Datensammlung)
- Trail-SL-Optimierung: Optimizer-Gruppe „trail" (trail_after_tp1 + trail_atr_mult 1.0–4.0),
  OPT_TRADE_KEYS erweitert (Übernehmen-Button schreibt Trail in Live/Paper/Backtest),
  Checkbox in Optimizer.js, wirkt in allen Modi inkl. Endlos-Suche/Nacht-Serie (Hintergrund)
- Tests: test_econ_event.py + test_trail_optimizer.py (63 grün inkl. FOMC-Regression),
  Testing-Agent iteration_2 Backend 13/13 + Frontend 100%

## Offen / Backlog (priorisiert)
- P0: Nutzer-Push via "Save to GitHub"; Phase-0-Punkte beim Nutzer (JWT_SECRET auf Render,
  Key-Rotation, Bitunix-IP-Whitelist)
- P1: 2–4 Wochen MESSPHASE der Live-Ausführungsparameter (A+B-Schwellen einfrieren; Strategie-
  Suche/Backtests/Policy-Lab/Website-Arbeit weiterhin erlaubt), danach Auswertung
  slippage-stats + Policy-Report
- P2: Entscheid Option 3 (1m-Hybrid-Trigger) nach Messphase

## Detail-Protokoll
Siehe `/app/UMSETZUNG_FORTSCHRITT.md` (Quelle der Wahrheit, wird je Schritt fortgeschrieben).

## Umgesetzt 15.06.2026 – Analyseplan AP00–AP03 (Paket `analysis_paket/`)
Basis: Commit 792ff0ac (Branch conflict_150926_0200), Plan aus Branch conflict_150926_1731.
- AP00: Offline-Testbasis `backend/tests/analysis_regression/` (Fake-DB, opt. Netzsperre via KI_OFFLINE_TESTS=1), 55 Solltests.
- AP01 (T01/R03): `leftover_evidence()` – Watchdog schließt Fremdpositionen nie ohne ID-/Mengenbeleg; `transition_close_query()` scoped; Übergangsschutz nur im Apply-/Confirm-Pfad (Refresh handelswirkungsfrei).
- AP02 (T02/T03/T06): `recovered_fill_qty()` bucht echte Fillmenge; `sl_exchange_status` confirmed/missing/unknown (additiv); Risikobudget fail-closed bei unbekannter Equity + `unknown_sl_risk_pct` (2%) für offene Trades ohne SL.
- AP03 (R01/R02): `services/strategy_plan.py` – deterministischer Resolver (plan_hash), Basis-Ableitung statt kumulativer Merges (`dynamic_keys`/`dynamic_param_keys`), Sub-Strategie-Regeln transparent am Coin-Override.
- Fortschrittsdatei: `/app/PROGRESS.md` (Berichtsformat des Handoffs). Tests: 1466 unit + 54 offline + 25 API-Regression grün.

## Backlog (aus Plan, priorisiert)
- P1: AP04 (observed/desired/applied, CAS-Confirm, idempotenter Apply), AP05 (Datenmanifeste/Candlegrenzen), AP06 (Referenzsimulation Same-Bar/offene Positionen), AP07–AP09 (Holdout-Trennung, MarketContext, Policy-Provenienz)
- P1/P2: AP10 (UI-Zustände), AP11 (Worker-Vertrag), AP12 (Shadow/Paper-Abnahme)
- P2: AP13 (Bereinigung/Erweiterung)
