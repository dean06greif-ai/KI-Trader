# KI-Trader – PRD (Stand 20.09.2026)

## Original-Problemstellung (aktuelle Iteration, 20.09.2026)
Repo `dean06greif-ai/KI-Trader`, Branch `conflict_190926_2356`, Render-Deploy (Ordnerstruktur 1:1 erhalten).
1. Fee-Wächter blockt Live-Trades des KI-Traders → statt Block einen Datensammel-„Schattentrade“ machen,
   den Block später bewerten und die Wächter autonom kalibrieren (Nutzerwahl: vollautonom mit Leitplanken).
2. Lokaler Worker: Dauerbetrieb (Endlos-Suche) – „connection error“ im Terminal, Balken hing bei 10 %;
   Auto-Reconnect nach Verbindungsabbruch / Neustart der Datei. Bedienung sonst unverändert lassen.
3. Regime-Lab Autopilot soll alles vollautomatisch machen.

## Umgesetzt (20.09.2026)
### A) Wächter-Schattentrades + autonome Kalibrierung – NEU `services/guard_shadow.py`
- Hook in `bitunix_trade._on_signal_impl`: blockt Fee-Wächter oder Einstiegs-Wächter (trade_guard, regime_gate,
  safety_status) einen **LIVE**-Einstieg des KI-Traders, wird das Signal als Paper-Sammel-Trade
  (`data_collection=True`, `guard_shadow={guard, reason, snapshot}`) nachgespielt; nur der blockende Wächter wird
  für diesen Schatten übersprungen (`_shadow_bypass`, `entry_guard.check_entry(skip=)`).
  Schutz: Cooldown 15 min je Coin/Seite/Wächter, max. 8 offene Schatten, nie für Sammel-Trades.
- Hook in `_after_close`: Urteil (`block_right` = Schatten verlor, `block_wrong` = gewann netto inkl. Fees) →
  `guard_shadow_reviews`; danach `calibrate()`.
- Autotune (nur Fee-Wächter): ≥12 unverbrauchte Urteile je Teilgrund (Fee-/ATR-Minimum); Fehlblock-Quote ≥60 % &
  Netto-PnL > 0 → Faktor −0.25; ≤35 % & Netto < 0 → +0.25 zurück Richtung Baseline. Leitplanken: harte Bounds,
  max. ±1.5 um Baseline, max. 1 Schritt / 12 h. Log `guard_calibration_log` + Governance-Eintrag im KI-Chat.
  Config-Keys `guard_shadow_enabled`, `guard_autotune_enabled`, `guard_autotune_min_samples`.
- API `GET /api/ai/guard-shadow/stats`; UI `components/GuardShadowPanel.js` im KI-Trader-Setup.
- Tests: `tests/test_guard_shadow.py`.

### B) Lokaler Worker 1.13.0 (`local_worker/worker.py`, `services/local_exec.py`)
- Poll-Schleife absturzsicher (Catch-all), Backoff 5→30 s, Reconnect-Log, Nachlieferung gesicherter Ergebnisse.
- Ergebnisse vor Upload auf Platte (`<data_dir>/pending_results/`), Upload-Wiederholung 6 h.
- Server: Worker-Neustart erkennen (Heartbeat listet zugeteilten Job nicht mehr; 20 s Karenz, 3 Polls) → Job
  automatisch neu einreihen (Payload beim Claim gemerkt) bzw. klare Fehlermeldung statt bis zu 6 h Hängen.
  `worker_id` in `local_jobs` persistiert. REQUIRED_WORKER_VERSION 1.13.0 (alte Worker laufen weiter).
- Tests: `tests/test_worker_restart_and_autopilot_chain.py`.

### C) Regime-Autopilot Vollautomatik (`services/regime_autopilot.py`, `regime_lab.py`, `RegimeAutopilot.js`)
- Nach verbessertem Lauf (Cloud oder Worker) wird „Regime suchen & speichern“ mit der besten Erkennung automatisch
  in die Job-Warteschlange (`job_series`, kind `regime_analysis`) gestellt; Param `auto_chain` (UI „Vollautomatik“, default an).
- UI übernimmt das beste Ergebnis eines über Nacht fertig gewordenen Laufs beim nächsten Öffnen automatisch (einmalig je Lauf).

### D) Sonstiges
- `GET /api` und `/api/` liefern 200 (Proxy-/Preview-Healthcheck, vorher 404).
- `backend/.env` mit Nutzer-Env + `AI_TRADER_LOCAL_DISABLE=1` (Vorschau tradet nicht live).

## Backlog
- P1: Autotune für weitere Wächter (regime_gate) – aktuell nur Statistik.
- P1: Schatten-Badge in der Trade-Liste (`collection_reason` „guard_shadow:…“).
- P2: Worker-Setting „Daten-Ordner“ plattformabhängig validieren.
- Bekannt (vorbestehend): 16 Tests schlagen ohne Dev-Server :8055 bzw. wegen IBKR-Routing fehl.

---
# Vorherige Iteration (19.09.2026)

## Original-Problemstellung
Produktive Daytrading-Website (Repo dean06greif-ai/KI-Trader, Branch conflict_180926_2323, React + FastAPI + MongoDB, extern auf Render deployt – Ordnerstruktur 1:1 beibehalten). Verbesserungen sauber/modular in die bestehende Architektur:
1. Regime-Lab intuitiver (Grundgerüst → Kalibrierung → Analyse → Ergebnis), Anzeige welches Regime/Detektor kalibriert wird, Ladebalken-Bug, bestes Ergebnis automatisch übernehmen + „Edge gefunden“-Anzeige, „Übernehmen“ im Kalibrierungs-Verlauf + Anzeige der aktuellen Kalibrierungsdaten, Erklärung Auto-Kalibrierung/Vergleich/Ablation, Regime-Insights grafisch, Copilot mehr (tokensparenden) Regime-Input.
2. Datensammel-Trades (data_collection) dürfen keine Statistik verfälschen (Tages-Zusammenfassung, Dashboard Analyse → Trades → PnL „alle“).
3. Backtester-Option „Nur 100 % Regel-Treffer“ sinnlos → entfernen.
4. Prüfen: Behalten-Vorschlag (war bereits umgesetzt: POST /keep/suggest + Button in RegimeRelease) und Trefferquote je Regime-Label im Snapshot (NEU umgesetzt).
5. Wichtige Trendfilter (ADX etc.) als Indikatoren/Parameter prüfen und fehlende ergänzen – sichtbar für Copilot, Builder, Optimizer, Backtest, Live.

## Umgesetzt (19.09.2026)
Frontend (neu/modular):
- `RegimeJobProgress.js` – Hook `useLabJob` (Start/Poll/Cancel/Server-Neustart), `JobProgress` (einheitlicher Ladebalken unter dem Werkzeug), `EdgeBanner`.
- `RegimeDetectorTools.js` – EMA-Vergleich, Kombi-Auto-Kalibrierung, Ablation (aus RegimeLab.js ausgelagert), Auto-Übernahme des besten Ergebnisses + Banner „Besserer Edge gefunden & übernommen“.
- `RegimeLabSummary.js` – 4-Schritte-Übersicht (Grundgerüst / Kalibrierung aktiv? / Analysen / Ergebnis-Note), `DETECTOR_LABELS`.
- `RegimeLabHelp.js` – Glossar (Grundgerüst, Kalibrieren, Vergleich, Auto-Kalibrierung, Ablation, Live=Final, Holdout/OOS/Walk-Forward, empfohlener Weg).
- `RegimeInsights.js` – Balkengrafik je Coin (Holdout-Treffer, gut/mittel/schwach) + je Regime (Anteil, Abschnitte, Ø Dauer, WF-PnL, „dünn“).
- `RegimeEngineSettings.js` – „Wird kalibriert: <Detektor>“-Box, aktive Kalibrierung, Ladebalken, Banner; `RegimeCalibrationHistory.js` – Übernehmen mit Toast, ✓ aktiv, andere Detektoren gedimmt.
- `RegimeLab.js` – neue Struktur, Copilot-Kontext `regime{...}` kompakt; `calibApplied` in localStorage.
- `PerformanceAnalytics.js` – PnL-Filter „alle“ = Live+Paper ohne Datensammlung.
- `Backtester.js` – Checkbox entfernt, `require_all_rules` fest true (war ohnehin wirkungslos: Strategien signalisieren nur bei 100 % Regeln; Live-Default ebenfalls true).
Backend:
- `core/scheduler.py`, `routers/analytics.py` – data_collection aus Tages-/Trade-Statistik + Telegram-Tageszusammenfassung ausgeschlossen.
- `services/regime_cockpit.py` `observer_per_label`; `ai_market_observer._label_hit_pct` → Snapshot-Feature `regime_label_hit_pct`; `ml_gate.GATE_FEATURES` erweitert (alte Modelle nutzen gespeicherte Feature-Liste).
- `services/strategy_copilot.py` – Regime-Lab-Fachwissen im Panel-Prompt, Block REGIME-LAB-STAND (≤1400 Zeichen), Strategie-Übersicht im Regime-Lab auf 12 Zeilen gekürzt.
- Neue Indikatoren (Builder/Optimizer/Backtest/Live-Parität): supertrend, supertrend_dir, ema_slope_pct, roc, chop, aroon_up, aroon_down, williams_r (`services/vec.py`, `services/fast_sim.py`, `strategies/custom_strategy.py`, `strategies/custom_params.py`). ADX/DI/CCI/Keltner/Donchian waren bereits vorhanden.
- Tests: `backend/tests/test_regime_lab_intuitive_2026.py` (8 grün), Testing-Agent iteration_8 (Backend 6/6, Frontend alles grün).

## Offene Punkte / Backlog
- P1: Kalibrierung im Preview >2 min (Cloud) – ETA-Anzeige im Ladebalken.
- P2: Regime-Insights um Zeitachse (wann lag die Erkennung falsch) erweitern.
- P2: Copilot-Schnellfragen („Ist meine Note gut?“) im Regime-Lab.
- Hinweis: Lokaler Worker enthält Kopie von services/ → nach Deploy neu herunterladen.
