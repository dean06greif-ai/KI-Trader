# PRD – Neiekeeke Daytrading-Website (extern, Render-Deploy)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/Neiekeeke, Deploy auf Render).
Grundsatz: sauber, modular, rückwärtskompatibel; Originalstruktur beibehalten. Zu fixen:
1. KI-Trader macht Datensammel-Trades, aber keine Live-Trades ("kein verfügbares Kapital").
2. Mobile-Bug: an einer bestimmten Stelle im Dashboard kein normales Hochscrollen mehr.
3. Bitunix-Live-Anzeige: externe Margen-Reduktion + Hebel-Erhöhung, Partial-TP/SL werden nicht
   erkannt; freies Kapital falsch berechnet (teils negativ) → KI blockiert Live-Trades.

## Architektur
- Backend: FastAPI (server.py + routers/ + services/ + core/), MongoDB (Motor), Scheduler,
  Positions-Watchdog (120s-Loop gegen echte Bitunix-API), KI-Engine (Cerebras/Groq/OpenRouter/...).
- Frontend: React (CRA/craco), Komponenten-CSS, mobile.css/extra.css für Responsive.
- Singleton `autotrader` lebt in `core/state.py`.

## Umgesetzt (20.06.2026)
1. **KI-Trader Live-Trades gefixt**
   - `services/ai_trade_manager.py` Z.408/713: kaputter Import `from services.bitunix_trade
     import autotrader` (ImportError – Symbol existiert dort nicht) → `self.autotrader`.
     Der Import brach jede KI-Trade-Verwaltung/Review ab.
   - `services/bitunix_trade.py`: neues `trade_bound_margin()` – gebundene Marge =
     Rest-Notional/Hebel (skaliert mit Teil-Closes & Hebel-Änderungen); `used_margin()` nutzt es.
   - Neues `free_capital(mode)`: Live nutzt echtes Börsen-Guthaben (`_live_available_balance`,
     10s-Cache) als Wahrheit (Modus "full": free = available; fixed/percent: min(alloc-used, avail)).
   - `open_from_signal` + `_free_capital_ok` nutzen `free_capital`.
2. **Bitunix-Sync externer Änderungen**
   - Neues `AutoTradeManager.sync_position_state(local, pos)`: spiegelt externe Teil-Closes
     (bucht PnL/Fees, reduziert qty_remaining) und Margen-/Hebel-Änderungen (margin_used,
     effektiver Hebel, Liq-Preis) in den lokalen Trade; Marge ist Wahrheit (Bitunix 'leverage'
     ist nur das Setting → Anti-Flattern), Misch-Positionen (extern aufgestockt) bleiben tabu.
   - `services/position_watchdog.py`: ruft sync_position_state je Position (auch sync-only),
     Status-Zähler `state_synced`.
3. **Kapital-Endpunkte**: GET /api/autotrade/capital + /api/autotrade/balance nutzen free_capital,
   liefern zusätzlich `exchange_available`; free nie mehr fälschlich negativ.
4. **Mobile-Scroll-Trap (Dashboard)**: mobile.css (≤968px): `.right-panel` und
   `.performance-analytics` max-height none / overflow-y visible (kein verschachtelter
   60vh-Innen-Scroller mehr); extra.css bereinigt. Desktop-Layout (sticky) unverändert.
5. **Regressionstests**: `backend/tests/test_capital_sync_fixes.py` (16 Tests: Margen-Berechnung,
   free_capital, Sync inkl. Anti-Flattern, E2E-Kernszenario); stale Test
   `test_comparison_and_ram_iter2.py` an dokumentiertes Verhalten angepasst.
   Getestet: Testing-Agent 100% backend+frontend (iteration_6.json), inkl. Live-Verifikation
   gegen echten Bitunix-Account (Sync realer externer Änderungen beobachtet).

## Umgesetzt (21.06.2026) – Setup-Reife-Gate
- Feature: KI-Trader macht Live-Trades nur für Setups mit genug echten Daten
  (Playbook-Urteil 'bewährt'/'neutral'); neue/unreife Setups ('test'/ohne Daten) werden
  auch über der Live-Konfidenz-Schwelle in die Paper-Datensammlung umgeleitet.
- `ai_playbook.py`: `live_ready()` (pur) + `cached_setup_stats()` (5-min-Cache).
- `ai_engine.py`: Config-Key `setup_live_gate` (default true, per /api/ai/config schaltbar),
  `_setup_live_gate()` – greift NUR wenn effective_mode('ai_trader')=='live'; Paper-Modus unverändert.
  Umgeleitete Entscheidungen tragen `live_gate`-Begründung (sichtbar in /api/ai/status).
- Frontend: Toggle "Live nur reife Setups" (data-testid `ai-setup-live-gate-select`) im
  AITradingPanel neben Datensammlung.
- Tests: `tests/test_setup_live_gate.py` (6 Tests); Testing-Agent 100% (iteration_7.json).

## Umgesetzt (21.06.2026) – Reife-Feed-Meldung + Reife-Übersicht
- `ai_playbook.refresh()`: erkennt Übergang "nicht live-reif -> live-reif" pro Setup
  (Status persistiert in settings-Doc `ai_playbook_state.live_ready`) und postet genau
  EINE KI-Feed-Meldung (ai_chat, role='playbook', "Setup ... LIVE-freigeschaltet ...");
  erster Lauf initialisiert still (kein Spam).
- `maturity_overview()` + GET /api/ai/playbook Feld `maturity`: pro Setup Trades, Winrate,
  PnL, Urteil, live_ready, Begründung (live-reife zuerst sortiert).
- Frontend: Reife-Tabelle "Setup-Reife – Live-Freischaltung" im AITradingPanel unter
  Reiter 'Verlauf' (AIEquityPanel, data-testid ai-setup-maturity-table); Feed-Rendering
  role='playbook' (data-testid ai-playbook-message, "SETUP LIVE-FREIGESCHALTET").
- Tests: test_setup_live_gate.py auf 10 Tests erweitert (Übergangs-/Init-/Übersicht-Logik);
  Testing-Agent 100% backend+frontend (iteration_8.json, inkl. Cleanup der Testdaten).

## Bekannte Umgebungs-Hinweise
- Lokale Test-DB ist frisch (Produktion nutzt Mongo Atlas); viele alte Integrationstests des
  Repos benötigen geseedete Produktionsdaten und schlagen lokal fehl (dokumentiert in
  /app/test_reports/full_tests.log) – KEINE Regressionen der Fixes.
- Modus lokal auf "paper" belassen; echte Bitunix-Keys nur lesend verwenden.

## Backlog / Nächste Aufgaben
- P1: `events[]` der Trades auf strukturierte Objekte {ts, type, msg} migrieren (Anmerkung Testing-Agent).
- P1: Telegram-Benachrichtigung bei erkannten externen Änderungen (SYNC-Events).
- P2: Anzeige "effektiver Hebel vs. Hebel-Setting" im Trade-Detail der Website.
- P2: Externe Teil-Closes mit echtem Fill-Preis aus der Bitunix-Order-History statt Mark-Preis buchen.
