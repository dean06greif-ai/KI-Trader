# PRD – KI-Trader (externe Daytrading-Website)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader, Branch `conflict_230826_1950`, Deployment: Render). Verbesserungen sollen sauber, modular und rückwärtskompatibel in die bestehende Architektur eingepflegt werden. Originalstruktur (Ordner/Dateien) beibehalten für Render-Deployment.

## Architektur
- Backend: FastAPI (`/app/backend/server.py` + `routers/` + `services/` + `core/`), MongoDB Atlas (extern, 500 MB Limit), Bitunix Trading-API (LIVE-Keys!), Multi-LLM-Provider-System (`services/ai_providers.py`, Rollen-Teams in `services/ai_roles.py`)
- Frontend: React + craco, `PerformanceAnalytics.js` (offene/geschlossene Trades), `AITradingPanel.js` (KI-Team, Warnungen), lightweight-charts v5
- KI-Rollen laufen parallel (News-Wächter, Deep-Analyst, Chat, Model-Advisor …)

## User-Entscheidungen
- Branch: `conflict_230826_1950`
- Trade-Chart: Bitunix Kline-API on-demand (kein Mongo-Speicher)
- MFE/Peak-Tracking: nur neue Trades ab jetzt (alte zeigen "—")
- Watchdog-Bug: zuletzt beim XRP-Trade (KI-Trader) aufgetreten

## Umgesetzt
### 23.06.2026 – Bugfix: Falsche Fallback-Zuordnung in KI-Warnungen ✅ (getestet 5/5)
- Root Cause: `record_result()` nutzte das globale `_current_role` zum Call-ENDE → parallele Rollen-Calls (News-Wächter triggert Deep-Analyse als Task, Deep-Analyst nutzt OpenRouter) wurden falsch zugeordnet.
- Fix: `generate_chain()`/`stream_chain()` fixieren die Rolle einmal am Start (`role`-Parameter) und reichen sie an alle `record_result()`-Aufrufe durch. Aufrufer: `ai_engine.generate_for_role`, Chat-Stream (role="chat"), `ai_model_advisor` (role="model_advisor").
- Tests: `backend/tests/test_role_fallback_attribution.py`, `test_role_attribution_stream_iter9.py`.

### 23.06.2026 – 5 Trade-Verbesserungen ✅ (Testing-Agent: 24/24 Backend, 100% Frontend)
1. **MFE/Peak-Tracking**: `update_peak()` in `services/bitunix_trade.py`; Tracking im Tick von `_manage_trade` (kein Extra-DB-Write), Exit-Fill fließt beim Schließen ein, `manual_close` ebenfalls. Anzeige via `_enrich_trade` (`core/utils.py`): `computed.peak_price/peak_distance_pct/mfe_pct` – offen live berechnet, geschlossen gespeichert, Alt-Trades → null. UI: Meta-Feld `trade-peak-{id}` + `lvl-peak`-Zeile in der Preis-Leiter.
2. **Trade-Chart on-demand**: `GET /api/autotrade/trades/{id}/chart` (routers/autotrade.py, `_chart_interval` wählt 1m–1d), Kerzen via `fetch_klines_range()` (`services/bitunix_client.py`, öffentliche Bitunix-Kline-API, kein DB-Speicher). Frontend: `TradeChart.js` (lightweight-charts v5, Preislinien Entry/SL/SL-initial/TP1/TP Full/Exit/Peak, de-DE-Locale-Fix, autoscaleInfoProvider damit TP/SL außerhalb der Kerzen-Range sichtbar sind, ResizeObserver). Button `trade-history-chart-btn-{id}` neben "Live-Chart öffnen", nur geschlossene Trades, lädt erst beim Klick.
3. **Strategie-Filter**: `<select data-testid="trade-filter-strategy">` neben "Nur BTC" – filtert offene UND geschlossene Trades (inkl. Option "Manuell / Extern").
4. **Mehr laden (+100)**: Backend `offset`-Param + `total` bei `?status=...` in `GET /api/autotrade/trades`; Frontend seitenweises Nachladen (`load-more-closed-btn`, dedupliziert, "x von y", Button verschwindet am Ende). Kein zusätzlicher Mongo-Speicher.
5. **Watchdog-Fix (unvollständige Closes, XRP)**: Root Cause: `_fmt_qty` rundete beim Voll-Close AB auf die Step-Size → bis zu 1 Step blieb auf Bitunix offen. Fix: `flash_close(full=True)` rundet AUF (`_fmt_qty(round_up=True)`), reduceOnly schützt vor Überschließen; `close_live_position` reicht `full` durch; Watchdog nutzt an 3 Stellen `full=True`.
- Tests: `backend/tests/test_trade_improvements.py` (11), `test_iter10_trade_improvements_api.py` (10, vom Testing-Agent).

### 23.06.2026 – MAE-Tracking + Analyse Live-vs.-Paper ✅ (Testing-Agent verifiziert)
- **MAE ("Tiefster/Höchster Gegenlauf")**: `update_trough()` (Spiegelbild von update_peak) in bitunix_trade.py, Tracking im Tick von `_manage_trade` + Exit-Fill + manual_close. `_enrich_trade`: `trough_price/trough_distance_pct/mae_pct`. UI: Meta-Feld `trade-trough-{id}`, `lvl-trough`-Zeile (#C97A8A) in der Preis-Leiter, gepunktete Trough-Linie im Trade-Chart. Chart-Endpoint liefert `trough_price`.
- **Bugfix nach Testing (HIGH)**: `_enrich_trade` nahm bei offenen Trades ohne gespeicherten Wert nur den aktuellen Kurs als Kandidat → positives MAE / "Gegenlauf über Entry". Fix: Entry immer als Kandidat (wie Tick-Tracker), symmetrisch auch beim Peak; `-0.0`-Normalisierung. Tests: 18 Unit + 9 API (test_iter11_mae_api.py) grün.
- **Analyse/Plan**: `UMSETZUNGSPLAN_LIVE_QUALITAET.md` aktualisiert – Ist-Stand: Baustein C (Key-Level-Limits) + D (ehrliches Paper) + E (MFE/MAE) fertig; OFFEN: Baustein A (ATR-Market-Block, Schwelle 0,10% default konfigurierbar – vom User bestätigt) und Baustein B (Slippage-/Fill-Qualitäts-Messung inkl. slippage-stats-Endpoint + MFE/MAE-Auswertung je order_kind). Empfohlene Reihenfolge: A → B → 2-4 Wochen Messphase → erst dann über Option 3 (1m-Hybrid) entscheiden.

### 24.06.2026 – Backlog P1+P2 komplett + Slippage-UI-Karte ✅ (Testing-Agent: 13/13 API + 10/10 Unit, Frontend 100%)
1. **Forex-Chart-Fix (P1)**: `kline_available()` in `core/instruments.py` (Forex hat `bitunix=None`); `_enrich_trade` liefert `computed.chart_available`; Chart-Endpoint antwortet für Forex mit `chart_available=false` + `reason` statt leerer Kerzen; Frontend blendet `trade-history-chart-btn` bei Forex aus.
2. **Not-Fallback-Kennzeichnung (P1)**: `role_manager.team_chain()` (Team ohne Not-Kette, aus `chain()` extrahiert); `health_status()` setzt je active_fallback `outside_team` (via `_fallback_outside_team`, lazy imports); UI: gelber Badge "NOT-FALLBACK außerhalb des Teams" (`ai-fallback-outside-team-{role}`) in AITradingPanel.
3. **Modularisierung (P2)**: `TradeDetailCard.js` (Card + LevelRow + OpenTradeActions + fmt-Helper) und `TradeFilters.js` (PnlFilter + TradeListControls) aus `PerformanceAnalytics.js` (1029 → ~705 Zeilen) extrahiert – alle data-testids unverändert, reine Refaktorierung.
4. **record_result ohne globalen Rollen-Fallback (P2)**: `role or _current_role.get("role")` entfernt – Rolle kommt immer explizit von generate_chain/stream_chain; ohne Rolle bleibt der Eintrag unzugeordnet. Test angepasst (`test_record_result_without_role_stays_unassigned`).
5. **Slippage-/Fill-Qualitäts-Karte (NEU)**: `SlippageStatsCard.js` im Analyse-Panel → Trades-View (`slippage-stats-card`), nutzt `GET /api/autotrade/slippage-stats?days=7|30|90`, Empty-State solange `measured_trades=0` (Messphase). days-Clamp-Fix: `days=0 → 1`.
- Tests: `backend/tests/test_backlog_p1_p2_fixes.py` (5 neu), `test_iteration13_api.py` (13, Testing-Agent). Unit-Suite: 865 passed.

## Backlog
- Nach der Messphase (2–4 Wochen ab 23.06.): Slippage-Auswertung (market vs. maker vs. limit_fill, MAE je Order-Art) → dann Entscheidung über Option 3 (1m-Hybrid-Trigger)
- Beobachten: Testing-Agent sah einmalig 2×60s-API-Timeouts (möglicher Event-Loop-Block durch Scan/Backfill), nicht reproduzierbar
- Optional P3: PerformanceAnalytics.js weiter aufteilen (Clear-Modal, Zeit-Analyse)

## Wichtige Hinweise
- backend/.env enthält ECHTE Bitunix-LIVE-Keys – in Tests keine Orders platzieren!
- Backend braucht nach Restart ~90–120 s (Candle-Backfill) bis Port 8001 antwortet
- pytest: `-n 0` für serielle Läufe anhängen (pytest.ini erzwingt xdist)
- Admin-Login: /app/memory/test_credentials.md
- DB-Stand (23.06.): 390 geschlossene / 26 offene Trades
