# Testen – Regeln (verbindlich)

**Niemals gegen die Produktiv-Datenbank testen.** Der Datenverlust vom 05./06.09.2026 (MasterPrompt
überschrieben, Paper-Trades gelöscht) entstand, weil die Preview-Umgebung mit der Produktiv-`MONGO_URL`
lief und automatisierte Tests dort `analytics/clear`, `ai/trader/reset`, `POST /api/ai/master-prompt` und
`POST /api/ai/lessons` ausgeführt haben.

## Regeln
1. `backend/.env` in Dev/Preview: `MONGO_URL=mongodb://localhost:27017`, `DB_NAME=crypto_scanner_dev`
   (oder ein anderer Nicht-Produktiv-Name). Die Produktiv-`MONGO_URL` gehört ausschließlich in die
   Render-Umgebung.
2. Unit-Tests (`tests/*.py`, Marker `unit`) laufen ohne DB/Netz: `python -m pytest tests -q -n 0`.
3. E2E-Tests (Marker `live`) nur gegen eine Preview mit lokaler DB. Vor dem Lauf prüfen:
   `curl $URL/api/admin/recovery/report` darf keine Produktiv-Daten zeigen bzw. `DB_NAME` muss `*_dev`/`*_test` sein.
4. Destruktive Endpunkte sind seit 06.09. abgesichert (Papierkorb `auto_trades_trash`, MasterPrompt-History
   50 Versionen + Restore, Lektions-History + Restore) – das ist ein Sicherheitsnetz, kein Freifahrtschein.
5. Produktiv-Daten für lokale Prüfungen nur als **Kopie** in die Dev-DB holen
   (`backend/scripts/copy_prod_subset_to_dev.py`, liest Prod read-only).

## Neue Regressionstests (06.09.)
- `tests/test_data_recovery_and_trash.py` – Wiederherstellung, Papierkorb, MasterPrompt-Restore
- `tests/test_seed_auto_and_tuning.py` – Seeding-Automatik, Feintuning

## Getrennte Test-Läufe (T1, Stand 06/2026)
Es gibt ZWEI Test-Ordner, die GETRENNT laufen müssen (beide heißen `tests` –
ein gemeinsamer Aufruf kollidiert bei der Modul-Auflösung):

1. **`backend/tests`** – klassische pytest-Suite (FakeDB, ohne Netz):
   `cd backend && python -m pytest tests -m unit -q`
   (xdist ist über `backend/pytest.ini` fest auf `-n 2 --dist loadscope` konfiguriert;
   seriell = `-n 0`.)
2. **`/app/tests`** (dieser Ordner) – Integrationstests gegen die **lokale** Mongo
   (jede Datei nutzt eine eigene `DB_NAME + "_test_…"`-Datenbank):
   `cd /app && python -m pytest tests -q`
   Jede Datei ist zusätzlich einzeln als Skript lauffähig: `python tests/test_xyz.py`.

Konventionen in diesem Ordner (T1: Skript-Asserts gekapselt):
- Keine Asserts auf Modulebene; Logik steckt in `async def main()` + `def test_main()`
  (pytest) + `if __name__ == "__main__":` (Standalone) ODER direkt in `test_*`-Funktionen.
- Zeitabhängige Prüfungen (Wochenende/Sessions) stubben die Zeitquelle
  (z.B. `market_hours.is_weekend_closed`) statt von der echten Uhr abzuhängen.
- E2E-Dateien (`test_iter3_live_bugfix.py`, `test_iter4_lesson_reject.py`,
  `test_iter44_playbook_classes.py`) brauchen ein laufendes Backend
  (`REACT_APP_BACKEND_URL`, Fallback `http://localhost:8001`) und überspringen
  sich selbst per `pytest.mark.skipif`, wenn es nicht erreichbar ist.
- `seed_iter36_sizing_trade.py` ist ein Seed-Skript, kein Test.

