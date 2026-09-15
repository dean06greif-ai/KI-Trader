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

## Umgesetzt 26.06.2026 – Analyseplan AP04 (Session conflict_150926_2016)
- AP04 (R04/R09/R13/T04) vervollständigt (Grundlagen aus abgebrochener Session lagen vor):
  Release-Statusmodell draft/validated/approved/stale/legacy (`strategy_release.py` +
  `revised_release`); Router-Anbindung: Save/Builds setzen Release, Apply+Confirm mit
  409-Gate, Confirm = CAS (command_id, Ablauf, Zustandsversion; Apply-Fehler retrybar),
  NEU `POST /api/dynamic/{id}/approve`, DELETE = Archivieren mit scoped Unapply
  (Wechsel-Protokoll bleibt), `/api/dynamic/list` additiv `release_status`.
- T04: `_setup_live_gate` fail-closed bei Exceptions; `live_gate_bypass_enabled` Default AUS.
- UI (DynamicPanel.js, additiv): Release-Badges, „Freigeben"-Button, Confirm mit command_id.
- Tests: analysis_regression 74 grün (20 neu), Unit-Suite 1540, Root-Suite 169 (3 vorbestehende
  Econ-Session-Fails gefixt inkl. hartkodiertem Prod-Passwort in test_iter44!),
  Testing-Agent iteration_5: Backend 17/17 + Frontend-E2E 100% (test_ap04_api_flows.py neu).

## Umgesetzt 26.06.2026 – Analyseplan AP10/AP11/AP12-Kern (Session conflict_150926_2313)
- Repo-Import (AP05–AP09 lagen bereits umgesetzt+dokumentiert vor); Baseline bestätigt.
- AP10 (R17, UI-Zustände): `research_validation.walkforward_status` (passed/stale, rein) +
  `walkforward_stale` in /api/regime-lab/list; RegimeLab.js: 4-stufiger WF-Status inkl.
  „WF veraltet", WF-Provenienz-Zeile (label_basis kausal, attempt_no, Trades-Warnung),
  Datenversion „Daten (nicht) gepinnt" im Detail, keine positive Färbung ohne Evidenz;
  DynamicPanel.js: tatsächlicher applied_state (Badges „Übernahme fehlgeschlagen – Retry"/
  „blockiert", „Stand angewendet ✓").
- AP11 (W01, Worker-Vertrag): `payload_input_hash`/`canonical_hash`; Input-Hash am Auftrag,
  Worker (v1.11.0) echot ihn, Mismatch = verständliche Ablehnung; Evidenz
  {input_hash, result_hash, worker} additiv am Ergebnis; **idempotenter Jobabschluss**
  (Doppel-/Spät-Upload verworfen – vorher Doppel-Persistenz möglich); Paketmanifest mit
  Dateihashes/code_fingerprint/commit; rekursive Unterpaket-Allowlist
  (services/setup_backtest jetzt wirklich im ZIP, .env/Configs hart ausgeschlossen).
- AP12 (kodierbarer Kern): `strategy_release.evidence_bundle` (rein) +
  `GET /api/dynamic/{id}/evidence` (read-only) + „Beweispaket"-Panel im DynamicPanel:
  Klartext-Blocker, ready_for_live nur bei leerer Liste (PnL allein genügt nicht);
  menschliche Live-Freigabe bleibt Nutzer-Aktion.
- Tests: analysis_regression **163 grün** (12 AP10 + 15 AP11 + 12 AP12 neu), Unit-Suite
  **1629 passed** (keine Regression), Testing-Agent iteration_6: Backend 5/5 + Frontend-E2E
  4/4 (echte Mini-Analyse BTC/15m/30d, „Daten gepinnt", WF-Status). Screenshot-Verify des
  Beweispakets. `/app/memory/test_credentials.md` neu angelegt (Dev-Login).

## Backlog (aus Plan, priorisiert)
- P1 (operativ, Nutzer): AP12-Rest – Shadow-/Paper-Beobachtungszeit auf Render, danach
  menschliche Live-Freigabe (Beweispaket-Panel als Grundlage); Push via „Save to GitHub".
- P2: AP13 (gezielte Bereinigung/Erweiterung – laut Plan erst nach Pilotnachweis).
