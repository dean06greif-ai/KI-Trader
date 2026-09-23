# PRD – KI-Trader Verbesserungen (Session 06/2026, Branch conflict_230926_1748)

## Ausgangslage
Produktive, extern auf Render deployte Daytrading-App (FastAPI + React + MongoDB). Repo 1:1 nach /app geklont (Struktur unverändert). Preview nur gegen lokale MongoDB (crypto_scanner_dev); Prod-DB ausschließlich lesend für Diagnose (scripts/prod_*_probe.py).

## Umgesetzt (2026-09-23, Session E1 – Teil 4)
- VWAP im Prompt: services/vwap_context.py – je Asset Tages-VWAP-Abstand (% + ATR5m), 5m-Seitenwechsel, „frischer VWAP-RECLAIM/-VERLUST“ bzw. „überdehnt“; Regel im Systemprompt (full + lean); vwap_reclaim-Beschreibung gekürzt
- KI-eigene Setups: Setup-Enum im Prompt-Schema jetzt dynamisch inkl. custom Setups (vorher statisch -> 0 Entscheidungen für KI-Setups)
- Setup-Diagnose (Prod read-only): Hauptblocker Richtungs-Guard (globale Zählung über alle Klassen) + stiller Sammel-Cooldown -> Sammel-Richtungslimit je Anlageklasse, Sammel-Cooldown je Symbol×Setup, Cooldown-Gründe protokolliert; neuer Setup-Trichter (GET /api/ai/setup-usage + SetupUsagePanel im Verlauf)
- Verlauf: Echtgeld / Paper / Live-Logik gesamt / Sammlung getrennt (equity-curve modes real|paper|live|collection + breakdown), Setup-Reife-Spalte je Welt (paper_*/coll_*), Bugfix globale Tabelle Sammel-Spalte leer bei Varianten
- Mindest-Trade (nur Echtgeld-Live, Bitunix): services/min_trade.py – statt Ablehnung an Kapital-Grenze/Risikobudget kleinste handelbare Position (Ziel-Marge 1 USDT, min. Börsen-Minimum, Risiko <= 3 % Equity, max. 3 offen, echtes freies Guthaben); Paper/Sammel unverändert; Einstellungen im Kapital-Modal (Live); Trade-Flag min_trade
- Telegram: Katalog NOTIFY_CATALOG (gruppiert, Unter-Schalter live/paper, Signale Scanner/KI, Order-Abbruch Börse vs. intern, Mindest-Trade, Confluence, bisher nicht abschaltbare Typen) + UI TelegramNotifySettings; Abbruch-Meldung unterscheidet „Interne Prüfung“ vs. „Bitunix hat abgelehnt“
- Strategie-Optimizer: Ausreißer-Assets (services/optimizer_outliers.py, robuster z-Wert, max. ⅓, Rest profitabel) -> Option B „übernehmen & Asset AUS“ (apply exclude_symbols) oder „ohne Ausreißer neu optimieren“; Empfehlungs-Ampel je Top-Ergebnis (farbig statt dunkel-transparent); Score-Text statt -500000000; win_rate-Ziel bevorzugt nie Verlust-Kandidaten; Endlos-Suche: „positiv“ = Filter bestanden + echter Gewinn, Champion braucht Mindest-Test-Trades + Test-DD
- Tests: tests/test_session_0626_improvements.py (17), backend/tests/test_iter21_session_0626.py (Testing-Agent, 11); zwei veraltete Tests an neues Verhalten angepasst
- .gitignore: backend/.env, frontend/.env, yarn.lock (öffentliches Repo)

## Backlog
- P1: Portfolio-Drawdown (zeitlich kombiniert) statt Summe der Einzel-DDs im Optimizer (aktuell konservativ)
- P1: Konfidenz-Kalibrierung (momentum_news meist 60 % < Live-Schwelle 65 %)
- P2: Detektoren für KI-eigene Setups (derzeit nur LLM-Entscheidungen/Sammeltrades)
- P2: Vorbestehende rote Tests: test_history_days_cap, TestNormalizeDefinition, test_resolve_forex_falls_back_to_yahoo_without_ibkr, ap13 Ablation

---
# PRD – KI-Trader Verbesserungen (23.09 / Session E1)

## Ausgangslage
Produktive, extern auf Render deployte Daytrading-App (FastAPI + React + MongoDB). Repo: dean06greif-ai/KI-Trader, Branch conflict_230926_1428, 1:1 nach /app geklont (Struktur unverändert). Lokale Tests nur gegen lokale MongoDB (crypto_scanner_dev). Einmalig: rein lesende Diagnose der Produktions-DB (vom Nutzer erlaubt).

## Umgesetzt (2026-09-23, Teil 3)
- LINKUSDT + SUIUSDT zentral in core/instruments.py (Scanner, Regime-Lab, Backtester, KI-Trader, Liquidity-Panel); KI-Trade-Modus je Coin standardmäßig „off“
- Neues Setup vwap_reclaim (5m, Tages-VWAP, nie gegen 1h-Trend) mit Detektor → Backtests + automatische Detektor-Sammeltrades (Shadow); Forex ausgeschlossen
- Kapital je Setup × Asset schrittweise gedrosselt (×0.75 → ×0.5 → ×0.25 → ausgesetzt), Stufenwechsel als Verlauf im AI-Trading-Panel (AssetCapitalLog)

## Umgesetzt (2026-09-23, Teil 2)
- Regime je Horizont-Band: pro Anlageklasse bis zu 2 Freigaben (Intraday ≤1h für Scalps, Swing ≥2h für Swing-Trades), eine einzelne Freigabe gilt weiter für alles; Prompt, Gate „Lab“, Entry-Snapshot und Shadow-Nachweis (structural_aid) je Band
- Telegram + Website-Glocke „Regime bereit für Wirksam“ (1× je Freigabe, Toggle regime_release_ready)
- Break-Even-SL puffert Exit-Slippage (30/410 BE-Stops endeten trotz Kurs im Plus als Mini-Verlust)
- Setup-Review (Prod read-only): 97 % der KI-Trades sind Scalps; Konfidenz 60 = LLM-freie Detektor-Sammeltrades; Indizes/Forex durchgehend schwach (Konter-Trend-Shorts in QQQ-Aufwärtstrend) → Lebenszyklus hält sie korrekt im Paper

## Umgesetzt (2026-09-23)
- Regime-Qualität: neue Note „sehr gut“ mit transparentem Benchmark (Holdout ≥200 Kerzen, Referenz-Holdout ≥72 %, Live=Final ≥80 %, Lag ≤ ⅓ Phase, verpasst ≤15 %, Ø Phase 5–15 d, Validierung ok) + Checkliste in der UI
- Shadow → Wirksam: dynamische Stichprobe je Note (sehr gut 10 · gut 15 · mittel 25 · sonst 30) + manueller Override (Risiko-Häkchen + Grund, History/Audit „MANUELL“; Wirksam erst ab „mittel“, „erst Shadow“ bleibt Pflicht)
- Autopilot: Sweet Spot Min/Max (Standard 5–15 d, Strafe auch für zu lange Phasen), Referenz zu 50 % im Score, Robustheit = Nähe zum Sweet Spot; Fortschritt mit 1 Kommastelle
- Fix: gespeicherte Regime-Analyse „eins versetzt“ (lokaler Worker: status done erst nach Persistenz)
- Fix: Setup-Reife zeigte 0 Trades (schwache Auto-Tunings setzten die Variante zurück); Tabelle zeigt zusätzlich „/ ges. N“
- Tests: backend/tests/test_regime_improvements_2309b.py (+ Variante-Test)

## Backlog
- P1: Konfidenz-Kalibrierung prüfen (momentum_news-Entscheidungen meist 60 % < Live-Schwelle 65 %)
- P2: Timeframe-spezifisches Struktur-Regime je Setup-Timeframe
- P2: Benchmark-Schwellen im UI konfigurierbar
