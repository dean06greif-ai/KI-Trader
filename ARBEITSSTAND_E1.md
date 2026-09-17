# Arbeitsstand E1 (Audit + Verbesserungen, Juni 2026)

Zweck: Fortschritt kompakt festhalten, damit bei Abbruch klar ist, was erledigt ist und wo weitergemacht wird.

## Phase 0 – Setup ✅
- Repo `dean06greif-ai/KI-Trader` Branch `dein-branch-name` nach `/app` (Originalstruktur 1:1, Render-kompatibel)
- Lokale Test-Mongo, Backend läuft lokal (`AI_TRADER_LOCAL_DISABLE=1`, KEINE Bitunix/IBKR-Keys lokal → kein Zugriff auf echte Positionen)
- ⚠️ Hinweis: Beim allerersten lokalen Start waren Bitunix-Keys kurz gesetzt; der Positions-Watchdog hat 2 Börsenpositionen (SOL/ETH LONG) nur LOKAL „adoptiert“ (lokale DB, keine Orders). Keys sofort entfernt.

## Phase 1 – Audit alter Prompt ✅ (Ergebnis)
| Punkt | Status | Fundstelle / Lücke |
|---|---|---|
| MongoDB 73% voll → Retention | ✅ vorhanden | `services/retention.py` (14 Collections, täglicher Sweep, Boot in `server.py:341`) · API `routers/maintenance.py` |
| … TTL-Indizes / Kompaktierung | ⚠️ bewusst anders | ISO-String-Timestamps → kein BSON-TTL möglich (App-Sweep statt TTL, korrekt). `compact` fehlt – auf Atlas M0 ohnehin nicht erlaubt; Quota = logische Größe → Löschen reicht |
| … Lücken | ❌ | keine UI; Collections ohne Regel: app_notifications, ai_ghost_trades, regime_lab_runs, dynamic_switch_log, confluence_events, local_jobs |
| Backtester „Setup-Optimierer“ | ✅ vollständig | `setup_backtest/runner.py:327-412` (nie Downgrade, OOS-PnL ≥ Basis), UI `AITraderSeeding.js` Modus „Optimierer“ |
| Backtester „Nur Event-Setups“ | ✅ vollständig | Button `event-seed-run-only` → `EventSetupSeeding` (eigener Job) |
| KI kaum Trades → adaptive Lockerung | ✅ vorhanden | `services/activity_guard.py` (30-min-Tick in Engine, 3 Stufen, Floors, Rückstraffung) |
| … Lücken | ❌ | keine UI; bei anhaltender Flaute (max. Stufe erreicht) passiert NICHTS weiter – keine Setup-Ideen-Runde |
| Datensammel-Trades für neue Ideen | ✅ vorhanden | `ai_playbook.propose_custom_setup` (Shadow/Paper bis Reife-Gate), genutzt von Move-Scanner + Strategie-Labor |
| Lernfunktion nach Trade („was wäre wenn“) | ✅ | `trade_postmortem.py` (run_loop, `context_text` in Prompt `ai_engine_context.py:821`) |
| Lernfunktion vor Trade | ✅ | `learning.lessons_text` → Prompt (`ai_engine_context.py:269,809`) |
| Bewegungs-Analyse | ✅ vorhanden | `services/ai_move_scanner.py` (5-min-Tick, LLM-Ursache, missed, revise/new_setup) |
| … Lücke | ❌ | keine UI für Move-Events |
| Multi-Asset Setup-Backtester | ⚠️ Bugs | Forex-Historie via Yahoo (nur ~30 Tage) statt IBKR (`history_sources.py`), kein Klassen-Tages-Cap (90–365 Tage angefordert), Wochenend-/Pausen-Kerzen nicht gefiltert (`market_hours` in setup_backtest ungenutzt) |
| Multi-Asset Event-Setups | ✅ | `event_assets.py` routet Forex → IBKR, je Klasse eigene Backtests/Validierung/Live-Opt-in |
| Kosmetik | ❌ | `AITraderSeeding.js` Intro-Text doppelt („Mindest-Trades adaptiv“ 2×) |

## Phase 2 – Umsetzung ✅ (Code fertig, lokal verifiziert)
- [x] 2.1 Retention: +6 Collections (`app_notifications`, `ai_ghost_trades`, `regime_lab_runs`, `dynamic_switch_log`, `confluence_events`, `local_jobs`), Best-Effort-Kompaktierung `POST /api/maintenance/compact` (Atlas-M0-Hinweis), `status()` liefert `overrides`/`last_compact`. UI-Tab „Speicher“ im KI-Labor (`frontend/src/components/StoragePanel.js`)
- [x] 2.2 Setup-Backtester Multi-Asset: `history_sources.fetch_ibkr` (1m, 48h-Chunks, Fallback Yahoo), `_resolve` Forex→IBKR wenn Gateway konfiguriert, `days_cap` (IBKR 730 Tage) in `candle_cache.get_candles`; `core/market_hours.open_mask` (vektorisiert) + `runner.market_open_only` (Nicht-Krypto: Wochenende/Handelspause raus); `data_notes` im Ergebnis + UI-Hinweis
- [x] 2.3 Aktivitäts-Wächter Stufe 4: `services/activity_ideas.py` (LLM-Ideen-Runde: verpasste Moves + HOLD-Gründe + Playbook → new_setup/revise/none), Trigger in `activity_guard.check` (`idea_round_due`), API `POST /api/ai/activity-guard/idea-round`, Config `idea_rounds`/`idea_gap_hours`
- [x] 2.4 UI-Tab „Adaptiv“ im KI-Labor (`frontend/src/components/AdaptivePanel.js`): Aktivitäts-Wächter (Status, Config, Ideen, Verlauf) + Bewegungs-Scanner (Status, Config, Events-Tabelle, „Jetzt scannen“)
- [x] 2.5 Kosmetik-Fix AITraderSeeding (doppelter Absatz entfernt)
- [x] 2.6 Tests `backend/tests/test_e1_adaptive_multiasset.py` (23 neu, grün) + Alt-Suite `test_adaptive_modules.py` grün

## Phase 3 – Tests
- [x] Neue Tests + adaptive_modules: 37 passed
- [x] Lokal per curl: compact/retention-run/idea-round (LLM antwortete, action=none korrekt begründet) → ok
- [x] Screenshot: Tabs „Adaptiv“ + „Speicher“ rendern mit Daten
- [ ] Gesamte Unit-Suite (`pytest tests -m unit`) – läuft (Log `/tmp/unit_suite.log`)
- [ ] Testing-Agent (Backend+Frontend E2E)

## Offene Punkte / Nächste Schritte
- Nach grünem Testing-Agent: Nutzer pusht selbst zu GitHub (Render-Deploy). Auf Render müssen keine neuen Env-Variablen gesetzt werden.
- Hinweis Forex-Backtest: IBKR-Gateway (ibeam) muss eingeloggt sein, sonst Yahoo-Fallback (30 Tage) – wird im Ergebnis als „Datenhinweis“ angezeigt.
