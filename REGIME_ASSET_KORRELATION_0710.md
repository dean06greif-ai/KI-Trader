# Asset-Korrelation im Regime-Lab – Prüf- & Umsetzungsbericht 07.10.2026

## Gab es das schon?
Teilweise: Jede Regime-Analyse speichert `coin_similarity` (Anteil gleicher Regime unter dem kombinierten Modell, nur für die gewählten Coins). Es fehlten Rendite-Korrelation, Gruppen-Vorschläge, die ganze Watchlist und Werte für den Copilot. Der letzte Prompt hatte das begonnen, war aber nicht fertig.

## Prüfung des letzten Stands
| Punkt | Vorher | Jetzt |
|---|---|---|
| Job | eigener In-Memory-Job neben dem Lab (lief parallel zu anderen Lab-Jobs, keine RAM-Queue, kein Abbruch) | normaler Lab-Job `correlation`: Haupt-Balken, Abbruch/Pause, RAM-Queue, 1-Job-Schutz |
| RAM | alle Coins gleichzeitig geladen (Watchlist 25 × 17 000 Kerzen) | je Coin laden → verdichten → freigeben |
| Matrix | nur Top-15-Paare als Liste | Heatmap aller Paare, Kennzahl umschaltbar |
| Erkennung | fest `reactive` | Detektor der Lab-Einstellung, immer 3 Regime (ab/seitwärts/auf) |
| Belastbarkeit | – | Holdout-Werte (letzte 100-Training-% der Kerzen): r, Richtung %, Kerzenzahl |
| Umfang | Watchlist | Watchlist (Standard) / Nur Krypto / Auswahl |
| Copilot | nur wenn berechnet | mit Holdout-Werten, Veraltet-Warnung bei anderem TF/Zeitraum, sonst Hinweis auf den Knopf |

## Kennzahlen
- **r**: Pearson-Korrelation der Log-Renditen je Kerze.
- **Richtung %**: Anteil gemeinsamer Kerzen mit gleicher Phase (ab/seitwärts/auf).
- **Score**: Mittel aus r und zufallsbereinigter Richtungs-Übereinstimmung. Gruppen: Average-Linkage, Ø Score ≥ 0,55.

## API
- `POST /api/regime-correlation` (Admin) `{symbols, timeframe, days, train_pct, engine, engine_config}` → `{job_id}`
- `GET /api/regime-correlation` → `{job, result}` (Rückgabeform kompatibel zum vorherigen Stand)

## Risiken / Rückwärtskompatibilität
- Während der Berechnung ist kein anderer Cloud-Lab-Job möglich (gleiche Regel wie bei allen Lab-Jobs). Hängende Datenquellen: 300 s Zeitlimit je Coin, der Coin wird dann als „ohne Daten“ geführt.
- Keine Änderung an bestehenden Analysen, Modellen oder am Trading.

## Tests
`backend/tests/test_asset_correlation.py` (9 Unit-Tests: Rechnung, Holdout, Detektor-Config, Job-Lauf mit synthetischen Kerzen, Abbruch/Fehler, Router-Schutz) + `backend/tests/test_regime_correlation_api.py` (Live). Vorher schon rote Tests: unverändert dieselben 20 (umgebungsabhängig, nicht betroffen).
