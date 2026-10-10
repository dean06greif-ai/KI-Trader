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

## Iteration 11.10.2026 – Signal-Broker „Detektor schlägt vor, KI entscheidet“ (Branch conflict_101026_1409)
User Choices: Save to GitHub (neuer Branch); Prod-DB lesen/schreiben erlaubt (nur lesend genutzt);
Live nur bei Detektor-Signal, KI-freie Trades nur Paper + getrennt gemessen; Fenster je Setup sinnvoll;
Sperren: harte Risiko-Guards bleiben, weiche -> Hinweise/gemessen (Default, Frage übersprungen).
- services/signal_broker.py (Signal-Fenster je Setup, Matching signal_id/Symbol+Seite+Setup, Timing, KI-früh, Prompt-Block)
- services/source_stats.py (Statistik regel/ki_geprueft/ki_frei in R, Reife: erste 5 KI-geprüfte = Paper, dann Erwartungswert)
- services/confidence_calibration.py (Konfidenz-Stufen an echten R geeicht, Prompt-Rückmeldung)
- services/custom_detector.py (Regel-Detektoren für KI-eigene Setups, LLM-Übersetzung der Beschreibungen)
- services/fee_guard_widen.py (Paper-Sammeltrades: SL erweitern statt Block – 420 Blocks/7 T)
- ai_engine: Gate-Lücke (kein Setup = kein Live), Broker-Live-Gate, getrennte Welten (Dup/Richtung/Cooldown), Smart-Skip aus bei offenen Signalen
- setup_trigger: Regel-Paper für alle Treffer, Fenster registrieren, gezielte KI-Prüfung je Treffer (60/Tag)
- Lab: MIN_TRADES_SHARE 0.4->0.3, Deckel 60/40->48/30
- UI: KI-Labor > Adaptiv > Signal-Broker-Karte; Quelle-Tag in der Entscheidungsliste
- Doku: KI_TRADER_SIGNAL_BROKER_1010.md; Tests: tests/test_signal_broker.py

## Iteration 10.10.2026 (b) – Historien-Lücken, Doku-Aufräumen, Worker-Auto-Update (Branch conflict_101026_1546)
Auftrag (Nutzer): Datenhinweise „GBPUSD: 14 von 365 Tagen (Quelle (ibkr) liefert nicht mehr)“, „QQQUSDT/SPYUSDT/GOLD/OIL:
… ab Kontrakt-Listing“ fixen; veraltete Pläne/Analysen löschen; Worker-Meldung „Job auto-…: fertig (error) – Ergebnis hochgeladen“ entfernen.
User Choices: Ersatzquelle automatisch; Tage vor Listing mit Basiswert füllen; nur eindeutig veraltete Doku löschen;
Save to GitHub; nur lesende Abrufe externer Quellen, keine Schreibzugriffe auf Prod.
- Ursachen: Dukascopy drosselt Cloud-IPs (503/Timeout) -> Backup leer + 24 h Sperre; IBKR-Gateway 503/429 wurde als „leeres Fenster“ gezählt -> Abbruch nach ~14 Tagen
- NEU services/history_fallbacks.py: Kette je Symbol (Forex FXCM -> Dukascopy -> Yahoo; GOLD Binance PAXG -> Dukascopy; OIL/SILVER/QQQ/SPY Dukascopy),
  füllt Kopf + Lücken > 4 Tage, Naht-Skalierung; candle_cache._backup_head + history_sources.fetch_secondary nutzen sie
- IBKR: Fehler je 48h-Fenster mit Backoff wiederholt (_ibkr_chunk), Abbruch nur bei Dauerfehler; Dukascopy-Abbruch -> 30-min-IP-Pause, Kopf-Suche dann 30 min statt 24 h
- Datenhinweis nennt die Ersatzquellen; Yahoo hat 1m nur ~30 Tage (für Vor-Listing-Historie ungeeignet)
- Worker: Auto-Update isoliert Fehler je Symbol, Auto-Jobs nur lokal protokolliert (kein Upload), Altlast-Pending-Dateien auto-* werden gelöscht
- Gelöscht: archive/, ARCHIV_KATALOG.md, PLAN_REGIME_BRUECKE_LAB_KI_TRADER.md, FORTSCHRITT_UMSETZUNG.md, UMSETZUNGSPLAN_LIVE_QUALITAET.md,
  ANALYSE_LEKTIONEN_UND_REGIME_LAB.md, KI_TRADER_SETTINGS_CHECK.md, STRATEGIEN_BEWERTUNG.md, REGIME_LAB_KLARHEIT_UND_KALIBRIERUNG.md,
  REGIME_LAB_PRUEFBERICHT_2309/2409/2509.md, REGIME_DYNAMIK_PRUEFBERICHT_3009.md
- Tests: tests/test_history_fallbacks.py (24), tests/test_local_worker_auto_update.py (7); Unit-Suite ohne neue Fehler ggü. Ausgangsstand; iteration_65 (100 %)

## Backlog
- P0 (Nutzer-Einstellung): Analyst-Hauptmodell gemini-3.5-flash statt :free; live_gate_bypass AUS; fee_guard_mult 6
- P1: Partial Pooling (Gruppen-Modell + Coin-Skalen), Regime-Wahrscheinlichkeiten, BTC-Kontext für Alts
- P1: Nach 2 Wochen Broker-Daten: Fenster je Setup aus Timing-Bilanz automatisch nachführen
- P2: Kalibrierte Konfidenz für Positionsgröße nutzen, sobald `informative` = true
- Prod-Override retention_config.overrides.regime_analyses.keep_last=15 nach Deploy optional entfernen
- P1: Alternative 1m-Quelle für OIL/SILVER/QQQ/SPY (bisher nur Dukascopy, auf Render stark gedrosselt)
