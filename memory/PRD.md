# PRD – KI-Trader (Daytrading-Website, extern auf Render deployt)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader,
Branch conflict_220826_1619). Verbesserungen sauber, modular, rückwärtskompatibel,
Original-Struktur beibehalten (1:1 Render-Deploy). Stack: FastAPI + React (CRA/craco)
+ MongoDB; Live-Trading über Bitunix; KI-Team (analyst, trade_manager,
market_observer, news_watcher, learner, …) mit Multi-Provider-Fallback
(groq/cerebras/openrouter/gemini/mistral, je bis 16 Backup-Keys).
Preview-Umgebung läuft ABSICHTLICH ohne Bitunix-/Telegram-/AI-Keys
(keine Kollision mit der produktiven Render-Instanz).

## User Persona
Betreiber (Admin: Admin / Dean06Greif!/Admin) überwacht KI-Trader, Live-Positionen,
Telegram-Signale. Ziel: autonomer, sich selbst verbessernder "ultimativer Trader".

## Umgesetzt – Iteration 1 (22.06.2026)
1. Legacy Coin-Level-AutoTradeModal.js sauber entfernt (CSS bleibt, wird von
   StrategyAutoTradeModal genutzt).
2. Test-Marker-Split: `pytest -m unit` (schnell, offline) vs. `-m live`
   (E2E gegen laufende Umgebung); Auto-Klassifizierung in tests/conftest.py.
3. Funding-Fees: services/funding_fees.py (Bitunix funding_rate, 10-min-Cache,
   fail-open) → fließt in fee_guard_check ein (Haltedauer-Horizont scalp 2h /
   swing 24h). Funding-Wächter für offene Live-Trades: Warnung ab 20% der Marge,
   optionaler Auto-Close ab 40% (settings _id=funding_guard, close_enabled
   Default AUS), Lauf alle 30 min im Monitor.
4. Entry-Order-Registry (services/entry_order_registry.py, Collection
   pending_entry_orders): Maker-Limit-Orders registriert; Watchdog übernimmt
   späte Fills als echten KI-Trade inkl. Telegram-Signal "TRADE ERÖFFNET
   (Limit-Fill)"; kein Market-Fallback bei unbestätigtem Cancel (kind=orphan).
5. HEAD / → 200 (routers/general.py); Frontend-Build-Warnungen behoben
   (eslint 9.39.5, Resolutions, devDeps @babel/core@7, react-is, typescript…).

## Umgesetzt – Iteration 2 (23.06.2026)
1. RCA Trade-Herkunft (Live-Diagnose Bitunix + Prod-DB read-only): die als
   "Manuell (Bitunix)" übernommenen Positionen stammten aus Orders OHNE TP/SL
   (GTC-Entries) – nicht vom aktuellen Website-Code (Bot hängt immer TP+SL an).
2. clientId-Tagging: alle Bot-Entry-Orders tragen clientId `KIT-<strategie>-<ts>`
   (make_client_id) → Herkunft an der Börse beweisbar.
3. Absturz-Schutz: JEDE Entry-Order (Market/Limit/Maker, alle Strategien) wird
   nach Order-Annahme registriert, nach DB-Insert aufgelöst → Deploy/Restart
   zwischen Order und Insert wird korrekt zugeordnet statt "Manuell".
4. KI-Gewinnschutz intern per Default (ohne Coin-Settings): Gewinnsicherung
   (SL in den Gewinn, lock 50% ab +30% auf Marge), Marge-Freisetzung +
   Hebel-Maximierung bis 200x, SL sicher vor Liq. Settings _id=
   ai_profit_protection; API GET/PATCH /api/autotrade/ai-protection (PATCH Admin).
5. KI-Priorisierung: live-kritische Rollen (trade_manager, analyst,
   market_observer, news_watcher) nutzen alle Keys inkl. Backups; Lern-/
   Datensammel-Rollen (learner, research_analyst, summarizer) nur Primär-Key
   (ai_providers.role_priority + restrict_indices_for_priority).

## Umgesetzt – Iteration 3 (23.06.2026)
1. Key-Level-Trailing (intern, ai_profit_protection.level_trail): SL ab +1R
   hinter das zuletzt durchbrochene Level (find_swing_levels /
   key_level_trail_sl in bitunix_trade.py, live-sync via _live_move_sl).
2. Live-Gate-Bypass: max. 2 hochkonfidente KI-Live-Trades/Tag (Konfidenz >=
   min_confidence+5) trotz noch nicht "live-reifem" Setup; Rest sammelt Paper-
   Daten (live_gate_bypass_enabled/margin/per_day in KI-Config).
3. Herkunft in erweiterten Trade-Details (PerformanceAnalytics.js,
   data-testid trade-origin-<id>): "Website · <Strategie> · KIT-…" (grün) vs.
   "Manuell (Bitunix)" (gelb); bitunix_client_id am Trade gespeichert.
4. KI sieht Gesamt-Konto-Verlauf: Analyst-Kontext (_strategy_performance_text)
   enthält letzte 20 Trades ALLER Quellen inkl. "DU SELBST"-Markierung;
   dynamisch auch für künftig hinzugefügte Strategien. ML-Lab trainiert bereits
   auf Signalen aller Strategien.

## Umgesetzt – Iteration 4 (23.06.2026)
1. Bypass-Feed: Telegram-Meldung (ntype live_gate_bypass) mit Setup, Grund,
   Konfidenz und Tageszähler, wenn die KI per Setup-Bypass live geht.
2. Offene-Order-Karte: GET /api/autotrade/pending-entry-orders (Registry inkl.
   age/expires-Countdown 36h) + Frontend-Karte "Wartende KI-Limit-Orders" im
   Analyse-Panel > Trades (PendingEntryOrders.js, Poll 15s, versteckt bei leer).
3. Beratung Live-vs-Paper-Kluft dokumentiert: Empfehlung Reihenfolge
   ATR-Low-Vol-Market-Block (Option 2) → Limit an Key-Levels mit TTL/Re-Quote
   (Option 1) → 1m-Hybrid-Trigger (Option 3); zusätzlich Fill-Qualitäts-Messung
   (Slippage-Logging) und Paper-Slippage-Simulation vorgeschlagen. NOCH NICHT
   implementiert – wartet auf User-Entscheidung.

## Tests
824 Unit-Tests grün (`pytest -m unit`, ~20s); Live-Suite via `-m live`.
Neue Testdateien: test_funding_fee_guard.py, test_entry_order_registry.py,
test_ai_protection_and_priority.py, test_key_level_and_live_gate.py.

## Bekannt / Hinweise
- eslint-Deprecation aus react-scripts (internes eslint@8) bleibt (nur mit
  CRA-Ablösung behebbar, kosmetisch); Cerebras-402 = Kontingent, Fallback ok.
- Frontend Cold-Start: transiente "Failed to fetch" in Console (kein Blocker).
- Preview-.env ohne Live-Keys; auf Render Env unverändert lassen.

## Backlog / Nächste Aufgaben
- P1: UI-Panel für Gewinnschutz-Policy + Funding-Wächter-Schwellen
- P1: Funding-Kosten (funding_est_usdt) in der Trade-Ansicht anzeigen
- P2: Registry-/Watchdog-Statuskarte (offene Entry-Limit-Orders) im Frontend
- P2: Coin-Max-Hebel auch im Backtester (effective_leverage) berücksichtigen

## Umgesetzt – Iteration (23.08.2026, Branch conflict_220826_1859)
1. **Key-Level-Limit-Orders** (services/key_level_limits.py, Collection ai_limit_orders):
   KI wählt pro Entscheidung entry_type market|limit, limit_price am Key-Level
   (Order-Block/POC/VAH/VAL/Range-Grenze; LONG darunter, SHORT darüber; max 2.5%
   scalp / 8% swing) und limit_valid_min (15–480, KI-gewählt). Orders lokal/synthetisch
   (identisch Paper+Live, keine verwaisten Börsen-Orders), Fill-Check je Scanner-Tick
   (core/scheduler.py), Fill läuft durch die normale Pipeline (Guards greifen beim Fill),
   Verfall + Neu-Bewertung je Analyse-Zyklus (reevaluate: cancel_limit / Gegenrichtung /
   Market-Ersatz; Ersetzen gleicher Richtung via place). Prompt: Schema-Felder + Block
   "WARTENDE LIMIT-ORDERS". API: GET /api/ai/limit-orders, DELETE …/{id} (Admin).
   Frontend: KeyLevelLimitOrders.js Karte in PerformanceAnalytics (Countdown, Stornieren).
2. **Ehrliches Paper** (services/paper_execution.py): Paper-Fills adversarial mit
   Spread/2 + Slippage. Echter Top-of-Book (Binance→OKX→Bybit, keyless, 20s Cache),
   Fallback-Tiers (Major 0.01/0.01, Crypto 0.04/0.03, Yahoo 0.06/0.05 %). Integriert in
   bitunix_trade.py: Entry (on_signal), Exits (_manage_trade TP1/TPF/SL, manual_close,
   partial_close). Nur mode=paper. Settings: paper_realistic_fills,
   paper_fallback_spread_pct, paper_slippage_pct. Trade-Doku: paper_exec + PAPER-FILL-Events.
3. **Größenabhängige Slippage** (Erweiterung zu 2): Sqrt-Impact-Modell
   size_scaled_slippage – bis Referenz-Notional (Major 100k / Crypto 25k / Other 10k USDT,
   oder echte Top-of-Book-Tiefe der konsumierten Seite, wenn größer) Basis-Slippage,
   darüber × sqrt(Notional/Referenz), hart gedeckelt (Default 0.30%). Alle Fill-Aufrufer
   übergeben die Order-Notional; paper_exec dokumentiert size_mult / notional_usdt /
   book_depth_usd / base_slippage_pct. Settings: paper_size_slippage_enabled,
   paper_size_ref_notional, paper_max_slippage_pct.
Tests: backend/tests/test_key_level_limits.py (8 Blöcke) + test_paper_execution.py
(8 Blöcke) grün; Regression 826 Unit-Tests grün; E2E via Testing-Agent (iteration_8.json,
100%). Hinweis: Root-tests test_stale_price_* + test_fix_0_5_* sind am Wochenende
zeitabhängig rot (pre-existing, auch im Original-Repo).

## Backlog (P1/P2)
- P1: Limit-Order-Historie als UI-Ansicht mit Fill-Quote-Statistik
- P1: Telegram-Notification bei Limit-Order-Platzierung/-Fill (eigener Event-Typ)
- P2: KI-Lernschleife: Fill-Quote & entgangene Moves der Limit-Orders in Lessons
- P2: Paper-vs-Live-Vergleich derselben Setups (Live-Reife-Ansicht)
