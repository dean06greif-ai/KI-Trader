# KI-Trader – PRD / Arbeitsstand (Emergent-Session 09/2026)

## Original-Problemstellung (Kurzfassung)
Bestehende, produktiv laufende Daytrading-Website (Repo `dean06greif-ai/KI-Trader`, Branch
`conflict_200926_2001`, Deploy extern auf Render). Struktur 1:1 beibehalten. Verbesserungen sauber,
modular, rückwärtskompatibel, mit Regressionstests:
1. Freies Kapital vorhanden, aber trotz Signalen (z.B. TrendFolge2) keine Trades – obwohl „Rest-Kapital“-Logik existiert.
2. Telegram zeigt bei TrendFolge2 „3/4 Rules“, obwohl die Strategie je Richtung nur 3 Regeln hat.
3. Regime-Lab: Auswahl einer Analyse zeigt oben zeitweise eine andere; „Ausgewählt“-Label gewünscht.
4. Regime-Lab: Punkt 3 fehlt komplett bis die Daten geladen sind -> Platzhalter.
5. Regime-Lab: Asset-Auswahl der Analyse direkt unter „Freigabe an KI-Trader“.
6. „je Coin einzeln“ -> „je Asset einzeln“ + Erklärung.

## Architektur (relevant)
- Backend FastAPI (`backend/`): `core/pipeline.py` (Signal -> Telegram -> `AutoTradeManager.on_signal`),
  `services/bitunix_trade.py` (Entry-Pfad, Kapital/Hebel), `services/capital_fit.py` (Rest-Kapital),
  `services/risk_budget.py` (Gesamt-Risikobudget), `services/entry_guard.py` (zentrale Einstiegsprüfungen),
  `services/telegram_bot.py`, `routers/regime_lab.py`.
- Frontend React (`frontend/src/components/RegimeLab.js` + `.css`).
- Prod-DB: MongoDB Atlas `crypto_scanner` (geteilt). Lokale Preview läuft mit `AI_TRADER_LOCAL_DISABLE=1`.

## Befund (Ursache Punkt 1)
- TF2 (`custom_23a30b65`) hat je Coin Live-Configs mit 40–100 USDT Marge (ADA 50, XRP 40, POL 100).
- Live-Equity ~22.7 USDT -> Risikobudget 6 % (Portfolio) / 4 % (Cluster) = ~0.9–1.4 USDT erlaubtes Risiko.
- `capital_fit` griff nicht (frei 22 > gewünscht nicht immer), das **Risikobudget** blockierte danach STILL
  (kein Telegram, kein persistierter Grund). Trades mit 5–10 USDT Marge (HYPE/ETH/SOL) passten noch.

## Umgesetzt (20.09.2026)
- **Rest-Budget-Trading**: `risk_budget.headroom()/fit_scale()/scale_for_new_trade()`,
  `entry_guard.fit_risk_budget()`, `AutoTradeManager._fit_risk_budget()` – Marge wird auf das
  freie Rest-Risikobudget verkleinert statt abgelehnt (Untergrenze `capital_fit.MIN_MARGIN_USDT`,
  Börsen-Minimum). Config-Schalter `risk_budget_config.scale_to_fit` (Default an).
- Risikobudget-Ablehnungen melden jetzt per Telegram (`_notify_reject`, 30-min-Cooldown).
- Ablehnungsgrund wird am Signal persistiert (`signals.trade_opened`, `signals.trade_reject_reason`).
- Telegram: `Rules: met/total` aus dem Signal (`TelegramNotifier.rules_counts`) statt fest „/4“.
- Preview-Guard `AI_TRADER_LOCAL_DISABLE` deckt jetzt auch Scanner-Auto-Trades und Trade-Monitor ab
  (auf Render ohne Env unverändert).
- Regime-Lab UI: Auswahl-Wechsel verwirft alte Detaildaten sofort (kein „falsches“ Regime oben),
  „Ausgewählt“-Tag in Liste, Punkt 3 immer sichtbar (Platzhalter „Bitte wähle …“ / „Lade Regime …“),
  Asset-Tabs direkt unter „Freigabe an den KI-Trader“ (+ „· ausgewählt“), Wording „je Asset“.
- Performance: `GET /api/regime-lab/{id}` ohne `chart`/`chart_emas` (–70 % Größe, `?full=1` wie bisher),
  neu `GET /api/regime-lab/{id}/chart/{symbol}`; Frontend lädt Charts je Asset lazy.
  Label-Migration nutzt gezieltes `$set` statt `replace_one`.
- Tests: `backend/tests/test_risk_budget_scale_fit.py`, `backend/tests/test_regime_lab_detail_slim.py`,
  `test_risk_budget.py` (Fenster), `test_manual_web_trades.py` (Guard neutralisiert).

## Backlog / Nächste Schritte
- P1: Risikobudget-Prozente im UI prüfen/anpassen (6 %/4 % sind bei ~22 USDT Equity sehr eng).
- P1: Signal-Liste im Frontend könnte `trade_reject_reason` anzeigen (Daten liegen jetzt vor).
- P2: Regime-Lab Detail weiter verschlanken (live_segments ~40 KB je Asset).
- P2: TF2 5m-Signal wiederholt sich je 1m-Kerze bis zur nächsten 5m-Kerze (Signals-Spam in DB).

---

# Session 21.09.2026 – Regime-Lab Job-Steuerung + Bitunix 10002 (Branch `conflict_200926_2227`)

## Problemstellung
1. Regime-Lab: die Strategie-Suche (und alle anderen Lab-Jobs) hatten nur „Abbrechen“ –
   Pausieren/Fortsetzen, „Suche beenden & Beste behalten“ und Notfall-Reset wie im
   Strategie-Optimizer fehlten.
2. Telegram „ORDER ABGEBROCHEN – Bitunix hat die Order abgelehnt: code 10002: Parameter error“
   (POLUSDT SHORT).

## Ursachen
- (2) `bitunix_trade._precision_to_step(0)` lieferte 0.0 statt 1.0 (`basePrecision=0` = ganze
  Einheiten bei POL/AVAX/…). Menge ging ungerundet („928.37“) an die Börse. Zusätzlich lieferte
  `_round_step(x, 1.0)` „928.0“ statt „928“.
- (Nebenbefund, blockierte die Suche komplett) R06-Manifest: `fetch_histories` behielt den ersten
  (angeschnittenen) und den letzten (noch laufenden) Bucket -> Checksum kippte Minuten nach der
  Analyse -> „Datensatz nicht reproduzierbar“ bei jeder Regime-Suche/Walk-Forward.

## Umgesetzt
- `services/job_control.py`: `request_stop()`, `stop_requested()`, `reset_running()` (eine Quelle,
  von Optimizer-Semantik übernommen).
- `routers/regime_lab.py`: `POST /api/regime-lab/pause|resume|stop/{job_id}`, `POST /api/regime-lab/reset`
  (Admin). `autopilot/stop` nutzt denselben Helfer (bleibt kompatibel).
- `services/regime_opt.py`: Pause-fähiger should_stop (`job_control.stop_check`), Pause-Checkpoints,
  sanfter Stop in Discovery/Parameter-Phase, `result.stopped_early`, Phase „Fertig (Suche vorzeitig beendet …)“.
- `services/dynamic_strategy.discover_regime_strategy(..., soft_stop=)`: Greedy + Deep-Test beenden
  sanft und liefern das bis dahin Beste.
- `services/regime_lab.fetch_histories` + `services/history_sources._check_cancel` (async): Pause greift
  auch während des Daten-Downloads (alle Lab-Jobs, Backtester, Optimizer).
- `services/regime_lab.fetch_histories`: nur vollständige Buckets (Anfang + Ende) -> Manifest stabil.
- `services/bitunix_trade.py`: `_precision_to_step(0) == 1.0`, `_round_step` ohne „.0“-Rest.
- Frontend `RegimeJobProgress.js`: `labJobAction`, `useLabJobControls`, `JobControls`, `JobStateTags`;
  `JobProgress` bekommt `onPause/onStop/onReset`. Genutzt in `RegimeOptimizePanel`, `RegimeLab`
  (Haupt-Balken), `RegimeDetectorTools`, `RegimeEngineSettings`, `RegimeAutopilot`.
- Tests: `tests/test_bitunix_qty_whole_units.py`, `tests/test_regime_lab_job_controls.py`
  (Unit + Live), `tests/test_regime_lab_closed_buckets.py`; `backend/test_bitunix_precision_fix.py`
  repariert (veralteter Import). Gesamtsuite: 203 grün.
- Skripte: `scripts/check_regime_lab_controls.sh` (E2E Pause/Resume/Stop gegen laufendes Backend),
  `scripts/diag_dataset_manifest.py`.

## Hinweise
- Lokaler Worker: Paket enthält die aktuellen `services/*` erst nach erneutem Download; ältere Worker
  (>= 1.10) pausieren weiterhin, kennen den sanften Stop der Regime-Suche aber noch nicht.
- Deep-Test: nach „Suche beenden“ werden keine weiteren Kandidaten mehr bewertet, die Phasen laufen
  mit dem bis dahin Besten durch.

## Backlog / offen
- P1: Bitunix-Ablehnungen mit `qty` im Telegram-Text ausweisen (bessere Diagnose).
- P2: Optimizer-/Backtester-Steuerknöpfe auf die gemeinsamen `JobControls` umziehen (nur Frontend-DRY).

---

# Session 21.09.2026 – Regime-Lab: Haupt-Balken, Übernahme-Regel, Copilot-Isolation, „Was jetzt?“

## Problemstellung (Kurzfassung)
Branch `conflict_210926_1222`. Einzel-Fortschrittsbalken der Werkzeuge (EMA-Vergleich, Auto-Kalibrierung, Ablation,
Kalibrierung) entfernen – nur Haupt-Balken; Text unter dem Balken springt (Restzeit) → statisch; Bug: schlechtere
Kalibrierung („HMM probieren“) wurde trotzdem übernommen; Regime-Lab-Copilot dachte, er sei im Strategie-Optimizer;
Nutzer versteht Autopilot-95 %, Ablation-Ergebnis und Shadow-Freigabe nicht.

## Umgesetzt
1. **Ein Haupt-Balken** – `useLabJob(onStarted)` meldet Werkzeug-Starts sofort an `attachPoll`; eigene `JobProgress`
   in RegimeDetectorTools + RegimeEngineSettings entfernt (Hinweis „läuft – Haupt-Balken oben“).
2. **Statisches Layout** – `.rl-job-meta` Grid (Phase links fix 2 Zeilen, Prozent+Restzeit rechts feste Spalte),
   `roundEta`/`useSmoothedEta` (grob gerundet, Wechsel max. alle 5 s), Endlos-Autopilot-Hinweis „≤95 % bis Suche beenden“.
3. **Übernahme-Regel** – `frontend/src/lib/regimeCalibration.js::calibrationDecision`: nur übernehmen, wenn ≥ aktive
   Kalibrierung desselben Grundgerüsts; sonst Banner „NICHT übernommen“ + „Trotzdem übernehmen“ (Verlauf bleibt).
4. **Regime-Lab-Copilot** – Backend `REGIME_LAB_SYSTEM_PROMPT` + `_regime_lab_context_block` (kein Optimizer-Prompt,
   keine Strategie-Übersicht/Min-Trades/Digests); Frontend `PANEL_UI.regime_lab` (Titel, Quick-Prompts, Leertext);
   Kontext um Autopilot-, Ablations-, Freigabe-/Shadow-Stand erweitert.
5. **„Was jetzt?“** – Autopilot-Ergebnis, Ablations-Interpretation (`ablationInterpretation`, erklärt „–%/unbewertet“),
   Freigabe (fehlende Nachweise sichtbar + Handlungshinweise, Shadow erklärt), Glossar/Workflow-Text erweitert.
6. **Tests** – `backend/tests/test_regime_lab_copilot_isolation.py` (5 unit), `frontend/src/__tests__/regimeLabHelpers.test.js`
   (14), E2E Testing-Agent `test_reports/iteration_13.json` – alles grün.

## Backlog
- P1: Walk-Forward-/Strategie-Suche-Balken in Abschnitt 3 ebenfalls nur über Haupt-Balken (bewusst unangetastet).
- P2: Ablation für Grundgerüst „ema“ hat nur Alternative „regression“ ohne Live-Kennzahlen → serverseitig bewertbare
  Alternative (z.B. reactive) ergänzen.
- P2: Kalibrierungs-Vergleich über verschiedene Referenzen (centered/hmm) methodisch normieren.

---

# Session 21.09.2026 (2) – Regime-Lab: Autopilot im Kalibrierungs-Verlauf, „nie schlechter“, Asset-Knöpfe, Shadow-Backfill

## Problemstellung (Kurzfassung)
Branch `conflict_210926_1807`. (1) Autopilot-Kalibrierungen wurden nicht als Kalibrierung gespeichert; einmal
überschrieben war der alte Stand weg; Anzeige „96,4 → 95,7“ (Autopilot senkte den Holdout). (2) Asset-Knopf „Öl“ fehlte
in der Analyse-Ansicht. (3) Shadow-Phase (30 Trades je Regime) beschleunigen ohne Qualitätsverlust. (4) Sicherheitsabfrage
beim Löschen (Regime-Analysen, Strategien). (5) Analysen nachträglich umbenennen. Nutzer-Entscheide: Verlauf als
Übernahme-Quelle; Autopilot nur automatisch übernehmen, wenn nicht schlechter, manuell weiterhin erlaubt; einfacher
Bestätigungsdialog.

## Umgesetzt (Details: PROGRESS.md, Abschnitt „Iteration 21.09.2026“)
- Backend: `services/regime_calibration_history.py` (vereinter Verlauf + Snapshots), `services/regime_shadow_backfill.py`,
  `regime_autopilot.holdout_regressed/adopt_recommended`, `fetch_histories(skipped=)`, Analyse-Felder
  `symbols_requested`/`symbols_skipped`, Routen `GET/POST /api/regime-lab/engine/snapshots`,
  `POST /api/regime-lab/shadow/backfill`, `POST /api/regime-lab/{aid}/rename`, Backfill-Trigger nach Freigabe.
- Frontend: `lib/regimeCalibration.js` (Metrik-bewusst, `autopilotDecision`), `lib/regimeSnapshots.js`,
  `RegimeCalibrationHistory.js` (Quelle-Badge, Snapshots „Zurückholen“), `RegimeAutopilot.js` (Regel + „Trotzdem
  übernehmen“), `RegimeLab.js` (Snapshot vor Übernahme, confirm beim Löschen, fehlende Asset-Knöpfe mit Grund,
  Umbenennen), `AnalysisRename.js`, `DynamicPanel.js` (confirm), `RegimeRelease.js` (Backfill-Knopf).
- Tests: `backend/tests/test_regime_calibration_history_and_shadow.py` (12), Frontend-Helpers (+6),
  E2E `test_reports/iteration_14.json` – alles grün.

## Backlog
- P1: Shadow-Backfill periodisch (z.B. täglich) statt nur nach Freigabe/manuell.
- P2: Snapshots auch für den lokalen Worker-Pfad (Kalibrierung, die am Worker endet, wird erst beim Übernehmen gesichert).
- P2: Regime-Labels (nicht nur Analysename) editierbar – erfordert Schutz vor der Label-Migration `relabel_regimes`.


## Umgesetzt (22.09.2026 – Fork-Session)
- **Repo in /app**: Branch `conflict_220926_1600` enthielt nur die Emergent-Vorlage (Render-Build: npm ERESOLVE
  react-day-picker@8 vs date-fns@4, pip emergentintegrations). /app enthält jetzt das echte Repo (Basis
  `conflict_220926_1431`) + alle Fixes → nächster „Save to GitHub“-Push ist deploybar. Lokale Preview läuft
  (ADMIN_USER/ADMIN_PASSWORD in backend/.env, siehe test_credentials.md).
- **Shadow-Backfill „0 Trades“**: `structural_regime.tf_seconds()` (24h → 86400 s statt 3600 s Fallback);
  `regime_shadow_backfill.py` nutzt sie.
- **Autopilot-Balken**: Endlos-Suche zeigt Bestwert-Score (0–100) statt 95 %-Deckel; Tie-Break
  `robustness_key` (Holdout → Ø Phase → weniger Umschaltungen). Hilfetexte (RegimeAutopilot.js,
  RegimeLabHelp.js, RegimeJobProgress.js) angepasst.
- **Autopilot „Ausgangs-Konfiguration liefert kein Modell“ (OIL/QQQ, lokaler Worker)**: Fallback auf
  Standard-Feinwerte (`fallback_start_config`), `result.start_fallback`; erklärende Fehlermeldung
  (`no_model_reason`: Kerzen/Training/nötig + Tipp). UI zeigt `Fehler: <Text>` rot im Job-Balken.
  Tests: tests/test_autopilot_no_model_fallback.py (5). Testing-Agent iteration_15: alles grün.
  HINWEIS: Der lokale Worker rechnet mit dem ZIP-Paket → nach Deploy Worker-Paket neu herunterladen.
  Bitunix-Historie OIL/QQQ beginnt erst 03/2026 (~180 Tageskerzen; Dukascopy-Backup bis 365 d).

## Offen / Backlog
- P0: Order-Abbruch/Telegram + Minimal-Trade-Fallback (≤10$→1$, 20$→2$, ≥50$→5$; Börsen-Minimals prüfen).
- P1: Gespeicherte Analysen erscheinen „eins versetzt“ (Repro nötig).
- P1: Empfehlung Regime-Scope kombiniert vs. je Asset (Beratung).
- P2: Setup-Review/Scalping-Setup (Analyse-Notizen im Chat 22.09.).
