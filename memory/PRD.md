# PRD – Neiekeeke Daytrading Website (externes Repo, Render-Deploy)

## Original Problem Statement
Bestehende, produktive Daytrading-Website (GitHub: dean06greif-ai/Neiekeeke, deployed auf Render, bleibt extern). Drei Verbesserungen:
1. KI-Trader macht nur Datensammel-Trades, keine Live-Trades ("kein verfügbares Kapital")
2. Mobile-App-Bug: an bestimmter Stelle kein normales Hochscrollen möglich (muss von ganz oben wischen)
3. Bitunix-Live-Anzeige ungenau: Margin reduzieren/Hebel erhöhen mitten im Trade wird nicht erkannt; Partial-SL/TP nicht akkurat; freies Kapital falsch (teils negativ) → blockiert KI-Live-Trades

Grundsätze: Originalstruktur 1:1 erhalten (Render), sauber/modular, Regressionstests, Rückwärtskompatibilität.

## User-Entscheidungen
- Repo klonen, direkt darin arbeiten, Original-Struktur erhalten
- Alle 3 Punkte in einem Durchgang
- Bitunix-API als Source of Truth fürs freie Kapital
- Delivery: User pusht selbst (Patch im Chat geliefert, Branch `fix/kapital-sync-mobile` lokal in /app/Neiekeeke committet)

## Architektur (Bestand)
- FastAPI-Backend (`backend/server.py` + routers/ + services/), React-Frontend, MongoDB (Atlas), Render-Deploy
- Kern-Services: `services/bitunix_trade.py` (AutoTradeManager), `services/ai_engine.py` (KI-Trader), `services/position_watchdog.py`
- Eigene pytest-Suite (~1719 Tests) in `backend/tests/`

## Implementiert (Juni 2026)
1. **Kapital-Fix (Root Cause für "keine Live-Trades")**: Neue `free_capital_status(mode)` in AutoTradeManager – LIVE nutzt Bitunix-API (echte Positions-Margin via `live_exchange_used_margin()` + `available` aus `_live_balance_fields()`), DB nur als Fallback. Verwendet in: execute()-Kapital-Gate, `_free_capital_ok`, AI-Prompt `_capital_risk_block`, Endpoints `/api/autotrade/balance` + `/api/autotrade/capital` (neue Felder `available`, `source`).
2. **Watchdog-Börsen-Sync**: `_sync_local_state()` spiegelt Hebel-, Margin- und Mengen-Änderungen (Partial-TP/SL, Teilschließungen) der Börse in den lokalen Trade (Event-Log "BÖRSEN-SYNC"). Misch-Positionen (manuelle Aufstockung) werden weiterhin nicht angefasst; qty wird nie erhöht. `_enrich_trade` bevorzugt Live-Börsen-Margin/Hebel (sofortige Anzeige, 10s-Cache).
3. **Mobile-Scroll-Fix**: mobile.css – verschachtelte Scroller (.app-layout, .right-panel 60vh, .performance-analytics) auf Mobile entfernt → ein Seiten-Scroller; Coin-Sidebar bleibt intern scrollbar mit overscroll-behavior contain.
4. Neue Regressionstests: `backend/tests/test_fix_capital_truth_and_sync.py` (10 Tests).

## Testing
- 52/52 gezielte Regressionstests bestanden; komplette Suite gegen Baseline (Original-Code) verglichen: identische Fehlermengen (rein umgebungsbedingt: fehlende API-Keys/Worker) → keine Regressionen
- Testing-Agent iteration_1.json: 100% Backend, keine Issues

## Delivery-Status
- Änderungen liegen in /app/Neiekeeke auf Branch `fix/kapital-sync-mobile` (lokal committet, 90fad11)
- Patch: /app/neiekeeke_fixes_0626.patch (auch komplett im Chat gepostet); User wendet ihn selbst an und pusht

## Backlog / Nächste Schritte
- P1: Nach Deploy auf Render live verifizieren (Bitunix-Keys vorhanden → source='exchange' in /api/autotrade/balance)
- P1: Mobile-Scroll auf echtem iPhone/Android verifizieren
- P2: Frontend-Anzeige der neuen Felder `available`/`source` (Badge "Live von Bitunix")
- P2: Watchdog-Sync-Intervall ggf. verkürzen für noch schnellere Anzeige
