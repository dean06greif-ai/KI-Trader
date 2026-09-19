# KI-Trader – Prüfung & Verbesserung (externes Repo, Render-Deploy)

## Original-Problemstellung
Bestehende, produktive Daytrading-Website (GitHub dean06greif-ai/KI-Trader, Branch
`conflict_190926_2225`) sauber verbessern, Ordnerstruktur für Render beibehalten:
1. Rest-Kapital-Trading (50 $ gewünscht, 22 $ frei -> mit 22 $ traden) prüfen.
2. Token-Verbrauch des Trade-Managers erklären; Bewegungsscanner ohne KI?
3. Lernen aus verpassten Bewegungen (Setup-Lücke, Momentum-News, autonome Setups).
4. Setups (Session Open, Momentum News, …) werden trotz Backtest-Edge nie getradet –
   Sicherheitsmechanismen prüfen.

## Arbeitsweise
- Repo nach `/app/KI-Trader` geklont, Änderungen lokal committet (a521527); Nutzer pusht/deployt.
- Prod-MongoDB nur LESEND analysiert (ai_token_usage, ai_playbook_state, ai_decisions …).

## Befunde (19.09.2026)
- Rest-Kapital: `services/capital_fit.py` vollständig für Bitunix (Entry, Limit, Retry).
- Tokens: analyst 57 Mio/10 T (free-Modell), trade_manager 4,8 Mio (233 Calls/Tag ohne
  Smart-Skip, im Branch bereits `trade_review_gate.py`), Move-Scanner LLM-frei bis 12/Tag.
- Root Cause "Setups nicht getradet": alle Klassen `live_blocked` mit "Revision v1:
  Validierung neu gestartet", 0 Paper-Trades seit Revision; Backtest-Seeding für
  rückgestufte Setups ausgeschlossen -> Dauer-Blockade.

## Umgesetzt (19.09.2026)
- `setup_lifecycle.revision_demotion()`, `kind: revision` in `revise_setup`.
- `ai_playbook._refresh_scope`: Revisions-Rückstufung per gewichtetem Backtest-Edge
  + ≥2 profitablen Paper-Trades aufheben (`bt_validated`), Seeding zählt weiter;
  Schwäche-Rückstufungen unverändert.
- `setup_trigger.paper_wanted()`: Paper-Sammel-Trades für rückgestufte Setups.
- `bitunix_trade.on_signal`: Rest-Kapital-Fit vor IBKR-Orders.
- `ai_move_scanner`: Antwortsprache Deutsch.
- Tests: `backend/tests/test_revision_backtest_lift.py` (9 grün), Suite ohne Regression.

## Backlog / offen
- P1: Branch deployen (Smart-Skip + Setup-Trigger + diese Fixes sind in Prod noch nicht aktiv).
- P2: `collection_max_same_direction`(5) ggf. anheben, wenn Setup-Trigger viele Paper-Trades liefert.
- P2: Trade-Manager-Prompt weiter verschlanken (MasterPrompt-Block gekürzt).
- P3: Custom-Setups des Move-Scanners haben keinen Detektor (nur LLM-Erkennung).
