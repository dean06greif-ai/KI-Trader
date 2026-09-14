# Umsetzungs-Fortschritt (lebendes Protokoll)

> Zweck: Jederzeit nachvollziehbar, **wo genau** die Umsetzung des Plans aus `KI_TRADER_AUDIT.md` steht.
> Wird nach JEDEM abgeschlossenen Schritt aktualisiert (Status, Dateien, Tests, offene Reste).
> Status-Legende: ⬜ offen · 🔄 in Arbeit · ✅ fertig (Tests grün) · ⏸️ zurückgestellt

## Arbeitsregeln (gelten für alle Schritte)
- Reihenfolge: Reproduktionstest → Fix → Test grün → Eintrag hier.
- Nur bestehende Dateien editieren oder EIN neues Service-Modul je Thema; Ordnerstruktur unverändert.
- Lokal: lokale Mongo, keine Exchange-Keys, `AI_TRADER_LOCAL_DISABLE=1`. Tests: `cd backend && python -m pytest tests -m unit -q`.
- Neues Verhalten per Setting/Env abschaltbar, wenn es Live-Verhalten ändert.

## Phase 0 – Sofortmaßnahmen (Nutzer, ohne Code)
| # | Schritt | Status | Hinweis |
|---|---|---|---|
| 0.1 | `JWT_SECRET` auf Render setzen (≥48 Zufallszeichen) | ⬜ Nutzer | **PFLICHT VOR DEPLOY** – Backend startet auf Render (`RENDER=true`) sonst NICHT (fail-fast, 1.5). Optional `CORS_ORIGINS=https://crypto-scanner-frontend-a98r.onrender.com` |
| 0.2 | Alle im Chat geteilten Keys/Passwörter rotieren | ⬜ Nutzer | Admin-PW, Bitunix, Telegram, Supabase, Atlas, LLM-Keys |
| 0.3 | Bitunix-Key: nur Futures-Trade, IP-Whitelist | ⬜ Nutzer | |
| 0.4 | `auc_risk_scaling` im ML-Gate ausschalten | ⬜ Nutzer/UI | bis 2.2 erledigt |

## Phase 1 – Geldschutz
| # | Schritt | Status | Dateien | Tests |
|---|---|---|---|---|
| 1.2 | Kill-Switch modus-rein (Live/Paper getrennt, kein Sammel) | ✅ | `services/trade_guard.py`, `bitunix_trade.py` (~Z. 1861, `mode=` an Guard), `routers/notify.py` (`?mode=`, `states`, Resume `{mode}`) | `backend/tests/test_phase1_trade_guard.py` (9 grün) |
| 1.3 | Lernpflicht im Einstiegs-Guard erzwingen | ✅ | `services/trade_guard.py` (`check_open_allowed` blockt bei `learning_required`, kickt Lernlauf) | dito |
| 1.7 | Kill-Switch Mindestpause (`min_pause_hours`, Default 6) statt nur „bis Mitternacht UTC“ | ✅ | `services/trade_guard.py` (`pause_until`) | dito |
| 1.4 | Atomarer Close (Compare-and-Set) für alle Close-Pfade | ✅ | `bitunix_trade.py` (`_finalize_close`; Monitor, Liquidation, Sync-Close, manual_close, Teil-Exit), `ibkr_trade.py` (`_book_close`) | `test_phase1_close_paths.py` (6 grün) |
| 1.1 | Close-Fehlpfad: Trade bleibt OFFEN/UNKLAR statt lokal `closed` (Design-Entscheid: kein neuer Status, sondern `live_close_failed`+`close_escalated_at`, Retry gedrosselt 60 s, KRITISCH-Alarm + Website-Notify) | ✅ Backend / ⬜ UI-Badge (→ 2.4 Sicherheitsstatus) | `bitunix_trade.py` (`_manage_trade`, `CLOSE_FAIL_ESCALATE_AT`, `close_retry_due`) | dito |
| 1.8 | Entry ohne `position_id`: SL-Verifikation nachholen (Retry, sonst `sl_exchange_missing=True` + Alarm); Notfall-Close löst Registry-Eintrag auf (extern F03/F11) | ✅ | `services/bitunix_trade.py` (~Z. 2417–2477) | dito |
| 1.9 | Scanner-Sessions über Mitternacht (`is_trading_session`, `get_current_session`) (extern F09) | ✅ | `services/strategy_scanner.py` (`session_contains`) | `test_phase1_sessions_entry_sl.py` (4 grün) |
| 1.5 | Auth-Härtung (JWT fail-fast, geschützte GETs/POSTs, Lockout, CORS; Frontend `response.ok` bei Toggles, extern F15) | ✅ | `core/auth.py`, `core/config.py`, `routers/*`, `server.py`, `App.js` | `test_phase1_auth.py` |
| 1.6 | Gesamt-Risikobudget offener Positionen | ✅ | `services/risk_budget.py` (neu), `bitunix_trade.py` (`on_signal`, vor place_order), `routers/notify.py` (`GET /risk-budget`, `POST /risk-budget/config`) | `test_risk_budget.py` (6 grün) |

## Phase 2 – Ehrliche Messung
| # | Schritt | Status |
|---|---|---|
| 2.1 | Zentraler Entry-Guard `services/entry_guard.py` | ✅ |
| 2.2 | ML-Leckage schließen (Snapshot ≤ ts, Split nach closed_at, Kalibrierung nested) | ✅ |
| 2.3 | R in Geld (`risk_usdt`), Backfill, Shadow-Report/Reward darauf | ✅ |
| 2.4 | Sicherheitsstatus + Ampel | ✅ |
| 2.5 | Getrennte Ergebnisdaten je Setup + Wilson-Intervall | ✅ |
| 2.6 | Setup-Gewichtung nach Erwartungswert | ✅ |
| 2.7 | Reward ohne Konfidenz-Anreiz | ✅ |
| 2.8 | Echte Bestätigungen in Governance | ✅ |
| 2.9 | Mitternachts-Filter Detektoren | ✅ |
| 2.10 | Shadow ehrlich (auc_risk_scaling Default aus) | ✅ |

## Phase 3 – Champion vs. Kandidat
| # | Schritt | Status |
|---|---|---|
| 3.1 | Policy-Fingerprint an Entscheidung/Trade | ✅ |
| 3.2 | Kandidaten-Modus `services/policy_lab.py` | ✅ |
| 3.3 | Promotion-Regel | ✅ |
| 3.4 | Netto-Erfolgsbericht je Version | ✅ |
| 3.5 | Portfolio-Backtest (gemeinsames Kapital, Korrelation, Szenarien) | ✅ |

## Phase T – Test-Hygiene
| # | Schritt | Status |
|---|---|---|
| T1 | `/app/tests` Skript-Asserts kapseln | ✅ |
| T4 | Paritätstest check_signal vs. fast_sim | ✅ |
| T2 | GitHub-Action: beide Test-Suiten bei jedem Push | ✅ |
| T3 | Read-only Prod-Probe `scripts/prod_safety_probe.py` | ✅ |

---
## Protokoll (chronologisch, neueste unten)
- 12.09.2026 – Audit + Plan erstellt (`KI_TRADER_AUDIT.md`). Umsetzung gestartet mit Phase 1.
- 12.09.2026 – **1.2/1.3/1.7 fertig** (`trade_guard.py`): State je Modus (`trade_guard_state` = live,
  `trade_guard_state_paper` neu), `on_trade_closed` filtert `mode` + `data_collection≠True`,
  Referenzkapital = Equity des Modus (Fallback alt), Lernpflicht blockt Einstieg, Anti-Stacking je Modus,
  `min_pause_hours`. API rückwärtskompatibel (`state` weiterhin vorhanden, zusätzlich `states`, `mode`).
  9 neue Tests grün. Frontend-Anzeige (SettingsPanel) funktioniert unverändert (`state`-Feld).
- 12.09.2026 – Zweiter externer Bericht eingearbeitet (Abschnitt 1b in `KI_TRADER_AUDIT.md`): neue
  Schritte **1.8** (SL-Verifikation ohne position_id + Registry-Auflösung beim Notfall-Close) und
  **1.9** (Scanner-Sessions über Mitternacht); 2.8/2.2/2.3/T4 präzisiert.
- 12.09.2026 – **1.4/1.1 fertig**: `_finalize_close(trade_id, updates)` = `update_one({"id", "status":"open"})`
  + `matched_count` → nur der Gewinner ruft `_after_close`. Alle 6 Close-Pfade umgestellt (Monitor,
  Liquidation, Bitunix-Sync, manual_close, Teil-Exit, IBKR-Sync). Close-Fehlpfad: nach 5 Fehlversuchen
  KEIN lokales `closed` mehr; Trade bleibt offen mit `live_close_failed=True`, `close_escalated_at`,
  Retry alle 60 s (`live_close_last_try`), Telegram „KRITISCH“ + Website-Notify `close_failed`.
  Abweichung vom Plan: kein neuer Status `close_failed` (24 `status:"open"`-Queries wären betroffen) –
  Flag-Ansatz ist rückwärtskompatibel. Voller Unit-Lauf: 1276 passed, nur vorbestehende Fails.
- 12.09.2026 – **1.8/1.9 fertig**: positionId-Nachfassen (1,5 s) → sonst `sl_exchange_missing=True`
  + Alarm; Notfall-Close löst `entry_order_registry` auf; `StrategyScanner.session_contains` mit
  Mitternachts-Wrap (22:00–06:00). 4 Tests grün.
- 12.09.2026 – **1.5 fertig**: `core/auth.py` (`resolve_jwt_secret` fail-fast bei `RENDER`/`JWT_REQUIRE_SECRET`,
  lokal Zufalls-Secret; `credentials_valid` compare_digest, leerer User nur ohne ADMIN_USER;
  Lockout 5 Fehlversuche/15 min je IP, X-Forwarded-For), `routers/auth.py` (429 bei Sperre),
  `require_admin` neu auf: GET `/autotrade/balance`, `/autotrade/capital`, POST `/analytics/ai-review`,
  `/analytics/clear/preview`, `/dynamic/{id}/refresh`, `/notifications`. `server.py`: CORS aus
  `CORS_ORIGINS` (Default `*` ohne credentials). Frontend: Header/PerformanceAnalytics/CapitalModal/
  NewTradeModal/toast.js senden Token + prüfen `r.ok`; `App.js toggleNotification` prüft `res.ok`
  (Rollback + Fehler-Toast). 7 Tests grün; E2E lokal: 401 ohne Token, 200 mit Token, 429 nach 5 Fehlversuchen.
  **Deploy-Hinweis:** Ohne `JWT_SECRET` startet Render-Backend nicht mehr (gewollt, Nutzerentscheid).
- NÄCHSTER SCHRITT: 1.6 Gesamt-Risikobudget (`services/risk_budget.py` neu + Hook in `on_signal`).
- (Datum unbekannt, vor 26.06.2026) – **1.6 fertig** (Protokoll-Nachtrag 26.06.): `services/risk_budget.py`
  (reine Kernfunktionen `trade_risk_usdt`/`open_risk`/`check` + `check_new_trade`), Regel: offenes Risiko
  (|Entry−SL|×Restmenge, SL im Gewinn = 0) + neuer Trade ≤ `max_portfolio_risk_pct` (Default 6%) × Equity,
  zusätzlich Cluster-Limit je Anlageklasse (`max_cluster_risk_pct`, Default 4%). Fail-open ohne Equity.
  Hook in `on_signal` (nach qty-Berechnung, nicht für Sammel-Trades), API `GET /api/risk-budget` +
  `POST /api/risk-budget/config` (routers/notify.py).
  `tests/test_risk_budget.py` (6 grün).
- 26.06.2026 – **2.1 fertig**: Zentraler Entry-Guard `services/entry_guard.py` (neu). Design-Entscheid:
  Bestehende Guards bleiben Bausteine (kein Neuschreiben); da die Prüfungen unterschiedliche Pipeline-Daten
  brauchen (Levels/SL erst nach `_levels`, Risiko erst nach Sizing), bündelt `check_entry(db, signal, cfg,
  mode, tf, checks)` die Stufe-1-Prüfungen (Kill-Switch + Lernpflicht + Übergangsschutz + Anti-Stacking via
  `trade_guard.check_open_allowed` + Regime-Gate) und `check_risk_budget(...)` das Risikobudget (fail-open,
  ersetzt den Inline-Block in `_on_signal_impl`). Fee-Wächter, Low-Vol-ATR-Block und Kapital-Limit laufen an
  ihrer bestehenden Stelle und werden über `EntryChecks.record()` protokolliert. Ergebnis wird als
  `entry_checks[]` am Trade gespeichert (Nachvollziehbarkeit je Trade: was wurde mit welchem Ergebnis
  geprüft). Alle Einstiegswege (Strategie-Signal, KI, Key-Level-Limit-Fill, manueller Trade, IBKR) laufen
  über `core/pipeline.py → on_signal` und damit automatisch durch den Guard. Slippage-Wächter bleibt an der
  KI-Entscheidungsstufe (`ai_engine`, blockt VOR Signal-Emission – dort korrekt platziert); „Wochenende“
  ist Teil der Scanner-Sessions (1.9), kein separater Entry-Check nötig.
  Dateien: `services/entry_guard.py` (neu), `services/bitunix_trade.py` (Guard-Aufrufe + `entry_checks` am
  Trade-Dokument), `tests/test_entry_guard.py` (6 neu, grün), `tests/test_risk_budget.py` (Wiring-Test auf
  entry_guard umgestellt). Kein API-/Verhaltensbruch: identische Reihenfolge, identische Reject-Gründe.
- 26.06.2026 – **Test-Hygiene (vorbestehende Fails)**: `test_strategy_insights.py::test_daily_quota_cooldown…`
  an bewusst geändertes Verhalten angepasst (Minuten-Limit = `MINUTE_LIMIT_COOLDOWN_S` ~65 s statt 10 min,
  siehe Docstring `_quota_cooldown_s`); `test_fix_custom_ai_trades.py` ScannerStub um `save_settings`
  ergänzt (Produktionscode nutzt `await scanner.save_settings`). Unit-Suite: 1300 passed; einzige Rest-Fails
  = `test_iter38_…` (braucht Dev-Server auf :8055 MIT konfigurierten Bitunix-Live-Keys – rein umgebungsbedingt).
- 26.06.2026 – **2.2 fertig (ML-Leckage geschlossen)**:
  1. `ai_ml_lab.nearest_snapshot(..., allow_future=False)` Default: nur Snapshots `ts <= target`
     (vorher konnte ein ZUKUNFTS-Snapshot als "nächster" gewählt werden). Gilt automatisch für
     ml_gate-Row-Builder (importieren dieselbe Funktion).
  2. `ai_ml_lab.train_sync(..., timestamps=)`: zeitliche Expanding-Window-Folds (`temporal_folds`)
     statt gemischtem StratifiedKFold; Fallback stratified ohne Zeitstempel/valide Folds.
     `build_dataset`/`load_training_data` liefern jetzt 4-Tupel (X, y, timestamps, meta) –
     Aufrufer angepasst (`routers/ai_lab.py`, Tests). Ergebnis-Feld `cv_mode` (temporal|stratified).
  3. `ml_gate.purged_walk_forward(..., label_timestamps=)`: Training nur Samples, deren LABEL
     (Trade-Close) vor Test-Start − Embargo entstand. Row-Builder liefern 5-Tupel mit `label_ts`
     (decision: trade_closed_at→outcome_ts→ts; signal: result_ts→ts; ghost: closed_at→ts);
     `build_dataset` → 6-Tupel, Projektionen erweitert, `routers/ml_gate.py` angepasst.
  4. Kalibrierung verschachtelt: berichteter `oos_brier_calibrated` nutzt je Fold nur einen auf
     FRÜHEREN Folds gefitteten Kalibrator (`CALIB_MIN_SAMPLES=30`); produktiver Kalibrator
     weiterhin auf allen OOS-Punkten. Neue Metrik-Felder: `label_purged`, `calibration_nested_samples`.
  Tests: `tests/test_ml_leakage_fixes.py` (6 neu, grün); Unit-Suite 1306 passed (Rest nur iter38-Umgebung).
- 26.06.2026 – **2.3 fertig (R in Geld)**: Neue Trades speichern `risk_usdt = risk × qty`
  (bitunix_trade, Trade-Dict); Boot-Migration `migrate_risk_usdt_backfill` (boot_migrations.py,
  Pipeline-Update, idempotent über Marker `risk_usdt_backfill_v1`) trägt es für Alt-Trades nach.
  `ml_gate.money_r(trade)` (neu, rein): R = pnl / risk_usdt, Fallback risk×qty, None ohne Basis –
  `shadow_report` nutzt sie (vorher pnl / Preisdistanz = dimensional falsch, R war um Faktor qty
  daneben). `ai_rewards.compute_reward`: Basis-Komponente jetzt "R-Basis (PnL/Risiko)" = clamp(R, ±4)
  wenn risk_usdt vorhanden, sonst alter PnL-%-Fallback (Alt-Trades); neues Feld `r_multiple` im
  Reward. Tests: `tests/test_risk_usdt_r_metric.py` (6 neu, grün); Unit-Suite 1312 passed.
- NÄCHSTER SCHRITT: 2.4 Sicherheitsstatus (`services/safety_status.py` + GET /api/safety/status +
  Ampel im Frontend, critical blockt via entry_guard).
- 26.06.2026 – **2.4 fertig (Sicherheitsstatus + Ampel)** – Umsetzung war bei Session-Abbruch bereits
  im Repo, Protokoll-Nachtrag: `services/safety_status.py` (Ampel ok|warn|critical aus sl_missing,
  close_failed, sync_stale, watchdog_stale, kill_switch; 20 s Cache, Config `safety_status_config`),
  `GET /api/safety/status` + `POST /api/safety/config` (routers/notify.py, admin), Hook in
  `entry_guard.check_entry` (blockt neue LIVE-Trades bei critical, fail-open, abschaltbar über
  `block_on_critical`), Frontend-Ampel `SafetyLight` in Header.js (Punkt + Label, 60-s-Poll,
  data-testid `safety-status-light`). Tests: `tests/test_safety_status.py` (7 grün).
- 26.06.2026 – **2.5 fertig (Wilson-Intervall + Live-Vorrang)**: `setup_lifecycle.wilson_interval`
  (95%-CI der Winrate) + `is_uncertain` (n<15); `ai_playbook._stats_row` liefert zusätzlich
  `wr_ci`/`uncertain` (rückwärtskompatibel, nur neue Felder); maturity-Zeilen tragen `wr_ci`,
  `live_wr_ci`, `uncertain` → UI (SetupMaturityTable) zeigt CI im Tooltip + gelben "unsicher"-Badge
  am Urteil. Gewichtung: `setup_weighting.judging_stats` (Live-Bilanz maßgeblich ab 5 echten
  Live-Trades, sonst Misch-Statistik nur als Prior mit Abzug – kann dämpfen, wertet NIE über 1.0
  auf), `combined_weight(..., class_live_stats=, live_pref=)`; neuer Klassen-Cache-Zugriff
  `ai_playbook.class_live_stats`. Schalter `setup_weight_live_pref` (DEFAULT_AI_CONFIG, Default an).
  Design-Entscheid: `setup_stats`-Rückgabeform NICHT geändert (viele Aufrufer) – die 4 Buckets
  {live, collect/paper, backtest, gesamt} liegen bereits getrennt an den maturity-Zeilen; Promotion
  bleibt bewusst paper-basiert (Sammeln→Live braucht Paper-Daten), Demotion nutzt bereits live.
  Tests: `tests/test_setup_stats_wilson.py` (7 neu, grün).
- 26.06.2026 – **2.6 fertig (Gewichtung nach Erwartungswert)**: `_GROUP_FIELDS` aggregiert
  zusätzlich `risk_usdt` (Summe riskiertes Kapital, seit 2.3 am Trade); `setup_weighting.weight_for`
  basiert jetzt auf dem geschrumpften Netto-R-Mittel je riskiertem USDT (pnl/Σrisk_usdt, Clamp ±1
  VOR Shrink n/(n+8), ×0.25 Spanne), Trefferquote nur noch Tiebreaker (±0.05). Plan-Test bestanden:
  90/100 Gewinner mit PnL −500 → Gewicht < 1. Fallback ohne risk_usdt-Daten (Alt-Trades) = alte
  WR-Shrinkage. Schalter `setup_weight_ev` (Default an). Tests: `tests/test_setup_weight_ev.py`
  (7 neu, grün); bestehende Gewichtungs-Tests unverändert grün.
- 26.06.2026 – **2.7 fertig (Reward entkoppelt)**: Konfidenz-Bonus/Malus aus `compute_reward`
  entfernt (Schalter `confidence_reward` in settings `ai_rewards_config`, Default AUS);
  Regelverletzungen separat als `violations[]` am Reward; `confidence` wird am Reward gespeichert.
  Neue Kennzahl Kalibrierungsfehler: `calibration_from_rows`/`calibration(db)` (|Ø-Konfidenz −
  Winrate| je Bin <60/60-70/70-80/80-90/90+, trade-gewichteter Gesamtfehler), im API-Payload
  `GET /api/ai/rewards` (`calibration`) + Tabelle im AIRewardPanel (data-testid
  `reward-calibration`); KI-Lern-Kontext (`context_text`) erklärt Kalibrierung statt
  Konfidenz-Disziplin. 1 Alt-Test an neuen Vertrag angepasst (test_iter_reward_guard_models).
  Tests: `tests/test_reward_decoupling.py` (6 neu, grün).
- 26.06.2026 – **2.8 fertig (Echte Bestätigungen)**: `ai_validation.real_confirmations(prior_ts,
  trade_ts, min_new_trades)` (rein): frühere Vorschläge derselben Richtung zählen nur als
  Bestätigung, wenn seit der zuletzt gezählten Bestätigung ≥ N NEUE geschlossene Trades vorlagen
  (nicht überlappende Evidenz-Fenster); auch der aktuelle Vorschlag braucht frische Evidenz –
  Vorschlags-Spam erhöht nichts mehr. Neues Setting `macro_evidence_min_trades` (Default 3).
  `ai_engine_governance._macro_gate` nutzt die Funktion (statt blindem count_documents) mit
  Evidenz-Basis `_evidence_trade_ts` (geschlossene Trades im Fenster; candidate-Scope über
  ai_candidate_id); Gate trägt `evidence_since_ts`/`evidence_min_trades`. Tests:
  `tests/test_real_confirmations.py` (6 neu, grün); 4 Alt-Tests in `test_ai_learning.py` an den
  neuen Vertrag angepasst (FakeDB sät jetzt Evidenz-Trades zwischen den Bestätigungen).
- 26.06.2026 – **2.9 fertig (Mitternachts-Filter Detektoren)**: `setup_backtest/detectors.py
  apply_filters`: `hour_from > hour_to` = Fenster über Mitternacht (22–6 → 22:00–05:59 Berlin,
  vorher leerer Filter). Test `test_apply_filters_hours_wrap_midnight` (grün, Nacht+Tag = alle).
- 26.06.2026 – **2.10 fertig (Shadow ehrlich)**: `ml_gate.DEFAULT_SETTINGS.auc_risk_scaling`
  Default AUS (Phase-0-Punkt 0.4 damit im Code verankert; bestehende DB-Einstellung bleibt
  respektiert – wer es aktiv hat, muss es im ML-Panel ausschalten). Status liefert
  `risk_scaling_active`; Trades tragen `ml_risk_scale` (Kennzeichnung, wenn die ML-Skalierung
  die Positionsgröße änderte). 2 Alt-Tests an neuen Default angepasst + `test_default_scaling_off`.
  **Phase 2 ist damit KOMPLETT.**
- 26.06.2026 – **Testing-Agent (iteration_60)**: Backend 10/10 grün (Login/401, Safety-Status +
  Config-Roundtrip, Rewards-Kalibrierung, ML-Gate `risk_scaling_active=false` unter
  `/api/ml/gate/status`, Playbook 200); Frontend-Smoke grün. 1 LOW-UX-Fund gefixt:
  `SafetyLight` lädt jetzt sofort bei Login/Logout neu (`adminAuthed` als useEffect-Dependency,
  Header.js) statt erst nach Reload/60-s-Poll. Unit-Suite final: **1347 passed**, Rest-Fails nur
  `test_iter38_*` (Dev-Server :8055 mit Live-Keys nötig – umgebungsbedingt, kein Bug).
- NÄCHSTER SCHRITT: Phase 3 (3.1 Policy-Fingerprint an Entscheidung/Trade) oder Phase T
  (T1 Skript-Asserts, T4 Paritätstest) – Reihenfolge nach Nutzer-Priorität.
- 26.06.2026 (neue Session) – Nutzer-Entscheid: Plan-Datei = Quelle der Wahrheit, Reihenfolge
  einhalten → Phase 3 vor Phase T. Fortschritte weiter auf Branch `conflict_130926_2244`
  (Push via Emergent "Save to GitHub"). Baseline-Check nach Repo-Import: 1347 passed,
  Rest-Fails nur `test_iter38_*` (umgebungsbedingt) – identisch zum letzten Stand.
- 26.06.2026 – **3.1 fertig (Policy-Fingerprint)**: `services/policy_fingerprint.py` (neu, rein):
  `short_hash` (kanonisches JSON, 10 Zeichen), `lessons_hash` (nur Inhalt title/detail/weight,
  sortiert – Zeitstempel/Zähler ändern die Policy nicht), `sizing_hash` (SIZING_KEYS =
  sizing_mode/risk_*/max_capital_per_trade/lev_*/swing_max_leverage), `build(...)` →
  `{prompt_hash, lessons_hash, playbook_version, model, gate_version, sizing_hash, combined}`,
  `group_key`. Verdrahtung: `ai_engine` berechnet die zyklusweiten Teile EINMAL vor der
  Gruppen-Schleife (Lektionen via learning.get_lessons, Playbook via NEU
  `ai_playbook.revision_versions()` aus dem Klassen-Cache, Gate = ml_gate.model_meta.version,
  Sizing aus config) und hängt `policy_version` an jede Decision (prompt_hash =
  prompt_version.combined, model = model_used je Gruppe). Fluss: Decision → Signal
  (`_emit_signal`) → Trade (`bitunix_trade`, Feld `policy_version`; manuelle/Strategie-Trades =
  None). Key-Level-Limit-Fills erben den Fingerprint automatisch (gespeichertes Signal wird
  wiederverwendet). Auswertung: `setup_variant.policy_groups(trades)` (rein, erweitert – nicht
  ersetzt) gruppiert nach `combined` ('' = Alt-Trades) mit trades/wins/pnl/first_ts/last_ts →
  Basis für 3.4 Policy-Report. Kein API-/Verhaltensbruch (nur neue Felder).
  Tests: `tests/test_policy_fingerprint.py` (7 neu, grün); Unit-Suite 1354 passed.
- 26.06.2026 – **3.2 fertig (Kandidaten-Modus)**: `services/policy_lab.py` (neu): Singleton
  `policy_lab` nach strategy_lab-Muster (setup(engine)/load_state/tick). Kandidat = EINE aktive
  Policy-Änderung (`system_suffix` an den Analyse-Systemprompt und/oder `config_overrides` aus
  SIZING_KEYS+min_confidence, validiert durch reines `validate_candidate`), gespeichert in
  `policy_candidates`. Champion handelt unverändert; der Kandidat läuft als SHADOW auf DENSELBEN
  Gruppen-Prompts: eigener LLM-Call nur bei jedem k-ten Zyklus (`shadow_every_k`, Default 3 –
  Token-Deckel, `begin_cycle()` in run_analysis), Hook am Ende jeder Gruppen-Schleife
  (`shadow_group`, fail-safe try/except). Trials in `policy_trials` mit Kandidaten-Fingerprint
  (candidate_fingerprint = Champion-Prompt-Hash+Suffix, Sizing mit Overrides), Entry-Kosten via
  `paper_execution.entry_fill`, SL/TP aus sl_pct/tp1_pct mit `clamp_levels` (wie Champion-Parsing).
  Auswertung im Ökosystem-Tick (Engine-Tick-Liste + server.py setup/load_state): SL/TP-Touch via
  scanner.current_price (ghost_outcome wiederverwendet), Exit-Kosten via exit_fill, Netto-%
  (`net_pnl_pct` inkl. 2× fee_pct), Timeout → expired (Deckel `max_open_trials`).
  API (routers/ai_lab.py): GET `/api/policy-lab/status` + `/trials`, POST `/candidate`,
  `/candidate/discard`, `/config` (admin). Tests: `tests/test_policy_lab.py` (9 neu, grün,
  FakeDB/FakeEngine ohne Netzwerk); Unit-Suite 1363 passed. UI-Panel folgt mit 3.4-Report.
- NÄCHSTER SCHRITT: 3.3 Promotion-Regel (rein: n≥N, Bootstrap-Untergrenze Netto-R-Differenz > 0,
  Max-Drawdown nicht schlechter, nur Situationen NACH Kandidaten-Erzeugung; Rollback-Fenster).
- 26.06.2026 – **3.3 fertig (Promotion-Regel + Rollback)**: `services/policy_promotion.py` (neu,
  REIN): `trial_r` (Trial-R = Netto-% / SL-Distanz-%), `max_drawdown` (kumulierte R-Kurve),
  `bootstrap_diff_lower` (deterministisch, seed), `promotion_check` (5 Kriterien: n≥min_trials 40,
  Champion-Basis ≥10, ΔR-Mittel>0, Bootstrap-5%-Untergrenze>0, DD nicht schlechter +Toleranz),
  `rollback_check` (Fenster 20 Trades, ab 8: R-Mittel<0 UND schlechter als Baseline → Rücknahme).
  Einheit beider Seiten = R-Multiple (Champion via ml_gate.money_r seit Kandidaten-Erzeugung –
  "nur Situationen nach Erzeugung"). policy_lab-Verdrahtung: Promotion-Check im Tick (30-min-
  Drossel) → Status `promotion_ready` + Chat/Telegram (ntype `policy_lab`); Promotion selbst
  bleibt TRADER-Aktion: POST `/api/policy-lab/promote` (force möglich) wendet config_overrides
  via engine.update_config an und hängt system_suffix als "[Policy-Promotion id]" an den
  MasterPrompt (Version-Snapshot). Rollback: automatisch im Fenster (`_check_rollback` →
  Config-Restore + master_prompt.restore) oder manuell POST `/api/policy-lab/rollback`; Fenster
  ohne Verschlechterung → `promoted_final` (Shadow endet). GET `/api/policy-lab/promotion-report`.
  Settings-Block `promotion` in `/api/policy-lab/config`. Tests: `tests/test_policy_promotion.py`
  (8 neu, grün).
- 26.06.2026 – **3.4 fertig (Netto-Erfolgsbericht je Policy-Version)**:
  `setup_variant.policy_report_rows(trades)` (rein, erweitert): je Policy-Fingerprint die Welten
  live/paper/collect + gesamt mit trades/wins/losses/wr, PnL NETTO (realized_pnl inkl. Fees,
  Slippage steckt im Fill), Σ Fees, Σ slippage_usdt, Σ risk_usdt, Ø R in Geld; jüngste Policy
  zuerst, Alt-Trades ('') zuletzt. `GET /api/analytics/policy-report?days=90` (routers/
  analytics.py): + Entscheidungs-Anzahl je Policy (ai_decisions-Aggregation) und Betriebskosten
  SEPARAT (`ops`: token_estimate_total + analysis_cycles aus dem Analyse-Feed – nie mit PnL
  verrechnet). UI: `frontend/src/components/PolicyReportCard.js` (neu, Muster SlippageStatsCard:
  einklappbar, 30/90/180 Tage, Welten-Aufklappung je Version, data-testids `policy-report-*`),
  eingebunden im Analyse-Panel (PerformanceAnalytics, unter der Slippage-Karte).
  Tests: `tests/test_policy_report.py` (3 neu, grün). Unit-Suite: **1374 passed**
  (Rest-Fails weiter nur iter38-Umgebung). **Phase 3 (3.1–3.4) damit KOMPLETT**;
  3.5 Portfolio-Backtest bleibt lt. Audit "später".
- NÄCHSTER SCHRITT: Phase T – T1 (`/app/tests` Skript-Asserts kapseln, README_TESTING) und
  T4 (Paritätstest check_signal vs. fast_sim), danach Testing-Agent-Lauf + Push.
- 26.06.2026 (neue Session) – Repo-Import Branch `conflict_140926_0940`, Nutzer-Entscheid: Plan
  fortsetzen (T1+T4). Baseline: backend-Unit 1374 passed (nur iter38-Umgebung rot).
- 26.06.2026 – **T4 fertig (Paritätstest check_signal vs. fast_sim)**:
  `backend/tests/test_strategy_parity.py` (NEU, 19 Tests grün): für ALLE 13 Built-in-Strategien
  mit `vectorized_signals` wird `fast_sim.build_builtin_signal_provider(...)` gegen den
  Referenzpfad `check_signal(candles[:i+1])` auf identischen Zufalls-Kerzen verglichen
  (5 Seeds × 260 Kerzen × jeder Index; Vergleich type/signal_class/entry_price), zusätzlich mit
  Nicht-Default-Parametern (ema_pullback require_mid/Perioden, rsi_only, bollinger_reversion,
  stoch_reversal) + Registry-Drift-Wächter + Nicht-Vakuität (Signale müssen feuern).
  ERGEBNIS: 0 Abweichungen – der externe Befund F10 (EMA-Pullback PRE_SIGNAL vs. SHORT) ist im
  aktuellen Code NICHT mehr reproduzierbar (durch frühere Fixes behoben); F5 damit geklärt.
  KEINE Produktionsänderung nötig.
- 26.06.2026 – **T1 fertig (Test-Hygiene `/app/tests`)**: Suite ist jetzt komplett
  pytest-fähig: `cd /app && python -m pytest tests -q` → **163 passed, 0 failed**.
  Einzeldateien bleiben standalone lauffähig (`python tests/test_x.py`). Fixes im Detail:
  1. Veraltete Defaults an Produktions-Wahrheit angepasst: fee_guard_mult/atr_mult 4.0→2.5
     (test_fee_guard, test_weekend_guard_and_fee_v2, jetzt gegen DEFAULT_AI_CONFIG),
     Boot-Migration-Modelle via Konstanten ANALYST_CHAIN/CEREBRAS_REPLACEMENT
     (test_boot_migrations_and_guards), Playbook-Setup-Anzahl >=16 statt ==16 (iter44),
     trend_follow2-Beschreibung: Längen-Ausnahme 200 (test_asset_class_setups).
  2. An neue Verträge angepasst: ml_gate Row-Builder 5-Tupel mit label_ts (test_phase5),
     shadow_predict braucht Krypto-Symbol (B6), ai_ml_lab.load_training_data 4-Tupel,
     Proposal-Supersede-bei-Insert erlaubt Status `superseded` für geparkten Vorschlag
     (test_phase4; auto_applied bleibt verboten), Setup-Reife je Anlageklasse: Trades brauchen
     `symbol`, Asserts auf `classes.crypto.*` (test_setup_lifecycle),
     Preset-Hebel-Vorrang + risk_max_margin_pct-Deckel (test_risk_sizing: 150 USDT @7.2x,
     Risiko 5.4), funding_fade nur noch in ALIAS-CHECK-Zeile des Forex-Kontexts erlaubt.
  3. E2E-Dateien (iter3/iter4/iter44) auf `REACT_APP_BACKEND_URL`-Env (Fallback
     localhost:8001) + `pytest.mark.skipif` bei nicht erreichbarem Backend umgestellt;
     Zugangsdaten aus Env statt hartkodiert. `_run` in test_ram_and_manager_guards auf
     `asyncio.run` (geschlossener-Loop-Bug unter pytest); FakeAutotrader um
     `ai_manage_allowed` ergänzt; run_analysis-Preflight in test_fix_0_4 per Dummy-Env-Key.
  4. Produktions-Änderungen (minimal, sicherheitsfördernd): `scripts/migrate_0_5_result_truth.py`
     prüft `--prod --apply`-Verbot jetzt VOR dem PROD_MONGO_URL-Check (Schreibschutz greift
     immer); `backend/tests/test_regime_lab.py`/`test_regime_worker_regression.py` Login mit
     `username` aus `memory/test_credentials.md` (Auth-Härtung 1.5 verlangt User) + Skip bei
     leerer Regime-Liste; `memory/test_credentials.md` (gitignored) neu befüllt.
  5. `tests/README_TESTING.md`: getrennte Aufrufe beider `tests`-Ordner + T1-Konventionen
     dokumentiert.
  Backend-Unit-Suite final: **1393 passed** (1374 alt + 19 Paritäts-Tests), Rest-Fails
  weiterhin NUR `test_iter38_*` (braucht Dev-Server :8055 mit Bitunix-Live-Keys – Umgebung).
  **Phase T (T1+T4) damit KOMPLETT.** T2 (GitHub-Action) und T3 (Prod-Probe-Skript) bleiben
  als optionale Punkte aus dem Audit-Backlog offen.
- 26.06.2026 (neue Session) – Repo-Import Branch `conflict_140926_1702`, Nutzer-Entscheid: Plan
  fortsetzen. Baseline nach Import identisch zum Protokoll: backend-Unit **1393 passed**
  (rot nur `test_iter38_*`, umgebungsbedingt), Root-`/tests` **163 passed**.
- 26.06.2026 – **Testing-Agent (iteration_62, Smoke nach Repo-Import)**: Backend **15/15 grün**
  (Login+401, Auth-Schutz autotrade/balance, safety/status, risk-budget, ai/rewards.calibration,
  ml/gate/status risk_scaling_active=false, policy-lab status+trials, analytics/policy-report
  rows+ops, slippage-stats); Frontend grün (Login via Schloss-Icon, Safety-Ampel data-level=ok,
  PolicyReportCard rendert unter Tab "Trades" mit Empty-State). KEINE Produktions-Bugs; einzige
  Änderung: `backend/tests/smoke_regression_test.py` um 7 Read-only-Smoke-Tests erweitert
  (auto-markiert `live`, Unit-Suite unberührt). Hinweis aus dem Lauf: SlippageStatsCard sitzt
  per Design hinter dem showSlippage-Toggle (Default aus) – kein Bug.
- 26.06.2026 – **T2 fertig (GitHub-Action)**: `.github/workflows/tests.yml` (NEU): bei jedem
  Push/PR ein Job (ubuntu, Python 3.11 = Render-Version, Mongo-7-Service-Container,
  `AI_TRADER_LOCAL_DISABLE=1`, CI-Dummy-Admin-Creds): 1) `cd backend && pytest tests -m unit`
  mit `--ignore=tests/test_iter38_watchdog_pnl_playbook_api.py` (braucht Dev-Server :8055 mit
  Bitunix-Live-Keys – in CI nicht erfüllbar, lokal wie dokumentiert), 2) Root
  `python -m pytest tests -q` (E2E-Dateien skippen sich ohne Backend selbst, T1).
  Beide Kommandos lokal verifiziert: 1393 passed / 169 passed. Kein Deploy-Eingriff.
- 26.06.2026 – **T3 fertig (Read-only Prod-Probe)**: `scripts/prod_safety_probe.py` (NEU,
  nur find/aggregate, keine Writes): 1) Kill-Switch-Zustand live+paper (settings
  `trade_guard_state[_paper]`, Auswertung als reine Funktion `guard_state_view`),
  2) offene Trades mit `live_close_failed=True` (Close-Fehlpfad 1.1), 3) offene Trades mit
  `sl_exchange_missing=True` (SL-Verifikation 1.8), 4) Positions-Abgleich Börse↔`auto_trades`
  via `BitunixTradeClient.get_positions()` (reine Funktion `diff_positions`, orphan/ghost je
  Symbol inkl. Symbol-Mapping GOLD→XAUUSDT; ohne Keys sauber übersprungen). Env-Auflösung:
  `PROD_MONGO_URL` → `backend/.env.prod` → `backend/.env` (Muster prod_readonly_probe).
  `--json`-Flag; Exit-Code 1 bei Findings (Cron-/CI-tauglich). Tests:
  `tests/test_prod_safety_probe.py` (6 neu, grün – Root-Suite jetzt 169 passed);
  README_TESTING um T2/T3-Abschnitt ergänzt. Lokaler Probelauf: keine Findings, exit=0.
  **Audit-Backlog T2/T3 damit erledigt; Phase T komplett (T1–T4).**
- 26.06.2026 – **3.5 fertig (Portfolio-Backtest)** – letzter offener Code-Punkt des Audits.
  Eigener Modus, bestehende Backtest-Läufe UNVERÄNDERT: `services/portfolio_backtest.py` (neu):
  reine Kernfunktionen `normalize_trades` (Einzel-Sim-Trades → Replay-Zeilen inkl. risk_usdt/
  notional/Cluster via risk_budget.cluster_of), `portfolio_replay` (chronologisches Replay gegen
  EIN gemeinsames Kapital: Margin-Reservierung je Trade, max_open_trades, Risikobudget
  max_portfolio_risk_pct + Cluster-Limit analog Live-Guard, Skip-Zähler je Grund, Equity-Kurve
  ≤400 Punkte, Max-DD, max/Ø gleichzeitige Positionen), `daily_pnl_by_symbol`+`correlation_pairs`
  (Pearson der Tages-PnL je Symbol-Paar, min. 5 gemeinsame Tage), Szenarien `apply_slippage`
  (Aufschlag je Fill-Seite aufs Notional) und `apply_outage` (deterministische, gleichverteilte
  Offline-Fenster: Entries entfallen, Exits im Fenster schlechtestenfalls am initialen SL, nie
  besser als real), `run_portfolio_analysis` (Gesamtauswertung), `sanitize_portfolio_cfg`.
  Runner mit eigenem Job-Store PJOBS (Muster bt.JOBS, via ram_queue), nutzt UNVERÄNDERTE
  `backtester.simulate_pair` (collect_trades=True) + fast_sim-Provider je (Strategie, Symbol).
  API (routers/backtest.py): POST `/api/portfolio-backtest/run` (admin, 409 wenn Backtest/
  Portfolio-Job läuft), GET `/api/portfolio-backtest/status[?job_id]`, POST `/cancel/{id}`.
  UI: `frontend/src/components/PortfolioBacktestCard.js` (neu, einklappbare Karte unten im
  Backtester-Strategien-Tab, nutzt die oben gewählten Strategien/Coins/Zeitraum; Eingaben
  Startkapital/Max Positionen/Risiko %/Cluster %/Slippage %/Ausfälle; Ergebnis: KPIs,
  Übersprungen-Zeile, Szenario-Tabelle Basis vs. Slippage vs. Ausfall, Korrelations-Pills
  >0.5 gelb; data-testids `pbt-*`/`portfolio-backtest-card`; Run-Button nur Admin).
  Tests: `backend/tests/test_portfolio_backtest.py` (10 neu, grün) – Unit-Suite **1403 passed**.
  Testing-Agent iteration_63: Backend 5/5 + Frontend-E2E (Login→Backtester→Karte→Lauf→Ergebnis)
  100% grün, Regression /api/backtest/run unberührt. **Audit-Plan damit VOLLSTÄNDIG umgesetzt
  (Phasen 1, 2, 3 inkl. 3.5, T inkl. T2/T3).**
- NÄCHSTER SCHRITT: Push via Emergent "Save to GitHub". Danach lt. Plan: 2–4 Wochen MESSPHASE
  der LIVE-Ausführungsparameter (Bausteine A+B: Schwellen/Guards nicht anfassen!) – Strategie-
  Suche, Backtests/Portfolio-Backtests, Policy-Lab-Kandidaten (Shadow) und Website-
  Verbesserungen sind davon NICHT betroffen und weiterhin erlaubt. Nach der Messphase:
  Auswertung `GET /api/autotrade/slippage-stats` (UI-Karte) + Policy-Report → Entscheid
  Option 3 (1m-Hybrid-Trigger) bzw. Feintuning der Schwellen.
