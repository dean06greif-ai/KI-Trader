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

## Backlog
- P1: Trade-Chart-Button für Symbole ohne Bitunix-Kline (Forex, z.B. GBPUSD) ausblenden oder Grund im Response melden (aktuell: sauberer Hinweistext "Keine Kerzendaten")
- P1: Kennzeichnung "Not-Fallback außerhalb des Teams" in KI-Warnungen (UI-Hinweis)
- P2: `PerformanceAnalytics.js` (~1020 Zeilen) modularisieren (TradeDetailCard/Filter in eigene Dateien)
- P2: Globalen `_current_role`-Fallback in `record_result` entfernen

## Wichtige Hinweise
- backend/.env enthält ECHTE Bitunix-LIVE-Keys – in Tests keine Orders platzieren!
- Backend braucht nach Restart ~90–120 s (Candle-Backfill) bis Port 8001 antwortet
- pytest: `-n 0` für serielle Läufe anhängen (pytest.ini erzwingt xdist)
- Admin-Login: /app/memory/test_credentials.md
- DB-Stand (23.06.): 390 geschlossene / 26 offene Trades
