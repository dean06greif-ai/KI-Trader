# PRD – KI-Trader (Daytrading-Website, extern auf Render deployt)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader,
Branch conflict_030926_0737; FastAPI-Backend + React-Frontend + MongoDB Atlas,
deutschsprachig, deployt auf Render). Originalstruktur MUSS erhalten bleiben.
Verbesserungen sauber/modular in bestehende Architektur, Stabilität & Rückwärts-
kompatibilität vor aggressiven Änderungen.

## Architektur
- backend/ FastAPI (server.py, routers/, services/, strategies/, core/), Motor/MongoDB
- frontend/ React (CRA), components/, lib/
- local_worker/ optionaler lokaler Rechen-Worker
- Trading: Bitunix (Krypto-Futures live), Paper-Engine, Backtester, Optimizer (fast_sim),
  Job-Serie/Warteschlange (services/job_series.py), Strategie-Copilot (OpenRouter),
  Telegram-Notifications, Admin-Auth (Bearer-Token)

## User Persona
Einzelner Power-User (Admin) – Daytrader, optimiert Strategien selbst, tradet
Krypto live über Bitunix und jetzt Forex live über Interactive Brokers.

## Umgesetzt am 03.06.2026 (diese Iteration)
1. **IBKR-Forex-Live-Trading**
   - services/ibkr_client.py: Client-Portal-Gateway REST-Client (IBeam-kompatibel),
     Env: IBKR_GATEWAY_URL, IBKR_ACCOUNT_ID (optional), IBKR_VERIFY_SSL
   - services/ibkr_trade.py: Bracket-Order (MKT + STP-SL + LMT-TP, GTC), Monitor-Loop
     (Session-Keepalive + Positions-Abgleich, verbucht Closes mit echtem Fill),
     manueller Close via _live_flash_close-Branch
   - core/instruments.py: Instrument.ibkr / broker / live_tradable; Forex → IBKR
   - bitunix_trade._on_signal_impl: Live-Forex → IBKR-Pfad; ohne Gateway → Paper-Fallback
   - Ausschlüsse: monitor() & sync_live_positions() überspringen broker=ibkr
   - GET /api/ibkr/status (routers/autotrade.py) + Statusanzeige im Master-Panel
   - Anleitung: /IBKR_SETUP_ANLEITUNG.md (IBeam auf Render, kein klassischer API-Key)
   - Hinweis: Live-Forex nutzt vollen TP (1 TP je IBKR-Bracket), kein Partial-TP1
2. **Forex-Gebühren getrennt** (Live + Paper + Backtest)
   - services/fee_model.py: fee_percent_for(symbol, default, notional) – Forex =
     Kommission %/Seite + Mindestkommission USD; Settings forex_commission_pct (0.002),
     forex_min_commission_usd (2.0) in strategy_scanner.DEFAULT_SETTINGS
   - Eingebunden: bitunix_trade (fee_pct_used), backtester.simulate_pair
   - UI: SettingsPanel "Forex-Gebühren (Interactive Brokers)"-Karte + IBKR-Status
3. **2 neue Mean-Reversion-Strategien** (universell, optimierbar)
   - mr_zscore ("Z-Score Mean Reversion PRO"), mr_keltner_fade ("Keltner-Channel Fade")
   - registriert in strategies/registry.py, volle DEFAULT_PARAMS (min/max/step)
4. **Warteschlange im Optimizer integriert** (kein Extra-Reiter)
   - Optimizer.js: Button "Zur Warteschlange" (opt-add-series) + Inline-QueueStrip
     (opt-queue-strip): Status je Auftrag, "Ergebnis laden" in die Ergebnis-Ansicht,
     Löschen; Backend job_series startet Aufträge automatisch nacheinander
5. **Copilot-Fixes**
   - Abbrüche: Chat als Hintergrund-Job (POST /api/copilot/chat/start +
     GET /api/copilot/chat/job/{id}), Budget 110s/45s – Render-60s-Cap umgangen;
     StrategyCopilot.js pollt
   - Performance: Kontext getrimmt (Raw-JSON 4500→2500, History 10→6)
   - Drawdown-%: metrics_summary/sanity_check (bereits im Branch) bleiben; dd ist
     relativ zum Startkapital – konsistent
- Regressionstests: backend/tests/test_forex_ibkr.py (7 Tests) +
  tests/test_iter40_forex_mr_copilot_queue.py (Testing-Agent, 6/6 pass)

## Bekannte Einschränkungen
- IBKR_GATEWAY_URL in dieser Umgebung leer → Forex-Live fällt auf Paper zurück (gewollt)
- Alte pytest-Suite hat env-abhängige Altlasten-Failures (Produktions-URL/Keys)

## Backlog / Nächste Aufgaben
- P1: Partial-TP1/Break-Even für IBKR-Live-Forex (OCA-Gruppen)
- P1: IBKR-Kontostand/Margin im Dashboard (USDT-Anzeige ist Bitunix-only)
- P2: Warteschlangen-Strip auch im Backtester/Regime-Lab
- P2: Ergebnis-Vergleichsansicht (mehrere Queue-Ergebnisse nebeneinander)
