# KI-Trader (Crypto Scanner) – Iteration Log

## Ursprung
Bestehende, produktive Daytrading-Website (extern auf Render deployt). Repo:
https://github.com/dean06greif-ai/KI-Trader (branch conflict_080926_1721).
Stack: FastAPI (backend/) + React (frontend/) + MongoDB Atlas. Zusatzmodule:
local_worker/ (Rechen-Worker), ibeam_gateway/ (IBKR). Original-Ordnerstruktur
MUSS für den Render-Deploy erhalten bleiben (nur bestehende Dateien editiert).

## Grundsatz
Sauber, modular, rückwärtskompatibel. Keine Schnelllösungen. Stabilität vor
aggressiven Änderungen.

## Umgesetzt (2026-06 / diese Iteration)
1. **Lokaler-Worker-Token kopierbar** (`frontend/src/components/LocalWorkerPanel.js`):
   - `loadToken()` mit Retry (Cold-Start/Admin-Session), robustes `copy()` mit
     `execCommand`-Fallback, eigenes MARKIERBARES Token-Feld (`lw-token-field`)
     + Button `lw-token-copy`, `lw-cmd-copy` nicht mehr dauerhaft disabled.
   - Worker-Code (`local_worker/worker.py`) bewusst UNVERÄNDERT gelassen.
2. **Setup-Reife lädt automatisch** beim Öffnen des Verlauf-Panels
   (`AIEquityPanel.js`): `loadMaturity()` mit Retry-wenn-leer, Reload-Button
   aktualisiert auch die Reife-Tabelle.
3. **Live-Logik ↔ Datensammel-Modus-Umschalter** für die Setup-Reife
   (`AIEquityPanel.js` + `SetupMaturityTable.js`), analog zum Equity-Umschalter.
   Backend liefert neue Felder `collect_trades/collect_winrate/collect_pnl`
   (`ai_playbook.py`: `setup_stats(paper_only=True)` je Klasse + global,
   `maturity_overview` erweitert).
4. **Reset bei starker Setup-Änderung** (`setup_lifecycle.py` + `ai_playbook.py`):
   Neue reine Funktion `is_strong_change` (>=30 % SL/TP/Hebel ODER TF-Wechsel).
   `evolve_versions` markiert starke Versionen mit `strong`. `_refresh_scope`
   setzt bei starker Änderung `eval_since[setup]` der Anlageklasse zurück →
   Validierung startet neu. Kleine Tunings (<30 %, gedeckeltes ±20 %) lösen
   das NICHT aus.

## Verifikation
- Backend: `/api/ai/playbook` liefert collect_*-Felder (curl bestätigt);
  Reset end-to-end bestätigt (`eval_since` wird gesetzt); 3 Unit-Tests grün
  (`backend/tests/test_strong_change_reset.py`).
- Frontend: Testing-Agent 3/3 PASS (Token kopierbar, Reife auto-load, Umschalter).

## Deploy-Hinweise
- `.env` ist gitignored → lokale Test-`.env` (Paper-Modus, lokale Mongo) gelangt
  NICHT ins Repo; Render-Env-Variablen des Nutzers bleiben unberührt.
- Lokale Testumgebung nutzt bewusst KEINE echten Bitunix/LLM-Keys (kein Live-Trade-Risiko).

## Backlog / offen
- P2: Sammel-Statistik ggf. zusätzlich in der globalen Equity-Kurve spiegeln.
- P2: Frühere Fetch-Fehler beim Cold-Start durch Retry/ErrorBoundary glätten (Log-Noise).
