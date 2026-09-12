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
| 1.6 | Gesamt-Risikobudget offener Positionen | ⬜ | `services/risk_budget.py` (neu), `bitunix_trade.py` | `test_risk_budget.py` |

## Phase 2 – Ehrliche Messung
| # | Schritt | Status |
|---|---|---|
| 2.1 | Zentraler Entry-Guard `services/entry_guard.py` | ⬜ |
| 2.2 | ML-Leckage schließen (Snapshot ≤ ts, Split nach closed_at, Kalibrierung nested) | ⬜ |
| 2.3 | R in Geld (`risk_usdt`), Backfill, Shadow-Report/Reward darauf | ⬜ |
| 2.4 | Sicherheitsstatus + Ampel | ⬜ |
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
