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
