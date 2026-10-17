# Verbesserungen 08.10 – Korrelation, Pause, Trail-SL, Autopilot-Verlauf

## Asset-Korrelation (Regime-Lab)
- **Ursache „ohne Daten“ (13 Assets):** (1) jedes Asset hatte ein hartes Limit von 300 s. Erst-Downloads über 540 Tage (Bitunix: 200 Kerzen/Request, + Dukascopy) dauern 15–30 min → Abbruch → nichts im Cache → beim nächsten Lauf wieder das Gleiche. (2) Bei parallelen Teil-Downloads meldete nur der erste Teil Fortschritt. (3) Yahoo-Forex: die Schleife startete beim ältesten Tag und brach nach 5 leeren Fenstern ab, die vorhandenen letzten ~30 Tage kamen nie an.
- **Fix:** Abbruch nur noch, wenn der Download steht (`STALL_TIMEOUT_S`, Pause zählt nicht), max. 3 h je Asset; der Fortschritt der Teil-Downloads wird zusammengefasst; Yahoo fragt nur sein 1m-Fenster ab; Forex ganz ohne Primärdaten → Dukascopy. Ein fehlerhaftes Asset beendet nicht mehr den ganzen Lauf, der Grund wird je Asset angezeigt.
- Jeder Lauf ist ein eigener Eintrag in `regime_correlation` (max. 40): ansehen/löschen (`GET/DELETE /api/regime-correlation/runs/{id}`). Das alte Einzel-Dokument `latest` bleibt lesbar.
- Matrix zeigt **alle ausgewählten Assets** (ohne Daten ausgegraut, Grund im Tooltip). Paare mit **|r| ≥ 0,95 (+1 wie −1)** sind markiert (★, goldener Rahmen, eigene Liste). Text „Coins“ → „Assets“.

## Pausieren & zwischendurch andere Suche
- Worker 1.23.0: ein **tatsächlich pausierter** Job belegt keinen Rechen-Slot mehr (Stand bleibt im RAM).
- `job_control`: Pause-Event je Job-Pool (vorher global → nach einer Zwischen-Suche hätte Fortsetzen den falschen Pool freigegeben).
- Bereichsübergreifende Sperren (Backtester ↔ KI-Trader-Lab, Werkbank ↔ Regime-Lab) ignorieren lokal pausierte Jobs (`job_control.parked_locally`). Im selben Bereich bleibt es bei einem Job (das Panel verfolgt einen Job). In der Cloud bleibt alles wie bisher (Render-RAM).
- Header-Anzeige „N pausiert“ (`/api/jobs/paused`) mit Fortsetzen/Abbrechen über die bestehenden Bereichs-Endpoints.
- Mobil: Phasentext (Varianten/Kombis) auf volle Breite; Score und Prozent mit einer Nachkommastelle.

## Trail-SL (`services/trail_policy.py`, gemeinsam für Backtester und Live)
- Bisher: Trail startet nur nach TP1, Abstand ATR × Faktor, SL wird nur verbessert.
- Neu (optional, Standard unverändert): `trail_mode` = `tp1` | `profit_pct` (ab X % Gewinn auf die Marge) | `crv` (ab X R); `trail_start_be` zieht beim frühen Start den SL zuerst auf Break-Even. Alles ist eine Ratsche: Trail, BE und Gewinnschutz setzen den SL **nie** zurück.
- Live-Fixes: BE bei TP1 hat einen bereits besseren SL (Gewinnschutz) auf BE **zurückgesetzt** – jetzt Ratsche wie im Backtester. Das Live-Trailing las nur die Coin-Config – jetzt werden die Trail-Werte der Strategie beim Öffnen im Trade gespeichert (Optimizer-Werte wirken live).
- Optimizer-Gruppe `trail`: + `trail_mode`, `trail_trigger_pct`, `trail_trigger_crv`, `trail_start_be`.
- Live-Prüfung: `scripts/live_trail_sl_check.py` (Minimal-Position, SL zweimal nachziehen, Read-Back, schließen).

## Autopilot-Verlauf
- „Regime suchen“ hat nichts gemacht: `RegimeAutopilot` bekam kein `onAnalyzeWith`. Jetzt nutzt es denselben Handler wie der EMA-Vergleich (`analyzeWith` in RegimeLab.js).
- Läufe löschen (`DELETE /api/regime-lab/autopilot/runs/{id}`) → fließen auch nicht mehr in den Warmstart.

Tests: `backend/tests/test_improvements_0810_trail_corr_pause.py`, `test_iter62_*.py`.
