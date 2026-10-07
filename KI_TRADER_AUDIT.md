# KI-Trader – Audit & Umsetzungsplan „Geldschutz, ehrliche Messung, Champion vs. Kandidat“

Stand: 12.09.2026 · Basis: Branch `conflict_120926_1840` (1:1 nach `/app`) · Nur Analyse, **keine Codeänderungen**.

> Zweck: Die Befunde der externen KI-Bewertung wurden **im Code nachgeprüft** (Datei/Zeile) und
> mit isolierten Reproduktionen belegt. Danach folgt ein priorisierter, in die bestehende Architektur
> eingepasster Umsetzungsplan (Original-Ordnerstruktur für Render bleibt unverändert).
> Hinweis: Eine Datei `KI_TRADER_AUDIT.md` existierte im Repo bisher nicht – dieses Dokument füllt diese Rolle.

---

## 0. Kurzfazit

| Bereich | Externe Note | Mein Gegencheck |
|---|---|---|
| Entwicklungsbasis / Funktionsumfang | 3 | **Bestätigt (eher 2–3)** – 1261 Unit-Tests grün, saubere Modultrennung, viele Schutzbausteine |
| Technischer Gesamtstand | 4 | **Bestätigt** – Kernpfade korrekt, aber Geldschutz und Bewertung haben Logiklücken |
| Eignung für unbeaufsichtigten Echtgeldbetrieb | 5 | **Bestätigt** – 3 rote Befunde reproduziert (Close-Fehlpfad, Paper-Verdeckung, Auth) |

**Alle 🔴-Befunde und fast alle 🟠-Befunde der externen KI treffen zu.** Zwei Punkte sind zu präzisieren
(Handelszeiten über Mitternacht: nur ein Pfad betroffen; „Shadow-Modus“: Einfluss ist ein globaler
AUC-Skalierungsfaktor, keine Einzeltrade-Prognose). Zusätzlich habe ich 9 eigene Befunde ergänzt
(Abschnitt 3).

Wichtigste Aussage der externen KI, die ich uneingeschränkt teile: **Nicht mehr Autonomie, nicht mehr KI-
Rollen. Zuerst Geldschutz korrigieren, dann ehrlich messen, dann Champion-vs.-Kandidat.**

---

## 1. Gegencheck der externen Befunde (Code-Evidenz)

Legende: ✅ bestätigt · ⚠️ teilweise / präzisiert · ❌ nicht bestätigt

### 🔴 F1 – Fehlgeschlagene Live-Schließungen werden nach 5 Versuchen lokal als „closed“ verbucht ✅
- `backend/services/bitunix_trade.py` Z. 3389–3422 (`monitor`-Close-Pfad):
  `live_close_ok = attempts >= 5` → danach `status="closed"`, `result`, `realized_pnl` (Paper-Berechnung!)
  werden gesetzt, `_after_close` läuft (Telegram, Reward, Kill-Switch) – **obwohl die Börsenposition
  nachweislich nicht geschlossen wurde**.
- Folgen: Phantom-PnL, Kill-Switch/Reward auf Basis nicht existierender Ergebnisse, Position läuft ohne
  lokales Monitoring weiter (nur noch Watchdog kann sie „adoptieren“).
- Risiko: **hoch**. Ein Trade darf nie „closed“ sein, wenn die Börse etwas anderes sagt.

### 🔴 F2 – Paper-/Sammel-Gewinne verdecken Live-Verluste im Kill-Switch ✅ (reproduziert)
- `backend/services/trade_guard.py` Z. 217–250 `on_trade_closed`: Query `{"status":"closed", "closed_at"≥heute}`
  **ohne `mode`- und `data_collection`-Filter**. Tages-PnL und Verlustserie mischen Live, Paper, Sammeltrades.
- Reproduktion (isoliert, Fake-DB): Live −100 USDT → Trigger „Tagesverlust 10 %“; zusätzlich Paper +200 → **kein Trigger**.
- Ebenso `ref_capital`-Automatik (Summe `max_capital` aller Trades) wird durch Paper-Trades verwässert.
- Hinweis: `check_open_allowed` (Anti-Stacking) ist ebenfalls modus-blind.

### 🔴 F3 – Zugangsschutz ✅
- `core/auth.py` Z. 10: `JWT_SECRET = os.getenv("JWT_SECRET", "change-me")` → mit bekanntem Default kann
  **jeder gültige Admin-Tokens selbst signieren**. `JWT_SECRET` fehlt in der Render-Env-Liste des Nutzers.
- Design-Prinzip „GET öffentlich, WRITE geschützt“ (`core/auth.py` Docstring) → damit öffentlich:
  `GET /api/autotrade/balance`, `GET /api/autotrade/capital` (inkl. `live_total_balance`,
  `exchange_available`), alle Trade-/Entscheidungs-/Playbook-Listen, `/api/ai/status`.
- `POST /api/analytics/ai-review` (`routers/analytics.py` Z. 494) **ohne** `require_admin` → verbraucht
  LLM-Kontingente (Groq/OpenRouter) für jeden Aufrufer.
- Weitere ungeschützte POSTs: `/api/analytics/clear/preview`, `/api/dynamic/{id}/refresh`,
  `/api/notifications*` (Spam-Vektor). Worker-Endpunkte nutzen eigenes Token (ok).
- Ergänzung (eigen): `admin_login` akzeptiert leeren Benutzernamen; kein Rate-Limit/Lockout;
  CORS `allow_origins=["*"]` **mit** `allow_credentials=True` (`server.py` Z. 433).

### 🟠 F4 – KI-Bewertung kann besser aussehen als sie ist ✅
| Teilbefund | Evidenz | Status |
|---|---|---|
| Marktzustand **nach** Entscheidungszeitpunkt im Training | `ai_ml_lab.nearest_snapshot` nutzt `abs(ts - target)` (Z. 96–109); Reproduktion: Snapshot +10 min wird dem Snapshot −30 min vorgezogen. `ml_gate.row_from_signal/row_from_ghost` nutzen dieselbe Funktion als Fallback | ✅ |
| Zeitliche Train/Test-Trennung ignoriert Abschlusszeitpunkt | `ml_gate.purged_walk_forward` splittet nach `ts` (Öffnung); Label ist erst bei `closed_at` bekannt. Embargo 24 h hilft nur bei Trades < 24 h Haltedauer. `ai_ml_lab.train_sync` nutzt sogar `StratifiedKFold(shuffle=True)` = keinerlei zeitliche Trennung | ✅ |
| Kalibrierung auf denselben Daten bewertet | `ml_gate.train_sync` Z. 254–260: Platt-Fit auf `oos_pred`, `oos_brier_calibrated` auf exakt denselben `oos_pred` | ✅ |
| R-Kennzahl nutzt Preisabstand statt Geldrisiko | `ml_gate.shadow_report` Z. 606–610: `r = realized_pnl / trade.risk`; `risk` ist `abs(entry − sl)` **pro Einheit** (`bitunix_trade.py` Z. 1463, 2529). Korrekt: `pnl / (risk × qty)`. Beispiel: BTC 0.01 Stk, SL-Abstand 1000 $, PnL +10 → aktuell r=0.01, korrekt r=1.0. `economic_uplift_pct` ist damit wertlos | ✅ |

### 🟠 F5 – Weitere Punkte
| Befund | Evidenz | Status |
|---|---|---|
| Handelszeiten über Mitternacht | `ai_roles.in_active_hours` und `ai_schedule.window_matches` behandeln Mitternacht **korrekt**. Betroffen ist `setup_backtest/detectors.apply_filters` Z. 621/634: `h_from <= hour < h_to` → bei `hour_from=22, hour_to=2` **kein einziges Signal**. KI-Revisionen, die so ein Fenster vorschlagen, werden dadurch als „kein Edge“ bewertet | ⚠️ ein Pfad |
| Lernpflicht nicht durchgesetzt | `trade_guard.check_open_allowed` prüft nur `paused`; `learning_required` wird nirgends als Sperre ausgewertet (nur Anzeige). Nach Mitternacht UTC ist Auto-Trading frei, auch wenn der Lernlauf scheiterte | ✅ |
| Doppelte Close-Hooks | Kein Compare-and-Set: alle Close-Pfade (`monitor` Z. 3424, `manual_close` Z. 3986, Watchdog Z. 2860/4099, IBKR) machen `update_one({"id"})` ohne `status:"open"`-Bedingung. `_monitor_lock` schützt nur Monitor↔Preis-Wächter, nicht `manual_close`/KI-Trade-Manager/Watchdog. Rewards sind idempotent (Dedupe), **Kill-Switch-Zähler, Telegram, Signal-Sync nicht** | ✅ |
| Abweichung zweier Strategie-Berechnungspfade | Live-Scanner: `strategies/*.check_signal` (Python, Kerzenliste); Backtest/Optimizer: `services/fast_sim.py` (vektorisiert, NumPy) mit `provider_for`/`build_builtin_signal_provider`. Zwei Implementierungen derselben Regel = systematisches Divergenz-Risiko (Rundung, Warm-up, `cross`-Definition, HTF-Alignment). Konkrete Abweichung habe ich **nicht** einzeln verifiziert; die Architektur macht sie aber wahrscheinlich | ⚠️ offen |

### Zweite Bewertung (Logik) ✅
| Befund | Evidenz | Status |
|---|---|---|
| Verlustreiches Setup bekommt mehr Gewicht | `setup_weighting.weight_for`: Gewicht ≈ Trefferquote (Bayes-Shrink), PnL nur ±0.05. Reproduktion: 100 Trades, 90 Gewinner, **−500 USDT** → Gewicht **1.25 (Maximum)**, Konfidenz 70→80 | ✅ |
| Reward belohnt Selbstbewusstsein | `ai_rewards.compute_reward` Z. 61–66: Verlust bei conf<80 −0.5, Gewinn bei conf≥80 +0.25. Gleicher Verlust: conf 60 → −5.5, conf 90 → −5.0. Basis `pnl / max_capital` = Margin-relativ → hebelabhängig | ✅ |
| „Shadow“ beeinflusst Positionsgröße | `ai_engine.py` Z. 2293 `ml_gate.size_factor()` → `signal.ml_risk_scale` → `bitunix_trade.py` Z. 2021 skaliert Margin. Präzisierung: Es ist ein **globaler** Faktor (OOS-AUC < 0.55 → ×0.5), keine Einzelprognose. Trotzdem: ein „nur beobachtendes“ Modell verändert echtes Kapital, und seine AUC stammt aus einem Pfad mit Leckage (F4) | ⚠️ präzisiert |
| Wiederholte Vorschläge = Bestätigungen | `ai_engine_governance.py` Z. 129–135: `confirmations = 1 + count(ai_proposals gleicher Richtung im Fenster)` – zählt eigene frühere Vorschläge, nicht neue Evidenz. Lektionen: `confirmations` erhöht sich bei jeder Wiedererkennung (`ai_learning.py` Z. 830–853) | ✅ |
| Paper ≠ Live nicht getrennt bewertet | Playbook trennt `collect_*` bereits (paper_only), aber `judge_stats`/Gewichtung/Konfidenz-Justierung mischen weiterhin | ✅ (teilweise vorhanden) |
| Kein Nachweis, dass Lernen verbessert | Es gibt keinen Champion/Kandidat-Vergleich; Versionszuordnung erfolgt per Zeitfenster (`setup_variant.py`: „seit“-Zeitpunkt), nicht per Entscheidungs-Fingerprint | ✅ |

**Was ich NICHT bestätigen konnte / offen:** die konkrete Strategie-Pfad-Abweichung (siehe oben); ob
`JWT_SECRET` auf Render evtl. doch gesetzt ist (Nutzer-Liste sagt: fehlt → als fehlend behandeln).

---

## 1b. Nachtrag 12.09. – zweiter externer Bericht (`/app/audit/KI_TRADER_AUDIT.md` der Prüf-KI)

Zusätzliche Befunde, im Code **gegengeprüft und bestätigt** – in den Plan aufgenommen:

| ID (extern) | Befund | Evidenz | In Plan |
|---|---|---|---|
| F03 (P1) | Ohne `position_id` nach dem Entry wird die SL-Verifikation übersprungen, `sl_exchange_missing` bleibt `False` | `bitunix_trade.py` Z. 2448–2470: `if position_id:` umschließt `_ensure_live_sl`; Else-Zweig fehlt | **1.8** |
| F09 (P1) | Handelssessions über Mitternacht (`custom_sessions` 22:00–06:00) im **Strategie-Scanner** falsch (`start <= cur < end`) – zusätzlich zu den Detektoren (2.9) | `strategy_scanner.py` Z. 167–187 `is_trading_session`, `get_current_session` | **1.9** (Live-relevant → Phase 1) |
| F10 (P1) | Konkrete Parität-Abweichung: EMA-Pullback liefert im `fast_sim`-Provider PRE_SIGNAL, in `check_signal` SHORT (gleicher Präfix) | extern reproduziert; eigener Paritätstest steht aus | **T4** (konkretisiert) |
| F11 (P1) | Notfall-Close nach fehlgeschlagenem SL kehrt mit `return None` zurück, ohne den `entry_order_registry`-Eintrag aufzulösen → Watchdog kann später eine fremde Position über Symbol/Seite/Alter „adoptieren“ | `bitunix_trade.py` Z. 2465–2467, `entry_order_registry.resolve` nur Z. 1612/2642 | **1.8** |
| F13 (P1) | Bestätigungszähler zählt den **aktuellen** Vorschlag und `superseded`-Vorgänger mit (kein ID-Ausschluss, kein Statusfilter) → 25 unveränderte Trades + 2 Vorschläge = 3 Bestätigungen → Auto-Freigabe | `ai_engine_governance.py` Z. 129–135 | **2.8** (präzisiert) |
| F14 (P2) | Gültige Nullwerte werden durch Defaults ersetzt (`volume_ratio or 1.0`, `range_pos or 50.0`) | `ai_ml_lab.feature_row` Z. 128–129, analog `ml_gate.gate_feature_row` | **2.2** |
| F15 (P2) | `toggleNotification` prüft `response.ok` nicht → Erfolgs-Toast trotz 401/500 | `frontend/src/App.js` Z. 232–237 | **1.5** (Frontend-Teil) |
| Hinweis | `risk` NICHT umbenennen (andere Module erwarten Preisabstand), sondern **additiv `initial_risk_usdt`** | – | **2.3** (Feldname übernommen) |
| Hinweis | Prozesslokale Locks/Zähler (Monitor-Lock, Watchdog-Fehlerzähler) setzen Einzelinstanz voraus | – | 2.4 (Sicherheitsstatus dokumentiert Annahme) |

Bewertung des zweiten Berichts: deckungsgleich mit meinem Gegencheck; die neuen Punkte F03/F11 sind
**echte Geldschutz-Lücken** (Position ohne verifizierten SL bzw. Registry-Leiche) und wandern deshalb in
Phase 1 (neuer Schritt 1.8). F09 betrifft den Live-Scanner und ist ein Ein-Zeilen-Fix mit Test (1.9).


- `backend/tests` (`-m unit`): **1261 passed, 7 skipped, 12 failed, 3 errors**.
  - 11 Failures `test_iter38_*` brauchen einen laufenden Dev-Server (bekannt, PRD), 1× `test_fix_custom_ai_trades`,
    1× `test_strategy_insights::daily_quota_cooldown` (bekannt, vorbestehend).
  - 3 Errors `test_regime_lab/worker/stale_regime`: `memory/test_credentials.md` fehlte (jetzt lokal angelegt, gitignored).
- `/app/tests` (Skript-Stil, Asserts auf Modulebene): **121 passed, 18 failed, 14 errors** – Fehler stammen aus
  Live-Server-Abhängigkeit, Zeitabhängigkeit (Wochenend-/Marktpause-Asserts) und Settings-Defaults
  (`assert 2.5 == 4.0` Fee-Guard-Mult). → eigener Punkt T1 im Plan.
- Beide Ordner heißen `tests` → gemeinsamer Lauf kollidiert (`ModuleNotFoundError tests.test_…`); getrennt laufen lassen.

---

## 3. Eigene Zusatzbefunde (nicht in der externen Bewertung)

| # | Befund | Ort | Schwere |
|---|---|---|---|
| E1 | **Kein Gesamt-Risikolimit** über gleichzeitig offene Positionen (nur `max_open_trades` je Welt, Kapital-Cap). Korrelierte Krypto-Longs können das Tagesverlustlimit in einem Move sprengen | `ai_engine.py` ~2091, `position_sizing.py` | hoch |
| E2 | Kill-Switch-Referenzkapital `ref_capital=0` → Summe `max_capital` **der heutigen Trades**; ohne Trades = 100 USDT. Ein einzelner −6 USDT Trade auf 100 USDT Marge triggert 6 % → Kill-Switch. Sinnvoller: Equity (Börsen-Guthaben) | `trade_guard.py` Z. 243–245 | mittel |
| E3 | Kill-Switch pausiert nur bis Mitternacht **UTC** – Verlust um 23:50 UTC = 10 Minuten Pause | `trade_guard._next_midnight_utc` | mittel |
| E4 | `manual_close` prüft `live_exchange_ready` und schließt sonst **lokal** (auch bei `mode=live` ohne Client) – Pfad sauber absichern | `bitunix_trade.py` Z. 3933 | mittel |
| E5 | CORS `*` + `allow_credentials=True`; Token 24 h im `localStorage`; kein Refresh/Revoke | `server.py` Z. 433, `frontend/src/auth.js` | mittel |
| E6 | Login ohne Rate-Limit/Lockout; Passwortvergleich `==` statt `hmac.compare_digest` | `routers/auth.py` | mittel |
| E7 | Playbook-Urteile (`verdict`, Promotion `MIN_TRADES_PROMOTE=5`) bei n=5 rein zufällig (Konfidenzintervall 5 Trades ≈ ±40 pp). Keine Signifikanz-/Unsicherheitsangabe | `ai_playbook.py`, `setup_lifecycle.py` | mittel |
| E8 | Reward `pnl_pct/2.5` auf ±4 geklemmt → alles über ±10 % Margin ist gleich; mit hohem Hebel ist fast jeder Trade „geklemmt“ → Reward trägt kaum Information | `ai_rewards.py` Z. 40 | niedrig |
| E9 | Zwei Test-Ordner mit Skript-Asserts; keine CI; zeitabhängige Tests | `tests/`, `backend/tests/` | niedrig |

---

## 4. Umsetzungsplan (priorisiert, architekturkonform)

Grundregeln für alle Phasen:
- Nur bestehende Module erweitern oder **ein** neues Service-Modul je Fachthema (`backend/services/…`, rein & testbar).
- Jede Änderung mit Feature-Flag/Setting (Default = neues, sicheres Verhalten, alt abschaltbar) und
  Regressionstests **vor** dem Code (Reproduktion → Fix → Test grün).
- Keine Änderung an Ordnerstruktur, `server.py`-Routing-Prefix, Env-Namen. Neue Env-Variablen nur additiv.
- Ausführung lokal nur gegen lokale Mongo mit `AI_TRADER_LOCAL_DISABLE=1`; Bitunix-Keys lokal nur für
  **read-only** Probes (Guthaben/Positionen), niemals Order-Endpunkte.

### Phase 0 – Sofortmaßnahmen ohne Code (heute)
| # | Maßnahme |
|---|---|
| 0.1 | Auf Render `JWT_SECRET` setzen (≥ 48 Zufallszeichen). Alle bestehenden Tokens werden ungültig → einmal neu einloggen |
| 0.2 | **Alle im Klartext geteilten Zugangsdaten rotieren**: Admin-Passwort, Bitunix-Key/Secret (Order-Recht neu erzeugen, IP-Whitelist Render), Telegram-Bot-Token, Supabase-Service-Role, Mongo-Atlas-User, LLM-Keys. Diese Werte waren in Chat/Prompt sichtbar |
| 0.3 | Bitunix-Key auf **Futures-Trade** beschränken (kein Withdraw), IP-Whitelist |
| 0.4 | Bis Phase 1 fertig: Live-Kapitalzuweisung nicht erhöhen; `auc_risk_scaling` ausschalten (Setting), da AUC aus leckendem Pfad stammt |

### Phase 1 – Geldschutz korrigieren (🔴, ~1 Iteration)
| # | Maßnahme | Betroffene Dateien | Tests |
|---|---|---|---|
| 1.1 | **Close-Fehlpfad**: nach 5 Versuchen **nicht** `closed`, sondern neuer Status `close_failed` (Trade bleibt „offen/unklar“, `qty_remaining` unverändert, kein PnL, kein Reward/Kill-Switch-Eintrag). Eskalation: Telegram „KRITISCH“, Sicherheitsstatus (Phase 2.4) rot, Watchdog versucht weiter + Positions-Abgleich (`_reconcile_close_error`) jeden Tick. UI zeigt Badge „Close fehlgeschlagen – Börse prüfen“ | `bitunix_trade.py` (monitor-Close, `_after_close`), `position_watchdog.py`, `routers/autotrade.py` (Filter `status in [open, close_failed]`), Frontend Trade-Liste | Unit: 5 Fehlversuche → Status `close_failed`, kein `_after_close`; Reconcile bucht extern-geschlossen korrekt |
| 1.2 | **Kill-Switch modus-rein**: `on_trade_closed` nur `mode == trade.mode`, `data_collection != True`; getrennter State je Modus (`trade_guard_state_live`/`…_paper`, Alt-Key = live für Kompatibilität). Referenzkapital live = Börsen-Equity (`_live_total_balance`), Fallback zugewiesenes Kapital | `trade_guard.py`, `routers/notify.py` (State je Modus), `AITradingPanel`/Guard-Cockpit | Reproduktion aus Abschnitt 1 als Test: Live −100 + Paper +200 → **Trigger** |
| 1.3 | **Lernpflicht durchsetzen**: `check_open_allowed` blockt bei `learning_required` (Flag `forced_learning_enabled`); Resume nur manuell oder nach erfolgreichem Lernlauf | `trade_guard.py` | Unit |
| 1.4 | **Atomarer Close** (Compare-and-Set): alle Close-Pfade `update_one({"id", "status": "open"})` + `matched_count`-Prüfung → nur der Gewinner ruft `_after_close`. Helfer `_finalize_close(trade_id, updates)` in `AutoTradeManager`, von monitor/manual/watchdog/IBKR genutzt | `bitunix_trade.py`, `ibkr_trade.py`, `position_watchdog.py` | Unit mit Fake-DB: zwei parallele Closes → genau ein Hook |
| 1.5 | **Auth-Härtung**: `JWT_SECRET` fail-fast (Start bricht ab, wenn fehlt/`change-me` und `ENV != dev`; lokal Warnung); `require_admin` auf `GET /autotrade/balance`, `/autotrade/capital`, `POST /analytics/ai-review`, `/analytics/clear/preview`, `/dynamic/*/refresh`, `POST /notifications`; Login: `hmac.compare_digest`, einfacher In-Memory-Lockout (5 Fehlversuche/15 min); CORS auf `CORS_ORIGINS`-Env (Default weiter `*`, aber ohne `allow_credentials`) | `core/auth.py`, `core/config.py`, `routers/*.py`, `server.py`, Frontend: Header/Badge-Fetches mit Token (bereits via `auth.js`) | API-Tests 401 ohne Token; Frontend-Smoke |
| 1.6 | **Gesamt-Risikolimit** (E1): neues reines Modul `services/risk_budget.py`: Summe offenes Risiko (Σ `risk × qty_remaining`) + neuer Trade ≤ `max_portfolio_risk_pct` × Equity, optional Klassen-Cluster-Limit (Krypto/Indizes/…). Eingehängt in den **einen** Einstiegs-Guard (siehe 2.1) | `risk_budget.py` (neu), `bitunix_trade.py` `on_signal` | Unit |
| 1.7 | Kill-Switch-Pause mindestens N Stunden (Setting `min_pause_hours`, Default 6) statt nur „bis Mitternacht UTC“ (E3) | `trade_guard.py` | Unit |

### Phase 2 – Ehrliche Messung & eine Wahrheit (🟠, ~1–2 Iterationen)
| # | Maßnahme | Dateien |
|---|---|---|
| 2.1 | **Zentrale Einstiegsprüfung** `services/entry_guard.py`: eine Funktion `check_entry(signal, cfg, mode)` bündelt Kill-Switch, Anti-Stacking, Fee-Wächter, ATR-Block, Wochenende, Slippage-Guard, Risikobudget, Lernpflicht. Alle Wege (Strategie-Signal, KI, Limit-Fill, manueller Trade, IBKR) rufen sie auf; Ergebnis wird als `entry_checks[]` am Trade gespeichert (Nachvollziehbarkeit). Bestehende Guards bleiben als Bausteine erhalten (kein Neuschreiben) | neu + `bitunix_trade.on_signal`, `key_level_limits`, `routers/autotrade` (manual), `ibkr_trade` |
| 2.2 | **ML-Leckage schließen**: `nearest_snapshot(..., allow_future=False)` → nur `ts ≤ target` (Default), `purged_walk_forward` mit `label_ts = closed_at` (Train nur Trades, die **vor** Teststart geschlossen waren), `ai_ml_lab.train_sync` auf zeitliche Folds; Kalibrierung per verschachtelter WF (Fit auf Folds 1..k−1, Bewertung auf Fold k) | `ai_ml_lab.py`, `ml_gate.py` |
| 2.3 | **R-Kennzahl in Geld**: `risk_usdt = risk × qty` beim Öffnen speichern (`auto_trades.risk_usdt`), Backfill-Migration in `boot_migrations.py`; `shadow_report` und Analytics nutzen `pnl / risk_usdt`; Reward-Basis ebenfalls R statt `pnl/max_capital` | `bitunix_trade.py`, `boot_migrations.py`, `ml_gate.py`, `ai_rewards.py` |
| 2.4 | **Dauerhafter Sicherheitsstatus** `services/safety_status.py` + `GET /api/safety/status` (admin): unbestätigter SL/TP an der Börse, `close_failed`-Trades, Positions-Abgleich älter als X min, veralteter Kurs bei offenem Markt, Watchdog/Preis-Wächter-Heartbeat, Kill-Switch. Zustand `critical` → `entry_guard` blockt neue Risiken. Frontend: Ampel im Header (bestehende Guard-Cockpit-Komponente erweitern) | neu, `position_watchdog`, `price_watch`, `Header.js` |
| 2.5 | **Getrennte Ergebnisdaten je Setup**: `setup_stats` liefert immer `{backtest, collect, paper, live}`; `judge_stats`/Promotion/Weighting nutzen **live** (wenn ≥ N), sonst konservativ (Paper nur als Prior mit Abzug, nie zur Aufwertung über Live hinaus). Konfidenzintervall (Wilson) zu WR ausgeben; Urteile unter n<15 als „unsicher“ kennzeichnen (E7) | `ai_playbook.py`, `setup_lifecycle.py`, `setup_weighting.py`, `SetupMaturityTable.js` |
| 2.6 | **Setup-Gewichtung nach Erwartungswert**: `weight_for` auf geschrumpften **Erwartungswert je riskiertem USDT** (Mittel R netto, Shrink gegen 0) statt Trefferquote; Trefferquote nur als Tiebreaker. Test: 90/100 Gewinner mit −500 → Gewicht < 1 | `setup_weighting.py` |
| 2.7 | **Reward entkoppeln**: Konfidenz-Bonus/Malus aus `compute_reward` entfernen (Schalter, Default aus); stattdessen getrennte Kennzahl „Kalibrierungsfehler“ (\|conf/100 − Trefferquote im Konfidenz-Bin\|) im Reward-Panel. Basis R netto (2.3), Regelverletzungen separat (Liste `violations`) | `ai_rewards.py`, `AIRewardPanel.js` |
| 2.8 | **Echte Bestätigungen**: `confirmations` in Governance zählen nur, wenn seit letztem Vorschlag ≥ N **neue** geschlossene Trades bzw. ein neues, nicht überlappendes Fenster vorliegt (`evidence_since_ts`) | `ai_engine_governance.py`, `validation_gate` |
| 2.9 | **Handelszeiten-Filter über Mitternacht** in `detectors.apply_filters` (`hour_from > hour_to` → Wrap) + Test | `setup_backtest/detectors.py` |
| 2.10 | **Shadow ehrlich**: `auc_risk_scaling` Default **aus**; wenn an, dann Kennzeichnung „ML-Skalierung aktiv“ im Trade und im Status; Shadow-Report nur aus leckagefreiem Training (2.2) | `ml_gate.py`, UI |

### Phase 3 – Champion vs. Kandidat & Versionszuordnung (Kernfeature, ~2 Iterationen)
| # | Maßnahme |
|---|---|
| 3.1 | **Entscheidungs-Fingerprint**: jede `ai_decision` und jeder Trade speichert `policy_version = {prompt_hash, lessons_hash, playbook_version, model, gate_version, sizing_params_hash}` + `entry_market_snapshot` (bereits vorhanden, Fix 0.2). Auswertung nach `policy_version`, nicht nach Zeitfenster (`setup_variant.py` erweitern, nicht ersetzen) |
| 3.2 | **Kandidaten-Modus** `services/policy_lab.py`: Änderungen an Prompt/Lektionen/Setup-Parametern erzeugen eine **Kandidaten-Policy**. Champion handelt weiter (live/paper wie bisher). Kandidat läuft parallel als **Shadow-Entscheidung** auf denselben Marktsnapshots (nur Paper-Simulation, `paper_execution` mit Kosten), Ergebnisse in `policy_trials`. Token-Kosten begrenzen: Kandidat nur bei jedem k-ten Zyklus oder auf gespeicherten Snapshots (Replay) |
| 3.3 | **Promotion-Regel** (rein, testbar): Wechsel nur wenn n ≥ N (z. B. 40), Netto-R-Mittel Kandidat − Champion > 0 mit Bootstrap-Untergrenze > 0, Max-Drawdown nicht schlechter, Bewertung ausschließlich auf Situationen **nach** Erzeugung des Kandidaten. Sonst Verwerfen; automatische Rücknahme bei Verschlechterung nach Promotion (Rollback-Fenster) |
| 3.4 | **Netto-Erfolgsbericht je Policy-/Strategie-Version**: `GET /api/analytics/policy-report` – PnL netto (Fees, Funding, Slippage) je Version, Betriebskosten (Token) separat, Live/Paper/Sammel getrennt. UI-Tab im Analytics-Panel |
| 3.5 | **Portfolio-Backtest** (später): gemeinsames Kapital, gleichzeitige Signale, Korrelation, Ausfall-/Slippage-Szenarien – aufbauend auf `backtester.py`/`parallel_sim.py`, als eigener Modus, keine Änderung bestehender Läufe |

### Phase 4 – Test-Hygiene & Betrieb (parallel, klein)
| # | Maßnahme |
|---|---|
| T1 | `/app/tests` Skript-Asserts in Funktionen kapseln, zeitabhängige Tests mit festem `now` versorgen; getrennte Aufrufe dokumentieren (`tests/README_TESTING.md`) |
| T2 | Kleine GitHub-Action: `pytest backend/tests -m unit` + `pytest tests -n 0` bei jedem Push (kein Deploy-Eingriff) |
| T3 | Read-only **Prod-Probe** `scripts/prod_safety_probe.py`: offene Positionen Börse ↔ `auto_trades` abgleichen, `live_close_failed`-Trades, Kill-Switch-Zustand – als Vorher/Nachher-Kontrolle für Phase 1 |
| T4 | Strategie-Pfad-Parität: Property-Test, der für jede Built-in-Strategie `check_signal` gegen `fast_sim.provider_for` auf identischen Kerzen vergleicht (klärt den offenen Befund F5) |

---

## 5. Risiken & Rückwärtskompatibilität

- **Status `close_failed`** ist neu → alle Listen/Aggregationen, die `status in (open, closed)` annehmen, müssen
  ihn kennen (Watchdog, Balance, Equity, `used_margin`). Plan: `close_failed` zählt bei Margin/Exposure wie *offen*,
  bei PnL/Statistik **gar nicht** bis geklärt.
- **Kill-Switch je Modus**: bestehender State-Key bleibt für Live (keine Migration nötig), Paper bekommt eigenen Key.
- **JWT fail-fast** kann den Render-Start blockieren, wenn Env fehlt → Phase 0.1 **vor** Deploy; lokal nur Warnung.
- **GET-Endpunkte hinter Auth**: Frontend-Header-Badge lädt `balance/capital` bereits mit Token-fähigem Fetch
  (`auth.js`) – prüfen, dass Cold-Start ohne Token kein Fehler-Spam erzeugt (ErrorBoundary vorhanden).
- **Setup-Gewichtung/Reward-Änderung** verändert KI-Verhalten leicht → als Kandidat (Phase 3) statt sofort
  live, oder mit Schalter + Beobachtungswoche.
- **Prod-DB**: Alle Tests/Probes nur gegen lokale Mongo (tests/README_TESTING.md, Datenverlust 05./06.09.).

---

## 6. Antwort auf die Kernfrage „Grundsätzliche Logikprobleme oder nur Bugs?“

Beides – und die externe KI hat Recht, dass es **nicht nur Aufräumen** ist:
1. **Bugs** (klar falsch, klar behebbar): Close-Fehlpfad, Modus-Mischung im Kill-Switch, JWT-Default, Snapshot-
   Lookahead, R-Formel, Mitternachts-Filter, fehlendes Compare-and-Set.
2. **Logik-/Anreizprobleme** (Design): Trefferquote statt Erwartungswert, Konfidenz im Reward, Selbstbestätigung,
   Paper als Aufwerter, Version = Zeitfenster. Diese erzeugen einen Bot, der sich **selbst besser bewertet**, als er ist.
3. **Fehlender Nachweis**: Es gibt kein Instrument, das „Änderung“ von „Verbesserung“ unterscheidet →
   Champion-vs.-Kandidat ist die wichtigste neue Funktion; alles davor macht sie erst messbar.

Empfohlene Reihenfolge = Plan-Phasen 0 → 1 → 2 → 3. Kein Kapital-Ausbau vor Abschluss von Phase 2.
