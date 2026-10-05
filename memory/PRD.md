# PRD – MarketMaker / KI-Trader (Verbesserungen 10/2026)

## Original-Aufgabe
Bestehende, produktive Daytrading-Website (Repo KI-Trader, Branch conflict_041026_2030, Render-Deploy, Struktur 1:1 beibehalten) modular verbessern:
1. Ausreißer-Filter (wie im Strategie-Optimizer) auch für dynamische Strategien und Regime-Lab-Erkennung (+ strenger: wenige Trades / Ausreißer-Dominanz abwerten)
2. Restzeit im Optimizer genauer (blieb lange auf ~5 s)
3. Asset-Filter im Optimizer-Ergebnis (Insights je Asset inkl. Bull/Bär/Seitwärts)
4. RAM/Kerzen-Cache-Anzeige erklären
5. Ergebnis "Bestehende optimieren" (Kandidat mit Training -6139 übernommen) + "No module named 'telegram'" beheben
6. Realistische Limit-Order-Simulation in Backtester & Optimizer
7. (Folgewunsch) Backtester: dieselben Robustheits-Tests/Insights wie im Optimizer

## Architektur (unverändert: FastAPI backend/, React/CRACO frontend/, local_worker/)
Neue reine Module: services/limit_fill.py, services/regime_outliers.py, services/job_eta.py, services/backtest_robustness.py
Frontend neu: OptimizerAssetFilter.js, BacktestRobustness.js

## Umgesetzt (05.10.2026)
- Limit-Fill realistisch (Durchbruch statt Berührung, Gap-Fill, Stop in Fill-Kerze, Limit-Abstand, TP-Limit), Modus "touch" = alt
- Robuste Regime-Validierung (Training negativ / Ausreißer-Dominanz zählt nicht als validiert), refine ohne Verbesserung legt keine neue Version an
- Ausreißer je Regime im Ergebnis-Backtest, Ausreißer-Assets der Regime-Erkennung
- ETA: gleitendes Tempo + Stillstands-Korrektur; Optimizer-Fortschritt reserviert 90–98 % für Robustheits-Checks
- Asset-Filter Optimizer + Backtester; Per-Asset-Test-Kennzahlen und Marktphasen
- Kerzen-Cache: Auslagerung auch nach Disk-/Archiv-Hydrierung, RAM-Anzeige mit echtem Speicher
- Worker 1.22.0 (telegram optional importiert); Backtester-Robustheitstests (WF single/rolling/anchored, DD, Konstanz, Stress, MC, Marktphasen, Multi-Coin, Ausreißer)
- Regressionstests: backend/tests/test_improvements_1010_limit_outliers_eta.py (+ Testagent iter52)

## Backlog
- P1: Optimizer-UI: Auswahl Limit-Fill-Modus (aktuell Standard realistisch)
- P1: Regime-Lab: Ausreißer-Assets direkt per Klick aus gemeinsamer Erkennung herausnehmen
- P2: Backtester-Robustheit auch für Parameter-Stabilität (erfordert Neu-Simulation)
