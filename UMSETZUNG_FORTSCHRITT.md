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
| 2.4 | Sicherheitsstatus + Ampel | 🔄 |
| 2.5 | Getrennte Ergebnisdaten je Setup + Wilson-Intervall | ⬜ |
| 2.6 | Setup-Gewichtung nach Erwartungswert | ⬜ |
| 2.7 | Reward ohne Konfidenz-Anreiz | ⬜ |
| 2.8 | Echte Bestätigungen in Governance | ⬜ |
| 2.9 | Mitternachts-Filter Detektoren | ⬜ |
| 2.10 | Shadow ehrlich (auc_risk_scaling Default aus) | ⬜ |

## Phase 3 – Champion vs. Kandidat
| # | Schritt | Status |
|---|---|---|
| 3.1 | Policy-Fingerprint an Entscheidung/Trade | ⬜ |
| 3.2 | Kandidaten-Modus `services/policy_lab.py` | ⬜ |
| 3.3 | Promotion-Regel | ⬜ |
| 3.4 | Netto-Erfolgsbericht je Version | ⬜ |

## Phase T – Test-Hygiene
| # | Schritt | Status |
|---|---|---|
| T1 | `/app/tests` Skript-Asserts kapseln | ⬜ |
| T4 | Paritätstest check_signal vs. fast_sim | ⬜ |

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
