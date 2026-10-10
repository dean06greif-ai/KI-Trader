# PRD – MarketMaker / KI-Trader (extern auf Render)

## Original Problem Statement
Bestehende, produktive Daytrading-Website (Repo dean06greif-ai/KI-Trader, Branch conflict_081026_2009,
Render: backend/ + frontend/ + local_worker/ + ibeam_gateway/). Verbesserungen sauber, modular,
rückwärtskompatibel, Originalstruktur bleibt. Aufgaben: Korrelations-Analyse über lokalen Worker;
KI-Trader tief analysieren und optimieren; Regime-Lab-Frage (3 Coins vs. je Coin) + Anleitung;
MongoDB aufräumen/optimieren.

## User Choices
- Korrelation: Fokus lokale Kerzen statt Cloud-Download; sinnvolle Verbesserungen erlaubt
- Echte Orders erlaubt (nicht genutzt – keine Order-Logik geändert)
- MongoDB: alte Logs/Caches automatisch löschen
- Regime: nur Anleitung + Analyse
- Rückweg: Save to GitHub, Deploy auf Render durch den Nutzer

## Implemented (10.10.2026)
- Asset-Korrelation lokal: Worker 1.24.0 fn="correlation", Router execution=local + Ziel-Worker, Server-Persistenz, UI-Kennzeichnung
- MongoDB: Zeit-Indizes (Sort-Memory-Fehler der Retention behoben), Verschlankung Archiv/HOLD-Entscheidungen,
  Trade-Exporte gedeckelt, Champion-Analysen geschützt; Prod 435 MB → 306 MB; 3 Analysen aus Backup wiederhergestellt
- KI-Trader: Erwartungswert-Regel (R, Shrinkage) für Live-Freischaltung/Rückstufung; Edge-Bericht in R in der Diagnose
- Docs: KI_TRADER_DEEP_ANALYSE_1010.md, REGIME_ANLEITUNG_1010.md, VERBESSERUNGEN_1010_KORRELATION_LOKAL_DB_EDGE.md
- Tests: test_correlation_local_worker.py, test_lifecycle_expectancy_and_retention_slim.py, test_ai_edge_report.py, iteration_63 (100 %)

## Backlog
- P0 (Nutzer-Einstellung): Analyst-Hauptmodell gemini-3.5-flash statt :free; live_gate_bypass AUS; fee_guard_mult 6
- P1: Partial Pooling (Gruppen-Modell + Coin-Skalen), Regime-Wahrscheinlichkeiten, BTC-Kontext für Alts
- P2: Konfidenz-Kalibrierung aus Edge-Daten; LLM als Veto statt Auslöser falls LLM-Edge negativ bleibt
- Prod-Override retention_config.overrides.regime_analyses.keep_last=15 nach Deploy optional entfernen
