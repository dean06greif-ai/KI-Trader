# PRD – KI-Trader Verbesserungen (Juni 2026)

## Original problem statement
Bestehende, produktiv laufende Daytrading-Website (GitHub dean06greif-ai/KI-Trader, Branch conflict_101026_1830, Render-Deploy, externer lokaler Worker) verbessern – modular, rückwärtskompatibel, Originalstruktur behalten:
1. Laptop stürzt bei Strategie-Parameter-Optimierung mit hoher Iterationszahl auf dem lokalen Worker ab (Regime-Lab-Endlossuche nicht) – Ursache finden/fixen, Worker sonst unverändert lassen. Fehler „No module named dotenv“.
2. Dynamische Strategien: Empfehlung + Warnung „nicht geeignet“ für Regime-Erkennungen, erweiterte Coin-Empfehlung, Ausreißer-/„kein Edge“-Coins während Regime×Strategie-Tests, fehlende Ergebnisdarstellung, Endlossuche „alle ausgereizt → nur jede 4. Runde“, Timeframe-Variation, beste (bestehende) Strategie je Phase finden + Parameter anpassen, Overfitting/wenig Trades.
User-Entscheidungen: Timeframes „was Sinn macht“; „Vergleichen und Parameter anpassen“; „Mindestanzahl Trades + Out-of-Sample-Test“.

## Architecture
FastAPI backend (backend/routers, services), React (CRA/craco) frontend, MongoDB, local worker (local_worker/worker.py, lädt backend/services als ZIP). Repo liegt jetzt 1:1 in /app (gleiche Struktur wie GitHub/Render).

## Implemented (12.06.2026)
- RAM-Deckel: services/array_lru.py (FastSeries-Cache), indicator_cache RAM+Disk-Deckel, optimizer Ergebnis-/Bayes-Historie gedeckelt
- dotenv optional (core/config.py, ai_engine), local_worker/requirements.txt + python-dotenv
- services/dynamic_advisor.py: Eignung Regime-Erkennung, Timeframe-Empfehlung, Coin-Edge-Historie; GET /api/dynamic-workbench/analysis-advice
- regime_opt: coin_report je Kandidat, trade_sufficient/overfit, tf_options (Multi-TF-Filter)
- dynamic_workbench: Variations-Runden statt Leerlauf (MAX_IDLE_CYCLES=3), Such-Varianten, Regel-Tiefe 2–4, Ranking mit Overfitting-Schutz, Persistenz dynamic_workbench_runs (+ /history, /run/{id}, /active-Fallback)
- dynamic_matcher: neuer kind "match" (Beste Strategie je Phase: Vergleich → Anpassung der 2 Finalisten); nur Verlierer → keine neue Strategie
- Router: Validierung regime_ids/regime_modes (400), unbekannte Regime 400, None-Werte nicht gespeichert
- Frontend: DynamicAnalysisAdvice, DynamicWorkbenchRegimeTable, DynamicWorkbenchHistory, DynamicStrategyCandidates, Avoid-Liste im Asset-Vorschlag, Reiter „Beste Strategie je Phase“, Multi-TF-Schalter, Fix "00"-Text
- Tests: backend/tests/test_workbench_dynamic_1206.py (+ test_array_lru_budget_iter.py, test_dwb_retest_iter2.py); 2389 Unit-Tests grün, 5 vorbestehende Fehler unverändert

## Implemented (12.06.2026, Teil 2)
- Werkbank-Ergebnis im Umfang der Optimizer-Top-Ergebnisse: services/dynamic_scorecard.py, DynamicScorecard.js
- Such-Verlauf, Top-5 je Regime + apply-candidate Endpoint (neue Version)

## Implemented (12.06.2026, Teil 3)
- Endlos-Suche (deep_explore): TP/SL-Suche, Rettungs-Verfeinerung, Konsistenz (Test besser = ok), adaptive Kombi-Größe, Wiederholung statt Abbruch, Diagnose; tests/test_deep_explore_1206.py

## Backlog
- P1: Strategie-Timeframe je Phase (erfordert Live-Engine-Änderung)
- P1: Worker-Paket-Version im Dashboard als „Update empfohlen“ markieren
- P2: Equity-Kurve je Regime im Werkbank-Ergebnis


---
## Session 03.11.2026 – Strategie-Namen umbrechen + KI-Trader Deep-Analyse
Aufgabe: Namen im Verlauf umbrechen statt „…“; KI-Trader (stark negativ) analysieren, Rettungsplan, erste Verbesserungen.
User-Entscheidungen: Prod-DB nur lesend · Analyse + Plan + erste Verbesserungen · Live/Paper je Einstellung.

Umgesetzt:
- Analyse 1676 KI-Trades: netto −0,23 R, brutto −0,06 R, Gebühren 0,17 R → `KI_TRADER_RETTUNGSPLAN_0311.md`.
- Read-only Skripte `scripts/prod_ki_trader_deep_probe.py`, `scripts/prod_ki_trader_filter_sim.py`.
- Edge-Filter `services/edge_filter.py` + `AutoTradeManager._edge_filter_step` (blockt nur Live; Paper/Sammel nur markiert; Schattentrades), Keys `edge_filter_*`, UI `EdgeFilterSettings.js`.
- Edge-Bericht: Gruppen Edge-Filter / Gebühren je Risiko / Tagestrend / Anlageklasse.
- CSS `.tdc-strat-name`, `.strat-perf-name` umbrechen.
- Tests `tests/test_edge_filter.py`, `test_edge_filter_pipeline.py`, `test_edge_filter_api.py`.

Backlog:
- P0 (User-Einstellungen): Live-Bypass AUS, Fee-Faktor 8, ATR-Faktor 4; 3–4 Wochen Paper-Beweis (≥ 60 Trades „besteht“, ≥ +0,10 R).
- P1: Signale auf 15m/1h-Struktur, Maker-Einstieg Standard, LLM nur als Veto.
- P2: Edge-Filter-Block-Zähler in Diagnose; Policy-Fingerprint um edge_filter_* erweitern.

### Session 03.11.2026 (2) – freie Verbesserungen
- Live-Freigabe-Check (`edge_filter.readiness`) + Bilanz geblockter Live-Einstiege im Edge-Bericht/Diagnose.
- Empfohlene Einstellungen (`services/ai_recommended_settings.py`, `GET/POST /api/ai/recommended-settings[/apply]`), Übernahme nur per Klick.
- Wochen-Edge per Telegram (`services/edge_weekly.py`, So 19:00, ohne LLM), `GET /api/ai/edge-weekly`, `/config`, `/send`.
- Edge-Filter-Keys im Policy-Fingerprint; Edge-Tabelle nutzt Einstiegs-Urteil; Diagnose-Tabellen mobil scrollbar.
- Tests: `tests/test_edge_readiness_and_recommended.py`, `tests/test_recommended_weekly_api.py`.
