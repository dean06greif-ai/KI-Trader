# Verbesserungen 02.10.2026: Werkbank-Pause, Phasen je Strategie, lokaler Dynamik-Backtest

## 1. Ladebalken der Dynamik-Werkbank (wie Strategie-Discovery)
- Buttons: **Pausieren/Fortsetzen**, **Suche beenden & Beste behalten**, **Abbrechen**, **Zurücksetzen (Notfall)**. Dazu „Pause angefordert…“, „⏸ pausiert (Dauer)“ und die Restzeit.
- Backend: `POST /api/dynamic-workbench/{pause|resume|stop|cancel}/{id}` und `POST /api/dynamic-workbench/reset`.
  Das Pause-Flag wandert an den laufenden Regime-Lab-Unterjob (Cloud oder lokaler Worker, mit `db.local_jobs` gespiegelt). Zwischen den Regimen gibt es einen Pause-Checkpoint (`services/dynamic_workbench.py`).

## 2. Entkopplung Werkbank ↔ Regime-Lab
- `GET /api/regime-lab/active` liefert keine Werkbank-Unterjobs mehr. Der Regime-Lab-Balken zeigt also nur echte Lab-Jobs.
- Läuft die Werkbank in der Cloud, gibt es eine klare Meldung beim Lab-Start: Parallel geht es nur lokal auf einem **anderen** Worker. Die Cloud bleibt bei 1 Job (Render-RAM).

## 3. Bestehende optimieren: Strategie je Phase + Verlauf
- Panel „STRATEGIE JE PHASE“: Je Regime siehst du die aktuell gehandelte Strategie und die **darauf optimierte** Strategie (Score, PnL, Trades, Validierung). Die optimierte Strategie wird auch dann angezeigt, wenn die Phase abgeschaltet ist.
- Aktionen nach Bestätigung: *Nicht handeln* · *Optimierte Strategie aktivieren* · *Ausgangs-Strategie X*. Optional kannst du die Trade-Parameter zurücksetzen.
- Jede Änderung erzeugt eine **neue Version** (`dynamic_strategy_versions`). Vor der ersten Änderung wird der Ausgangsstand als v1 gesichert.
- Verlauf: Jede Version kann angesehen und wiederhergestellt werden. Das Wiederherstellen ist selbst wieder eine neue Version, es geht also nichts verloren.
- Freigabe (R09): Eine geänderte Definition wird zum Entwurf. Der alte Walk-Forward gilt nicht mehr für sie.
  - Optional „Freigabe beibehalten“: wird ehrlich als Freigabe ohne neuen Test protokolliert.
  - Stellst du eine Version exakt wieder her, gilt ihre alte Freigabe wieder.
- API:
  - `GET /api/dynamic/{id}/phases`
  - `POST /api/dynamic/{id}/phase` (`confirm: true` Pflicht)
  - `GET /api/dynamic/{id}/versions`
  - `POST /api/dynamic/{id}/versions/{v}/restore`

## 4. Backtester: dynamische Strategien auch auf dem lokalen Worker
- Der Server schickt die Strategie-Dokumente mit (`args.dynamic_docs`). Der Worker rechnet dann exakt denselben Pfad wie die Cloud (`backtester.run_backtest(dynamic_docs=…)`).
- Dafür ist **Worker 1.20.0** nötig. Ältere Worker bekommen solche Jobs nicht, die Website meldet das mit 409 und dem Hinweis „Worker-Paket neu herunterladen“.

## 5. Kleiner Fix
- Hinweis „Ø Live-Phase 5.0 Tage liegt unter der Untergrenze 5“ zeigt jetzt 2 Nachkommastellen (z.B. 4.96), wenn die Rundung den Abstand verschlucken würde.

## Tests
- `backend/tests/test_workbench_phases_1010.py`: 14 Regressionstests, laufen gegen eine lokale Test-DB.
- Bestehende Suiten (Werkbank, Job-Steuerung, Multi-Worker, Dynamik-Backtest) sind grün.
- Testing-Agent iteration_43: Backend und Frontend ohne Befund.
