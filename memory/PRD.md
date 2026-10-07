# PRD – MarketMaker / KI-Trader (Verbesserungen 10/2026)

## Original-Aufgabe
Bestehende, produktive Daytrading-Website (Repo KI-Trader, Branch conflict_041026_2030, Render-Deploy, Struktur 1:1 beibehalten) modular verbessern:
1. Ausreißer-Filter (wie im Strategie-Optimizer) auch für dynamische Strategien und Regime-Lab-Erkennung (+ strenger: wenige Trades / Ausreißer-Dominanz abwerten)
2. Restzeit im Optimizer genauer (blieb lange auf ~5 s)
3. Asset-Filter im Optimizer-Ergebnis (Insights je Asset inkl. Bull/Bär/Seitwärts)
4. RAM/Kerzen-Cache-Anzeige erklären
5. Ergebnis "Bestehende optimieren" (Kandidat mit Training -6139 übernommen) + "No module named 'telegram'" beheben
6. Realistische Limit-Order-Simulation in Backtester & Optimizer
7. (Folgewunsch) Backtester: dieselben Robustheits-Tests/Insights wie im Optimizer

## Architektur (unverändert: FastAPI backend/, React/CRACO frontend/, local_worker/)
Neue reine Module: services/limit_fill.py, services/regime_outliers.py, services/job_eta.py, services/backtest_robustness.py
Frontend neu: OptimizerAssetFilter.js, BacktestRobustness.js

## Umgesetzt (05.10.2026)
- Limit-Fill realistisch (Durchbruch statt Berührung, Gap-Fill, Stop in Fill-Kerze, Limit-Abstand, TP-Limit), Modus "touch" = alt
- Robuste Regime-Validierung (Training negativ / Ausreißer-Dominanz zählt nicht als validiert), refine ohne Verbesserung legt keine neue Version an
- Ausreißer je Regime im Ergebnis-Backtest, Ausreißer-Assets der Regime-Erkennung
- ETA: gleitendes Tempo + Stillstands-Korrektur; Optimizer-Fortschritt reserviert 90–98 % für Robustheits-Checks
- Asset-Filter Optimizer + Backtester; Per-Asset-Test-Kennzahlen und Marktphasen
- Kerzen-Cache: Auslagerung auch nach Disk-/Archiv-Hydrierung, RAM-Anzeige mit echtem Speicher
- Worker 1.22.0 (telegram optional importiert); Backtester-Robustheitstests (WF single/rolling/anchored, DD, Konstanz, Stress, MC, Marktphasen, Multi-Coin, Ausreißer)
- Regressionstests: backend/tests/test_improvements_1010_limit_outliers_eta.py (+ Testagent iter52)

## Umgesetzt (05.10.2026, Runde 2)
- Dynamisch „Bestehende optimieren“: Such-Modus je Phase (Parameter / Regeln+Parameter / komplett neue Strategie), auch über „Strategie ändern → Komplett neue Strategie suchen…“ (Backend: regime_modes)
- Optimizer: „Als eigene Strategie sichern“ je Top-Ergebnis + „Zwischenstand sichern“ während laufender Suche (apply type=save_copy, services/strategy_copies.py; Kopie nicht aktiviert)

## Umgesetzt (06.10.2026, Branch conflict_051026_2159)
Aufgabe: Regime-Anzahl-Bug (5 eingestellt -> 9 Regime), Regime-System prüfen + Vergleich mehrerer Erkennungen je Asset ohne Overfitting, KI-Trader-Lab Job-Steuerung wie Optimizer, Event-Setups bei Lab-Validierung automatisch live, session_open trotz Top-Lab-Werten ohne Trades -> Ursachen beheben + live schalten.
- Regime-Autopilot: Regime-Anzahl wird festgenagelt (regime_autopilot.pin_body_regime_mode; Router pinnt VOR Referenz/Feinsuche/Warmstart). Ursache: Referenz-Start + Warmstart-Seeds brachten eigenen Modus (9) mit
- Regime-Champion je Asset (services/regime_selection.py, routers/regime_champions.py, UI RegimeChampions.js): nur OOS (Holdout + innere Val.), schwächeres Fenster halb gewichtet, Overfit-Lücke/kurzes OOS/Coin-Modell bestraft, Wechsel nur bei Sieg in jedem Fenster + Mehrfachtest-Marge; Modus off(Standard)/suggest/auto; Runtime structural_regime._doc_for (Stufe bleibt Klassen-Freigabe)
- KI-Trader-Lab: Pause/Fortsetzen, Suche beenden & Ergebnisse behalten, Abbrechen, Notfall-Reset (runner.checkpoint/SoftStop, /api/ai/playbook/backtest/{pause,resume,stop}/{id}, /jobs/reset)
- Lab->Live: services/setup_lab_live.py (strenger Lab-Nachweis + Opt-in je Klasse×Setup; Revisions-Rückstufung blockt Lab-freigegebene Setups nicht; Events validiert = automatisch live, Opt-out bleibt). session_open Krypto per Opt-in live (settings.setup_lab_live)
- Setup-Trigger: verpasste Signale während LLM-Zyklus werden bis 3 Kerzen nachgeholt (Drift-Schutz 0,5 R); Lab-Live-Setups gehen direkt über die Pipeline (alle Guards)
- Backtest-Simulator wendet den Live-Mindest-SL an (Lab = Live)
- Tests: backend/tests/test_improvements_0610_regime_lab_live.py (17), fomc/econ angepasst, Testagent iter54 (alles grün)

## Backlog
- P1: Optimizer-UI: Auswahl Limit-Fill-Modus (aktuell Standard realistisch)
- P1: Regime-Lab: Ausreißer-Assets direkt per Klick aus gemeinsamer Erkennung herausnehmen
- P2: Backtester-Robustheit auch für Parameter-Stabilität (erfordert Neu-Simulation)
- P1: Regime-Gate (source=own) auf Lab-Champion umstellen (zweite Wahrheit entfernen)
- P1: Champion-Vergleich auf identischem Holdout-Zeitraum (gleiche Daten je TF) statt je Analyse
- P2: Setup-Trigger Lab-Live: Tageslimit/Asset-Liste je Setup in der UI

## 06.10.2026 – Abschluss „Ein Regime für alle“ + „Fairer Zeitraum-Vergleich“
- Prüfung des vorherigen (abgebrochenen) Laufs: Gate-Quelle `auto` (Lab/Champion → Rückfall eigene) war fertig; Fair-Vergleich-Modul existierte, wurde aber NIE ausgelöst (kein Endpoint/kein Hintergrund-Lauf/keine UI) → jetzt fertiggestellt.
- `services/regime_fair_compare.py`: zu frische (< 21 Tage OOS) und grenzlose Analysen werden ausgeschlossen statt den Vergleich zu blockieren; `status_for` (frisch ≤ 7 Tage, alle Kandidaten geprüft, Amtsinhaber messbar – sonst Alt-Verhalten); `needs_refresh`; Hintergrund-Job `start_job/run_many`.
- `regime_selection.compute` nutzt `status_for`; faire Zeilen mit Mindest-OOS in Tagen (`min_bars`).
- `structural_regime._auto_fair`: stündlich max. 2 fällige Paare (nur Champion-Modus suggest/auto).
- Endpoints: `POST /api/regime-lab/champions/fair-compare` (Admin), `GET /api/regime-lab/champions/fair-compare/status`.
- UI: `RegimeFairCompare.js` (Button + Fortschritt + Badge-Spalte „Vergleich“) in `RegimeChampions.js`; Auto-Trade-Modal zeigt die tatsächlich wirksame Phase (Lab/eigene).
- Tests: `backend/tests/test_improvements_0710_one_regime_fair_compare.py` (17), Live `test_iter55_fair_compare_live.py` (8) – alle grün.
### Backlog
- P1: Champion-Modus „Vorschlagen“ aktivieren und Fair-Ergebnisse einige Tage beobachten, dann ggf. „Automatisch“.
- P2: Fair-Vergleich-Details (Teilfenster je Kandidat) als Aufklapp-Ansicht.

## 07.10.2026 – Regime-Phasendauer fair je Regime-Anzahl + Such-Presets
- Problem: nur 9 Regime erreichten Ø Phase > 5 d. Ursachen (Bericht `REGIME_PHASENDAUER_UND_PRESETS_0710.md`): Glättungs-Wahl je Regime-Anzahl, Unterachse ohne Mindestdauer (5er 1,4 d), gleiche Untergrenze für Regime-Phase, Referenz-Richtung modusabhängig (+5 F1 für 9er), Unterachse nicht suchbar, KI-Prompt „seit X Tagen“.
- Neu: `services/regime_phase.py`, Config `sub_min_days` (1,5 d; Alt-Modelle unverändert), Suchraum je Modus, `phase_targets` im Score, Referenz-Revision 2.1, `direction_since`.
- Presets: `lib/searchPresets.js` + `SearchPresetBar` in Optimizer, Regime-Optimierung (★ je Regime), Dynamik-Werkbank (+ Je-Regime-Auto, Backend `regime_presets`), Autopilot-Such-Profile.
- Tests: test_regime_phase_fairness.py, test_search_presets_workbench.py, test_iter56_review.py – grün.
### Backlog
- P1: Kriterium „Verpasste Phasen ≤ 15 %“ gezielt angehen (Lag/Missed-Term im Such-Score).
- P2: Presets speicherbar (eigene Presets je Nutzer).

## 07.10.2026 – Asset-Korrelation im Regime-Lab (abgeschlossen)
- Vorheriger Lauf war halb fertig (Service + Top-Paare-Liste, eigener In-Memory-Job ohne RAM-/Lab-Schutz, keine Matrix, Copilot ohne Hinweis falls nichts berechnet).
- Fertiggestellt: Lauf als normaler Regime-Lab-Job (`kind=correlation`: Haupt-Balken, Abbruch/Pause, RAM-Queue, 1-Job-Schutz), Kerzen je Coin geladen + sofort verdichtet (RAM-schonend für die ganze Watchlist), Detektor der Lab-Einstellung (3 Regime), Holdout-Werte (r/Richtung/Kerzen im letzten Testabschnitt), fehlende Coins mit Grund.
- UI: `RegimeCorrelation.js` + `RegimeCorrelationMatrix.js` – Umfang Watchlist / Nur Krypto / Auswahl, Heatmap-Matrix (Score, Rendite r, Richtung %, Holdout r), Gruppen-Vorschläge mit „Gruppe für Regime-Suche übernehmen“, Warnung bei abweichendem TF/Zeitraum.
- Copilot: Werte inkl. Holdout + Veraltet-Warnung; ohne Ergebnis Hinweis auf den Knopf.
- Tests: `backend/tests/test_asset_correlation.py` (9), `backend/tests/test_regime_correlation_api.py` (Live) – grün. Bericht `REGIME_ASSET_KORRELATION_0710.md`.
### Backlog
- P2: Korrelation optional nachts automatisch aktualisieren.
