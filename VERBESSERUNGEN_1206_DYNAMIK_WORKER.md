# Verbesserungen 12.06 – Lokaler Worker (RAM) & Dynamik-Werkbank

Ordnerstruktur unverändert (backend/, frontend/, local_worker/) – Render-Deploy wie bisher.

## 1. Lokaler Worker: Laptop-Absturz bei hoher Iterationszahl (Parameter-Optimierung)
**Ursache:** `FastSeries._cache` (services/fast_sim.py) legte JEDE Parameter-Kombination
(EMA 9/10/11…, RSI 7/8…) als volles numpy-Array über die gesamte Historie dauerhaft ab.
Bei 1m-Kerzen mehrere MB je Array × tausende Kombinationen × jeder Rechen-Prozess
(Windows „spawn“: jeder Prozess hat eigenen Cache) → RAM voll. Zusätzlich hielt
`indicator_cache` bis zu 1024 Serien je Prozess im RAM und schrieb unbegrenzt auf die Platte.
Das Regime-Lab rechnet auf kurzen Regime-Abschnitten – daher dort kein Absturz.

**Fix (ohne Änderung an worker.py, Ergebnisse identisch):**
- `services/array_lru.py` (neu): dict-kompatibler Cache mit prozessweitem RAM-Budget
  (Env `FAST_SIM_CACHE_MB`; automatisch: Worker = 50 % RAM / Rechen-Prozesse, Server = 512 MB).
- `services/indicator_cache.py`: RAM-Deckel `INDICATOR_CACHE_MEM_MB` (256) + Disk-Deckel `INDICATOR_CACHE_DISK_MB` (4096).
- `services/optimizer.py`: Ergebnisliste/Bayes-Historie wachsen nicht mehr linear mit (Top-Ergebnis identisch).

**dotenv-Fehler** („No module named 'dotenv'“ → Backtests/Benachrichtigungen auf dem Worker scheiterten):
`core/config.py` importiert dotenv optional, `local_worker/requirements.txt` enthält `python-dotenv`.
→ Worker-Paket im Dashboard neu herunterladen (die Services kommen aus dem Paket).

## 2. Dynamik-Werkbank
- **Empfehlung Regime-Erkennung** (`services/dynamic_advisor.py`, `GET /api/dynamic-workbench/analysis-advice`):
  Eignung 0–100 + Urteil (empfohlen / geeignet / bedingt / nicht geeignet) mit Gründen
  (Anzahl Regime, Holdout, Erkennungs-Note, Ø Phasendauer, Stichprobe je Regime, Walk-Forward, Ausreißer-Assets).
- **Timeframes:** Die Erkennung bleibt auf ihrem Timeframe (Modell ist darauf kalibriert). Empfehlung für den
  Strategie-Timeframe (≈ ¼ der Erkennung, min. 5m) + Regel-Filter auf höheren Timeframes (Multi-TF) werden
  in der Suche mitgetestet (jede 2. Runde + bei erneuten Versuchen; Schalter „Höhere Timeframes als Regel-Filter“).
- **Coins:** Vorschlag erweitert um frühere Regime-Suchen („kein Edge“) und Erkennungs-Ausreißer („Eher weglassen“).
  `regime_presence` nutzt jetzt die Live-Sicht (`live_segments`), deren Regime-IDs zum Modell passen.
- **Ausreißer/kein Edge während der Suche:** Jeder Regime-Kandidat bekommt einen Coin-Report
  (Training + Walk-Forward je Coin, Ausreißer-Logik wie in der Parameter-Optimierung).
- **Overfitting-Schutz:** Mindest-Trades in Training UND Walk-Forward (`trade_sufficient`) und
  Training→Test-Lücke (`overfit`) gehen ins Ranking ein; Regel-Tiefe der Mehrrunden-Suche 2–4 statt bis 6.
- **Endlos-Suche:** Sind alle Regime ausgereizt, gibt es statt leerer Runden eine Variations-Runde
  (andere Such-Variante: TF-Filter / einfachere Regeln / breitere Suche). Nach 3 erfolglosen Variations-Runden endet die Suche.
- **Neuer Reiter „Beste Strategie je Phase“** (`kind="match"`, `services/dynamic_matcher.py`): je Phase alle
  gewählten Strategien vergleichen (Vergleichstabelle), die besten 2 per Parameter-Suche anpassen (normale Runden/Endlos).
- **Ergebnisse:** Tabelle je Regime (Training vs. Out-of-Sample-Test, Robustheit, Coins, Vergleich); Ergebnisse
  werden gespeichert (`dynamic_workbench_runs`, überleben Render-Neustarts) – „Letzte Ergebnisse“ zum erneuten Öffnen.

## Tests
`backend/tests/test_workbench_dynamic_1206.py`, `backend/tests/test_array_lru_budget_iter.py`.

## 3. Ergebnis-Ansicht wie in der Parameter-Optimierung (Aussehen + Umfang)
- `services/dynamic_scorecard.py` (neu): Ergebnis-Bewertung der fertigen dynamischen Strategie mit denselben
  Prüfungen wie die Top-Ergebnisse der Parameter-Optimierung – Empfehlung (Ampel), WF-Score + Übereinstimmung,
  DD/PnL, Monte-Carlo (Gesamt + Holdout), Kosten-Stress, Konstanz, Breite über Coins, Mindest-Trades,
  Vergleich mit bester Einzel-Strategie, Begründung, Training/Test/Gesamt-Kennzahlen, PnL je Regime/Coin,
  Ausreißer-Variante (ohne Ausreißer-Coins → „neu optimieren“). Nur aus vorhandenen Trades, keine neue Simulation.
- Such-Verlauf (jede übernommene Verbesserung) wie „SUCH-VERLAUF“ im Optimizer.
- Top 5 Kandidaten je Regime (wie „TOP 5“) inkl. „Für Phase übernehmen“
  (`POST /api/dynamic-workbench/apply-candidate`, legt eine neue Version an, zurückholbar).

## 4. Endlos-Suche (Strategie-Optimizer) findet wieder Champions
Ursachen (services/deep_explore.py): nur Kombis, die schon mit Standard-TP/SL im Plus lagen, wurden verfeinert
(→ „0 verfeinert“ trotz 100.000+ Kombis); TP/SL wurden in der Endlos-Suche nie gesucht; die Konsistenz-Prüfung
ließ Kandidaten durchfallen, die im Test BESSER als im Training waren; viele 4–6-Regel-Kombis feuern kaum;
bei ausgeschöpftem Kombi-Raum endete die Suche sofort.
Fix: TP/SL & Trade-Einstellungen je verfeinertem Kandidaten (Schalter „TP/SL mitoptimieren“), Rettungs-Verfeinerung
der besten knapp negativen Kombi je Batch, „im Test besser“ zählt als konsistent, Kombi-Größen nach Trade-Anteil
gewichtet, max. eine Regel je Indikator-Familie, Wiederholungs-Verfeinerung statt Abbruch, Diagnose
(„x % mit genug Trades“ + Tipp) im Status und in der Auswertung. Test: 5 Champions in ~3 min (vorher 0).
