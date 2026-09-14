# PRD – KI-Trader (externe Daytrading-Website, Render-Deploy)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (Repo dean06greif-ai/KI-Trader) soll
verbessert werden: sauber, modular, rückwärtskompatibel, Originalstruktur beibehalten
(Render-Deploy). Verbindliche Quelle: Plan-Dateien im Repo (`KI_TRADER_AUDIT.md`,
`UMSETZUNGSPLAN_LIVE_QUALITAET.md`), lebendes Protokoll `UMSETZUNG_FORTSCHRITT.md`
(nach jedem Schritt aktualisieren – Nutzer pusht via Emergent "Save to GitHub").

## Architektur
- Backend: FastAPI (`backend/server.py`, routers/, services/, core/), MongoDB (Motor)
- Frontend: React (frontend/src/components), Trading-Dashboard (Scanner, KI-Trader, Analyse)
- Extern: Bitunix (Futures), IBKR via ibeam-Gateway, OpenRouter/Groq/Mistral/Gemini (LLM),
  Telegram, Supabase. Deploy: Render (Python 3.11). Lokal: lokale Mongo, keine Exchange-Keys,
  `AI_TRADER_LOCAL_DISABLE=1`.
- Test-Suiten: `backend/tests` (`-m unit`, xdist -n 2) + Root-`/tests` (T1, pytest-fähig)

## Nutzer-Persona
Einzelner Betreiber (Admin) – handelt live/paper mit KI-Unterstützung, will Stabilität,
ehrliche Messung und nachvollziehbare Policy-Entwicklung.

## Umgesetzt (Stand 26.06.2026)
- Phase 1 Geldschutz (1.1–1.9) ✅ · Phase 2 Ehrliche Messung (2.1–2.10) ✅
- Phase 3 Champion vs. Kandidat (3.1–3.5) ✅ · Phase T Test-Hygiene (T1–T4) ✅ → **Audit-Plan komplett**
- Diese Session: Repo-Import Branch `conflict_140926_1702`, Baseline bestätigt,
  Testing-Agent Smoke iteration_62 (15/15 grün), **T2** GitHub-Action, **T3** Prod-Probe,
  **3.5 Portfolio-Backtest** (services/portfolio_backtest.py + /api/portfolio-backtest/* +
  PortfolioBacktestCard im Backtester; 10 Unit-Tests, Testing-Agent iteration_63 100% grün,
  Unit-Suite 1403 passed).

## Offen / Backlog (priorisiert)
- P0: Nutzer-Push via "Save to GitHub"; Phase-0-Punkte beim Nutzer (JWT_SECRET auf Render,
  Key-Rotation, Bitunix-IP-Whitelist)
- P1: 2–4 Wochen MESSPHASE der Live-Ausführungsparameter (A+B-Schwellen einfrieren; Strategie-
  Suche/Backtests/Policy-Lab/Website-Arbeit weiterhin erlaubt), danach Auswertung
  slippage-stats + Policy-Report
- P2: Entscheid Option 3 (1m-Hybrid-Trigger) nach Messphase

## Detail-Protokoll
Siehe `/app/UMSETZUNG_FORTSCHRITT.md` (Quelle der Wahrheit, wird je Schritt fortgeschrieben).
