# PRD – KI-Trader (Daytrading-Website, extern auf Render deployt)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (GitHub: dean06greif-ai/KI-Trader, Branch conflict_200826_2251) verbessern – sauber, modular, rückwärtskompatibel, Original-Ordnerstruktur beibehalten (Render-Deploy):
1. KI-Trader Training soll Lese-Zugriff auf andere Strategien (Trendfolge, Scalping) haben: Parameter, Ergebnisse, Lernnotizen – nur als Lernkontext.
2. KI-Trader soll aus dem Trainingslager selbst neue Setups/Strategien entwickeln können.
3. Komplette Website auf korrekte Berechnungen prüfen (PnL, Fees etc.).
4. Frage: Wie viele Cerebras-Backup-Keys braucht der Analyst?
5. Gewinnsicherung: Beim Marge-Freisetzen soll der Hebel steigen; Optionen (Häkchen) für Hebel-Max/Marge maximal reduzieren.
6. Runner-Option: Nur Teil-TP, Rest maximal laufen lassen, mit SL im Gewinn absichern + Marge reduzieren/Hebel max = risikofreier Trade.

## Architektur (unverändert)
- FastAPI-Backend (backend/) + React-Frontend (frontend/) + MongoDB, Render-Deploy, local_worker/ für Heim-PC-Rechenjobs.
- Struktur 1:1 beibehalten; /app spiegelt das Repo-Root.

## Umgesetzt (21.06.2026 / Iteration)
1. **services/strategy_insights.py (NEU)**: Read-only Cross-Strategie-Kontext (Parameter je Registry-Strategie, echte Paper/Live-Ergebnisse aus auto_trades, Optimizer-Best aus optimizer_runs, Erkenntnisse aus learning_memory, Labor-Lernnotizen). Injiziert NUR in Lern-/Trainings-Prompts:
   - ai_learning.run_learning (Lernlauf)
   - ai_strategy_lab.assist (Strategie-Beratung)
   - ai_strategy_lab.develop (neu)
2. **Trainingslager-Entwicklung**: StrategyLab.develop() – KI entwickelt selbst neue Setups (DEVELOP_SYSTEM-Prompt, research_analyst-Rolle), Kandidat startet als Ghost, normale Promotion-Pipeline. Endpoint POST /api/ai/strategies/develop (Admin) + Button im Strategie-Labor-Panel (data-testid strategy-develop-btn). Live getestet: erzeugt validen Ghost-Kandidaten mit learned_from.
3. **Bugfix secure_profit (KI-Trade-Manager)**: in_profit wurde nie an check_limits übergeben (secure_profit immer blockiert, Profit-Lock-Hebeldeckel griff nie) UND es fehlte der Ausführungszweig. Jetzt: uPnL zum Mark-Preis entscheidet; secure_profit entnimmt X% der Margin via adjust_margin.
4. **Gewinnsicherung + Marge freisetzen**: adjust_margin erhöhte Hebel schon korrekt. NEU pro Trade-Config: profit_secure_release_margin (Checkbox) + profit_secure_max_leverage (2–200, 200 = maximal reduzieren). Beim Auslösen der Gewinnsicherung (SL im Gewinn) wird Marge freigesetzt, Hebel steigt, Liq-Preis aktualisiert; live erst nach Börsen-Bestätigung. UI: AutoTradeModal + StrategyAutoTradeModal.
5. **Runner risikofrei stellen**: Engine-Config runner_secure_enabled (Default an), runner_secure_trigger_pct, runner_secure_max_leverage. Runner-Trades (ai_runner=true, nur Teil-TP + Trailing) bekommen automatisch profit_secure(+release). Prompts erweitert (KI nutzt runner nur bei echtem Potenzial). UI-Toggles im KI-Setup (ai-runner-secure-toggle, ai-runner-secure-maxlev-select).
6. **Fixes aus Audit**: watchdog/status liefert Zählerfelder immer (stabiler API-Vertrag); hold-Aktion crasht nicht mehr ohne autotrader und ist für fremde Strategie-Trades erlaubt (Guards für manuell/extern/Datensammel bleiben); .gitignore vom Repo übernommen (local_worker-Whitelist).
7. **Regressionstests NEU**: tests/test_strategy_insights.py (10 Tests), tests/test_profit_release_and_secure_profit.py (11 Tests). Suite: 1669+ passed; verbleibende Fails sind umgebungsbedingt (lokaler Worker offline, Prod-Datenbestand, LLM-Rate-Limits) – analysiert, keine Rechenfehler gefunden (PnL/Fee/Breakeven/Liq-Formeln geprüft: korrekt, Exchange-Truth hat Vorrang).

## Cerebras-Antwort (dokumentiert)
- Analyst nutzt Cerebras nur als 1. Fallback (primär Groq, 2. Fallback Gemini). Limits gelten pro KONTO, nicht pro Key. 2–3 Keys aus VERSCHIEDENEN Konten reichen völlig; weitere Keys desselben Kontos bringen nichts.

## Backlog / Nächste Schritte
- P1: E2E-Test der neuen UI-Flows via Testing-Agent (Admin-Panel: Runner-Toggle, Labor-Button, Trade-Modal-Checkboxen)
- P1: Optimizer-Best im Insights-Block: Symbol fehlt bei manchen Runs ("?")
- P2: Übersicht/Badge im Trade-Detail, wenn Marge freigesetzt wurde
- P2: learning_memory-Notizen um menschenlesbare Zusammenfassung erweitern
