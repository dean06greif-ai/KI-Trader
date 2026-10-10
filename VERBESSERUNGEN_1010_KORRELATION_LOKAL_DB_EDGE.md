# Verbesserungen 10.10 – Korrelation lokal, MongoDB-Aufräumen, KI-Edge

## Asset-Korrelation über den lokalen Worker
- Vorher: die Korrelation lief immer in der Cloud – jedes Asset lud seine Kerzen über Render (lange
  Erst-Downloads, Render-RAM, „ohne Daten“ bei Stillstand).
- Jetzt: Ausführung folgt dem Schalter **Cloud / Lokal** des Regime-Labs (inkl. Ziel-Worker).
  Lokal lädt der Worker die Kerzen aus **seinem** Cache, rechnet mit denselben Modulen und lädt nur das
  Ergebnis hoch; der Server speichert es (`regime_lab.persist_worker_result`, kind `correlation`).
- Worker **1.24.0** (`fn="correlation"`, `local_exec.FN_MIN_VERSION`). Ältere Worker → klare 409-Meldung.
- UI: Knopftext zeigt „lokal/Cloud“, Ergebnis-Zeile „lokal berechnet“.
- **Nach dem Deploy das Worker-Paket neu herunterladen** (Ausführung → Lokal → ⚙ Verwalten → Download).

## MongoDB (Atlas 512 MB)
| | vorher | nachher |
|---|---|---|
| logische Größe (Daten + Indizes) | **435 MB (85 %)** | **306 MB (60 %)** |

- Ursache 1: Die tägliche Retention für `regime_analyses` scheiterte jeden Tag mit „Sort exceeded memory
  limit“ (Sortierung ohne Index über MB-große Dokumente). → Zeit-Indizes für alle keep_last-Collections
  (`core/indexes.py`).
- Ursache 2: `ai_chat_archive` (124 MB) speicherte jede Entscheidung erneut **mit** Markt-Snapshot,
  Policy- und Prompt-Version – wird nirgends gelesen. → neue Archiv-Einträge schlank
  (`ai_engine_housekeeping`), Bestand verschlankt (`retention.SLIM_POLICY`).
- HOLD-Entscheidungen ohne Ergebnis: Markt-Snapshot nach 3 Tagen entfernt (ML-Gate nutzt nur win/loss).
- Trade-Exporte `backtest_trades`/`optimizer_trades` (je ~2,5 MB) auf die letzten 8 gedeckelt.
- Champion-Analysen (`regime_asset_champions`) sind jetzt vor der Retention geschützt. 3 beim Aufräumen
  entfernte Analysen wurden aus dem Backup vom 09.10. **wiederhergestellt**; bis zum Deploy hält
  `retention_config.overrides.regime_analyses.keep_last = 15` sie in Prod fest.
- Alles läuft automatisch im täglichen Sweep (Speicher-Panel zeigt „… verschlankt“).

## KI-Trader
Siehe `KI_TRADER_DEEP_ANALYSE_1010.md` (Erwartungswert-Regel + Edge-Bericht in R).

## Regime
Siehe `REGIME_ANLEITUNG_1010.md`.

## Tests
`backend/tests/test_correlation_local_worker.py`, `test_lifecycle_expectancy_and_retention_slim.py`,
`test_ai_edge_report.py`. Unit-Suite (`-m unit`): keine neuen roten Tests ggü. dem Ausgangsstand
(ein Test prüfte die exakte Worker-Version 1.23.0 → auf „≥ 1.23“ angepasst).
