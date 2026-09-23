# PRD – KI-Trader Verbesserungen (23.09 / Session E1)

## Ausgangslage
Produktive, extern auf Render deployte Daytrading-App (FastAPI + React + MongoDB). Repo: dean06greif-ai/KI-Trader, Branch conflict_230926_1428, 1:1 nach /app geklont (Struktur unverändert). Lokale Tests nur gegen lokale MongoDB (crypto_scanner_dev). Einmalig: rein lesende Diagnose der Produktions-DB (vom Nutzer erlaubt).

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
