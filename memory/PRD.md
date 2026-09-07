# PRD – KI-Trader (externe Daytrading-Website)

## Original Problem Statement
Bestehende, produktiv laufende externe Daytrading-Website (Repo: dean06greif-ai/KI-Trader, Branch conflict_070926_1333, Deploy auf Render). Verbesserungen müssen sauber, modular und rückwärtskompatibel in die bestehende Architektur eingepflegt werden. Originale Ordner-/Dateistruktur muss für Render-Deploy erhalten bleiben.

## Architektur (Bestand)
- Backend: FastAPI (`backend/server.py`), modulare Router (`backend/routers/`), Services (`backend/services/`, 100+ Module), Strategien als `BaseStrategy`-Klassen in `backend/strategies/` mit zentraler `registry.py`
- Registrierte Strategien erscheinen automatisch in: Live-Scanner, Backtester (inkl. vektorisiertem Fast-Path `fast_sim.py`), Optimizer, Paper/Live-Autotrade, Frontend-UI (dynamisch via `GET /api/strategies`)
- Frontend: React (`frontend/src`), StrategyTabs + „Strategien verwalten"-Dialog, Paper/Live-Umschalter im Header
- DB: MongoDB (env `MONGO_URL`/`DB_NAME=crypto_scanner`), Exchange: Bitunix (+ IBKR), Auth: Admin-JWT

## User Persona
Einzelner Admin-Trader (Dean), betreibt die Seite produktiv auf Render, handelt Crypto-Futures (Bitunix) paper & live.

## Core Requirements (statisch)
- Stabilität & Rückwärtskompatibilität vor aggressiven Änderungen
- Neue Features modular ins bestehende Strategie-Framework
- Originalstruktur für Render-Deploy beibehalten

## Implementiert
### 2026-06 (dieser Task)
1. **Horst VWAP + OBV-RSI Scalping Strategie** (`backend/strategies/horst_vwap_obv_strategy.py`, ID `horst_vwap_obv`)
   - Original-Regeln (Kian Horst, 1m): Kerze schließt in unterer/oberer VWAP-Range (StdDev-Bänder) + OBV-RSI <30 (Long) / >70 (Short), SL 0.6%
   - Verbesserungen (lt. Video-Backtest-Kritik: Gebühren + CRV<1:1): TP default 0.9% (CRV 1.5:1), Fee-Edge-Filter (`min_dev_pct`, Mindestabstand zum VWAP), TP1 = VWAP-Mittellinie (Horst Midline-Exit als Teilgewinn)
   - Original-Verhalten einstellbar: `tp_pct=0.6`, `min_dev_pct=0`
   - Beide Pfade: `analyze()` (Live-Scanner) + `vectorized_signals()` (Backtester Fast-Path)
   - Registriert in `registry.py`; automatisch in UI, Backtester, Paper/Live-Autotrade
   - Tests: `backend/tests/test_horst_vwap_obv.py` (10 Tests) + `backend/tests/test_horst_review_api.py` (API/E2E-Regression, vom Testing-Agent), alle grün; E2E-Backtest BTCUSDT 2d: 16 Trades, 68.8% Winrate (deckt ~70%-These aus dem Video)
2. **Polymarket 5-Min-Bot (X-Post)**: analysiert, bewusst NICHT implementiert (User-Entscheidung). Empfehlung siehe Backlog/Chat: reines Prediction-Market-Konstrukt (binäre 5-Min-Kontrakte), nicht 1:1 auf Bitunix-Futures übertragbar; adaptierbar wäre nur die Momentum-Timing-Logik.

## Backlog (priorisiert)
- P1: Optional – „BTC 5m Momentum-Into-Close"-Strategie (adaptierte Polymarket-Logik auf Bitunix-Futures: Einstieg in letzten 2 Min einer 5m-Kerze bei ≥70-100$ Bewegung, mit dem Trend)
- P2: Pre-existierende React-Console-Warning beheben (`<span>` in `<option>` im „Strategien verwalten"-Dialog, rein kosmetisch, bestand schon vorher)
- P2: Cooldown-Regel „10 Kerzen nach Verlust-Trade" für Horst-Strategie (stateful, bräuchte Trade-Manager-Anbindung)

## Test-Zugang
Admin / Dean06Greif!/Admin (siehe /app/memory/test_credentials.md)
