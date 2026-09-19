# KI-Trader – PRD (Stand 19.09.2026)

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
