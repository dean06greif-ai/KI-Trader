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
