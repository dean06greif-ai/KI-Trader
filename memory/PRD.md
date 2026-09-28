# PRD – KI-Trader Regime-Lab Prüfung

## Original Problem
Externe, produktive Daytrading-Website (Render, Repo dean06greif-ai/KI-Trader, Branch conflict_250926_0712).
Regime-Lab + bisherige Forschungsergebnisse (aktuellste Analyse 25.09.) und alte Prüfberichte analysieren:
Sinnhaftigkeit, Eignung für künftige Strategien, Genauigkeit der Benchmarks, 5 vs. 9 Regime.
Originalstruktur muss erhalten bleiben; erst Analyse, dann Entscheidung des Users zur Umsetzung.

## User-Entscheidungen
- Erst Analyse, dann Umsetzung nach Freigabe
- Prüfberichte im Repo; aktuellste Analyse = 25.09. (ra_e866c510)
- Produktiv-DB nur lesend

## Erledigt (28.09.2026)
- Repo geklont nach /app/ki_trader (unverändert)
- Produktiv-DB lesend ausgewertet (6 Analysen, Autopilot-Lauf 8d76b4cfa6f2)
- Unabhängige Nachmessung mit Bitunix-1h-Kerzen (13 Coins, 720/1080 d): /app/research/regime_study.py, effn.py
- Bericht: /app/research/REGIME_LAB_ANALYSE_2809.md

- User: Regime-Lab bleibt vorerst so (nichts kritisch)
- 28.09.: Note „sehr gut“ (Label + Umrandung) mit Verlaufs-Palette Blau→Petrol→Grün in
  frontend/src/components/RegimeLab.css (Karte + Klassen-Pill), Commit in /app/ki_trader, Patch in /app/research/

## Backlog (User entscheidet)
- P0-1 Walk-Forward-Benchmark + naive Baseline
- P0-2 Abschnitts-Gate auf unabhängige Marktphasen
- P0-3 Vorzeichen-Stabilitätsprüfung Regime→Strategie
- P1-4 jump + 5er als Empfehlung für neue Analysen
- P1-5 Bewertung der 2. Achse (Vola/Stärke)
- P1-6 Versiegelter Test
- P2 Konfidenz-Kalibrierung, Nicht-Preis-Features
