# PRD – KI-Trader (Daytrading-Website)

## Original-Problemstellung
Bestehende, produktiv auf Render laufende Daytrading-Website (Repo: dean06greif-ai/KI-Trader, Branch conflict_220826_1359).
Grundsatz: Verbesserungen sauber, modular, rückwärtskompatibel in die bestehende Architektur einpflegen;
Originalstruktur (Ordner/Dateien) beibehalten für externes Render-Deployment. Vor größeren Änderungen
Architektur analysieren, Risiken identifizieren, Regressionstests erstellen.

## Architektur
- Backend: FastAPI (`backend/server.py` als App-Assembly), Router pro Bereich in `backend/routers/`,
  Services in `backend/services/`, Hintergrund-Loops in `backend/core/scheduler.py`, Zustand in `core/state.py`.
- Frontend: React (CRA/craco), Komponenten in `frontend/src/components/`.
- DB: MongoDB (produktiv Atlas, Preview lokal). Trading: Bitunix Futures API.
- Preview-Sicherheit: Bitunix-Keys hier absichtlich NICHT gesetzt → 0 Live-Calls; produktiv läuft auf Render.

## User-Persona
- Einzelner Trader (Admin) mit Philosophie „Hebel maximal, Risiko über Marge + SL steuern".
- KI-Trader-Engine handelt automatisiert; Lektionen-System lernt, Hebel-Deckel-Lektionen sind blockiert.

## Kern-Anforderungen (statisch)
- SL muss IMMER mit Abstand vor der Liquidation liegen (auto_lev_value = wichtigster Sicherheitsparameter).
- Fee-Wächter: SL-Distanz muss Roundtrip-Fees realistisch einpreisen (mult × Fees, ATR-Rauschband, CRV-Relax).
- Gewinnsicherung pro Trade/Strategie: Trigger-% (min. 5%), Gewinn-Lock-%, Marge-Freisetzung mit Reglern
  (Marge-Reduktion 10–100%, Ziel-Hebel bis Coin-Max), SL wird automatisch hinter die neue Liq gezogen,
  Deferral wenn SL zu nah am Kurs läge.

## Umgesetzt (mit Datum)
### Frühere Sessions (bis 22.08., im Repo gepusht)
- Multi-Positions-Fix (Watchdog: getrennte Trades pro Bitunix-Position-ID, Hedge Long+Short).
- Lektions-Qualität: Hebel-Deckel-Lektionen maschinell blockiert, Richtungs-Lektionen brauchen Marktkontext, Verfallsdatum.
- Gewinnsicherung Backend: `profit_release_plan`, `sl_liq_guard`, `_release_secured_margin` (retry-fähig),
  Trigger-min-5%-Clamp, Fee-Wächter V3.

### Diese Session (22.06.2026) – Fortsetzung nach Abbruch
- Diskrepanz 14 vs. 15 Tests geklärt: Zählfehler, kein fehlender Test.
- **Coin-Max-Hebel-Integration** (Kern der Session):
  - `load_trading_pairs` cached jetzt `maxLeverage` pro Kontrakt; neuer Helper `max_leverage_for(symbol)`.
  - Ziel-Hebel der Gewinnsicherung, `adjust_leverage` und der Entry-Hebel (`lev_used`) werden am
    Coin-Max-Hebel gedeckelt (z.B. BNB=75x, XRP=100x) statt pauschal 200x.
  - Neuer öffentlicher Endpoint `GET /api/autotrade/coin/{symbol}/meta` → `max_leverage`.
- **StrategyAutoTradeModal** (das aktiv genutzte Modal!) um die Gewinnsicherungs-Regler ergänzt:
  Trigger min=5, Marge-Reduzieren-Slider (10–100%, `sat-ps-margin-reduce`), Ziel-Hebel-Slider bis Coin-Max
  (`sat-ps-max-lev`), Haupt-Hebel-Slider + Auto-Lev-Max ebenfalls bis Coin-Max, Schutz-Hinweistext.
- Strategie-Defaults (`core/defaults.py`) um `profit_secure_release_margin/max_leverage/margin_reduce_pct/sl_liq_buffer_pct` ergänzt.
- 6 vorbestehende, veraltete Unit-Tests ans gewollte neue Verhalten angepasst (Watchdog-Fakes mit `.find()`,
  lev_auto_max-Clamp 200, Token-Budget 7000, include_manual-Default). Baseline-Vergleich bestätigte: vorbestehend, keine Regression.
- 2 neue Regressionstests (Coin-Max-Deckel + Fallback ohne Katalog).
- Verifikation: 106/106 gezielte Backend-Tests grün; Testing Agent: Backend 87/87 + Meta-Endpoints, Frontend 8/8 inkl. Persistenz.

## Bekannte Einschränkungen / vorbestehende Test-Fails (nicht Teil dieser Session)
- ~70 E2E-Tests der vollen Suite schlagen umgebungsbedingt fehl (Local Worker offline, Live-Endpoints/Timeouts,
  API-Rate-Limits) – identisch im unveränderten Baseline-Clone, keine Regression.
- `AutoTradeModal.js` ist Legacy (nicht in App.js gemountet), enthält aber dieselben Regler (synchron gehalten).

## Backlog / nächste Aufgaben
- P1: Legacy `AutoTradeModal.js` entweder wieder einbinden (Coin-Level-Konfig) oder nach Rücksprache entfernen.
- P1: E2E-Testsuite in Umgebungs-Marker aufteilen (pytest markers: live/worker/unit), damit CI nur Unit läuft.
- P2: Coin-Max-Hebel auch im Backtester (`effective_leverage`) berücksichtigen (aktuell nur Live-Pfad).
- P2: Funding-Fees in den Fee-Wächter einbeziehen (aktuell nur Taker/Maker-Roundtrip).
