# PRD – KI-Trader (externe Daytrading-Website, Render-Deployment)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader,
Branch conflict_260826_1915) verbessern – sauber, modular, rückwärtskompatibel,
Original-Ordnerstruktur beibehalten (Render-Deployment):
1. Neuer Coin: Hyperliquid (HYPE/USDT)
2. Probleme beim Speichern von Parameteroptimierungen / neuen Strategien beheben
3. Alle Strategie-Ausarbeitungs-Funktionen (Endlos-Suche, dynamische Strategien …) prüfen/bewerten
4. Eigenständige Strategie-Bau-KI (Chat bei den Einstell-Panels), getrennt vom KI-Trader,
   aber mit Wissensaustausch; ändert nur nach Bestätigung; bewertet Ergebnisse
   (Nutzerwahl: NUR OpenRouter mit Fallbacks, klar getrennt)

## Architektur
- Backend: FastAPI (`backend/server.py` + `routers/` + `services/` + `strategies/` + `core/`), MongoDB (Motor)
- Frontend: React (CRA/craco), Komponenten unter `frontend/src/components/`
- KI-Trader: `services/ai_engine.py` (Multi-Provider), NEU getrennt davon:
  Strategie-Copilot `services/strategy_copilot.py` + `routers/copilot.py` (nur OpenRouter)
- Brücke der KI-Systeme: geteiltes Gedächtnis `ai_knowledge` (Copilot schreibt `copilot_note`, liest lesend KI-Trader-Erkenntnisse)

## Umgesetzt (2026-06)
1. **HYPEUSDT** in `core/instruments.py` (Historie+Live von Bitunix, da nicht auf Binance-Spot); erscheint in Sidebar/Optimizer/Backtester
2. **Speicher-Fix (Race-Condition)**: `scanner.save_settings()` in `services/strategy_scanner.py` –
   gezielte Persistenz nur geänderter Top-Level-Keys statt Komplett-Überschreiben von
   `scanner_settings` ($set: scanner.settings). Umgestellt: `routers/strategies.py` (create/duplicate/
   delete/restore/import), `routers/optimizer.py` (apply coins/global/strategy), `routers/general.py`
   (POST /api/settings, meldet jetzt `ignored_keys`), `services/dynamic_live.py`, `services/ai_strategy_lab.py`
3. **Strategie-Copilot** (eigene KI, nur OpenRouter mit Backup-Keys + Modell-Fallback,
   Zeitbudget 28s/Modell, 52s gesamt wegen Render/Ingress-Limit):
   - Endpoints: `/api/copilot/status|model|history|chat|apply|review`
   - Chat-Panel `StrategyCopilot.js` eingebunden in StrategyBuilder + Optimizer
   - erkennt aktuelle Einstellungen (Kontext: Entwurf, Optimizer-Settings, letztes Ergebnis, Registry, aktive Params)
   - Vorschläge (definition/params) werden erst nach Nutzer-Bestätigung angewendet (gleiche Validierungs-/Speicherpfade wie manuelle Endpoints)
   - deterministische Ergebnis-Sanity-Checks (Trade-Summe, Win-Rate, Ø-PnL, Drawdown)
4. Tests: iteration_27 (16/17) + iteration_28 (alles grün); pytest `backend/tests/test_iter27_copilot_hype_settings.py`

## Bewertung Such-Funktionen (Analyse)
- Parameter-Optimierung (random/optuna): Feintuning bestehender Strategien
- Discovery/Greedy: schnell, findet keine Regel-Synergien
- Deep-Test (deep/extreme, Beam-Suche): beste Qualität für neue Kombis, langsam
- Endlos-Suche (deep_explore): robustestes Verfahren (Training + Walk-Forward-Gate) → beste Wahl für neue Strategien
Empfehlungslogik ist im Copilot-Systemprompt hinterlegt.

## Offen / Backlog
- P1: Copilot auch im Dynamik-Panel & Backtester einbinden
- P1: Copilot-Modellwahl im UI (Endpoint /api/copilot/model existiert bereits)
- P2: `<span>` in `<option>` Hydration-Warning (Altbestand, kosmetisch)
- P2: GET /api/ai/memory Lese-Endpoint für Gedächtnis-Einsicht

## Hinweise Deployment (Render)
- Struktur unverändert; lokale .env nutzt lokale Mongo, auf Render weiterhin Atlas via ENV
- Neue Dateien: backend/services/strategy_copilot.py, backend/routers/copilot.py,
  frontend/src/components/StrategyCopilot.js/.css – keine neuen Dependencies
