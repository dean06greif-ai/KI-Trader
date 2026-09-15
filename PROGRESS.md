# PROGRESS – Umsetzung des Analyseplans (Regime Lab / KI Trader)

> Fortschrittsdatei gemäß Berichtspflicht aus `analysis_paket/06_KI_HANDOFF_STARTPROMPT.md`.
> Bei Abbruch hier weiterlesen: letzter Stand = unterster abgeschlossener Eintrag.

## Rahmen
- Quelle/Basis: Branch `conflict_150926_0200`, Commit `792ff0ac44861df447d3e619042ecef1704c37c5`
  (exakt der analysierte Stand – kein Diff zur Analyse nötig).
- Analysepaket: Branch `conflict_150926_1731` → lokal unter `/app/analysis_paket/` (Referenz).
- Maßgeblicher Plan: `analysis_paket/04_UMSETZUNGSPLAN_FUER_KI.md` + Handoff `06_…`.
  Der Handoff beauftragt als ersten sinnvollen Abschnitt **AP00–AP03** (P0-Sicherheitsfixes).
  AP04–AP13 sind Folgephasen (siehe „Nächste zulässige Schritte“).
- Originalstruktur (backend/, frontend/, local_worker/, ibeam_gateway/, …) unverändert
  beibehalten → weiterhin 1:1 auf Render deploybar.
- SICHERHEIT: Diese Dev-Umgebung läuft mit **lokaler MongoDB und OHNE Broker-/LLM-Keys**
  (bewusst, damit keine zweite Live-Instanz parallel zur Render-Produktion handelt).
  Die Produktions-Env bleibt unangetastet auf Render.

---

## Paket AP00 – Baseline & sichere Offline-Testbasis ✅ (15.06.2026)
- Bezug: Plan AP00.
- Geänderte/neue Dateien:
  - `backend/tests/analysis_regression/conftest.py` (neu): sys.path auf echtes Backend,
    mit `KI_OFFLINE_TESTS=1` Credential-Scrub + deny-by-default Socket-Sperre.
  - `backend/tests/analysis_regression/_fakes.py` (neu): Motor-ähnliche Fake-DB (offline).
  - `/app/memory/test_credentials.md` (neu, Dev-Login für Testsuiten).
- Die Charakterisierungstests des Analysepakets (erwarteten die FEHLER) wurden gemäß
  Plan-Regel 4 nach den Fixes durch Solltests ersetzt (siehe AP01–AP03 unten);
  Original bleibt unter `/app/analysis_paket/tests/` als Referenz erhalten.
- Abnahme: Offline-Suite läuft reproduzierbar mit und ohne Netzsperre (53 passed).

## Paket AP01 – Positionsschutz mit eindeutiger Ownership ✅ (15.06.2026)
- Bezug: Befunde **T01, R03** (Teil von T09-Härtung).
- Geprüfte Call-Sites: `position_watchdog._adopt/_check_position`,
  `dynamic_live.check_one/transition_protect`, `routers/dynamic.py::dynamic_confirm`.
- Geänderte Dateien & Verhalten:
  - `backend/services/position_watchdog.py`:
    - NEU `leftover_evidence()` (rein): Rest-Close nur mit Beleg – identische
      Bitunix-Position-ID ODER (kleine Menge ≤ 25 % der Bot-Menge UND nicht nach dem
      Close eröffnet). Unterschiedliche bekannte Position-IDs ⇒ NIE Close.
      Ohne Beleg wird die Position nur sichtbar übernommen ('Manuell (Bitunix)').
    - SL-Bestätigung setzt zusätzlich `sl_exchange_status="confirmed"` (additiv).
  - `backend/services/dynamic_live.py`:
    - NEU `transition_close_query()` (rein): close_open wirkt NUR auf Trades der
      eigenen Strategien der dynamischen Strategie, nie auf manuelle/fremde (R03).
    - `check_one()`: Übergangsschutz (Locks/Closes) läuft NUR noch im tatsächlichen
      Apply-Zweig (auto_apply && !require_confirmation). Reiner Refresh und offener
      Bestätigungs-Vorschlag haben keinerlei Handelswirkung.
  - `backend/routers/dynamic.py`: `POST /api/dynamic/{id}/confirm` führt den
    Übergangsschutz jetzt als Teil der FREIGEGEBENEN Übernahme aus
    (Antwort additiv um `transition` erweitert). Dismiss bleibt wirkungsfrei.
- Solltests: `tests/analysis_regression/test_ap01_watchdog_ownership.py`,
  `test_ap01_dynamic_apply.py` (Scope-Query, AST-Nachweis „nur im Apply-Zweig“,
  Doppel-Ausführung idempotent). Alt-Test `tests/test_iter46_watchdog_leftover.py`
  auf den neuen Mengenbeleg-Vertrag aktualisiert.
- Rückfall: Ohne Beleg werden Positionen nur sichtbar gemacht (kein Close) –
  konservativer als vorher; kein bestehender Stop wird entfernt.

## Paket AP02 – Fillmenge, Schutzstatus, Risikofrische ✅ (15.06.2026)
- Bezug: Befunde **T02, T03, T06** (AP02a/b/c – bewusst kleine Teiländerungen).
- Geänderte Dateien & Verhalten:
  - `backend/services/bitunix_trade.py`:
    - AP02a NEU `recovered_fill_qty()` (rein): Nach verlorener Order-Antwort wird
      NUR die an der Börse gefundene Menge verbucht (min(geplant, gefunden)),
      nie mehr 100 % geplante Menge bei 60 % Fill. `recovered_qty` in der Response.
    - AP02b NEU `sl_exchange_status_of()` (rein): dreistufig
      `confirmed/missing/unknown`. `_ensure_live_sl`-Ergebnis None (API unsicher)
      setzt jetzt `sl_exchange_missing=True` + `sl_exchange_status="unknown"`
      (vorher fälschlich False = „geschützt“). Feld ist additiv, alte Reader
      (safety_status, Watchdog) funktionieren unverändert.
  - `backend/services/risk_budget.py` (AP02c):
    - Equity unbekannt/0 ⇒ **fail-closed** (neuer Trade blockiert, klare Meldung).
      Altes fail-open nur noch per explizitem Config-Flag `fail_open_no_equity`.
    - Fehlender/ungültiger SL eines offenen Trades zählt konservativ mit
      `unknown_sl_risk_pct` % (Default 2 %) vom Entry statt als 0 Risiko
      (`trade_risk_usdt`/`open_risk` additiv erweitert, Signaturen kompatibel).
- Solltests: `tests/analysis_regression/test_ap02_fill_protection_budget.py`
  (0/49/60/100/150 % Fälle, SL True/False/None, Equity None/0, fail-open-Opt-out).
  Alt-Test `tests/test_risk_budget.py` auf den fail-closed-Sollvertrag umgestellt.
- Migrationswirkung: Live-Verhalten wird KONSERVATIVER (blockiert neue Exposure
  bei unbekannter Equity). Bewusste Entscheidung laut Plan; Opt-out vorhanden.

## Paket AP03 – Deterministischer Resolver für dynamische Strategien ✅ (15.06.2026)
- Bezug: Befunde **R01, R02, R12**.
- Geänderte/neue Dateien & Verhalten:
  - `backend/services/strategy_plan.py` (NEU): reiner Resolver
    `resolve_symbol_plan(doc, symbol, state)` → EffectivePlan mit stabilem
    `plan_hash` (identische Inputs ⇒ identischer Plan/Hash), expliziten Zuständen
    `unmapped/baseline`, Overrides strikt auf OPT_TRADE_KEYS begrenzt;
    `merge_overrides()` leitet die effektive Config IMMER von der Basis ab:
    früher dynamisch gesetzte Keys/Params, die im neuen Regime fehlen, werden
    ENTFERNT (A→B→Baseline == direkte Baseline). Manuelle Keys bleiben (Ownership
    über additive Felder `dynamic_keys`/`dynamic_param_keys`).
  - `backend/services/dynamic_live.py`: `apply_configs` und
    `apply_regime_strategies` nutzen den Resolver (R02-Bereinigung inkl.
    Baseline-Fall); `_apply_strategy_params` entfernt Rückstände über
    `settings['coin_params_dynamic_keys']`; R01 transparent: Discovery-
    Substrategie-Regeln des Regimes werden am Coin-Override als
    `dynamic_sub_strategy` sichtbar hinterlegt (additiv). `applied`-Infos
    enthalten jetzt `plan_hash`/`unmapped`.
  - `backend/services/strategy_scanner.py`: neuer Settings-Key
    `coin_params_dynamic_keys` (additiv, persistierbar).
- Solltests: `tests/analysis_regression/test_ap03_strategy_plan.py`,
  `test_ap01_dynamic_apply.py::TestApplyConfigsResolver` (A/B-Gegensätze,
  Teilconfig, unbelegtes Regime, zwei Assets, manuelle Parameter bleiben).
- Bekannte Grenze (ehrlich): Die AUSFÜHRENDE Umschaltung von Discovery-
  Substrategien im normalen Dynamic-Apply (R01 vollumfänglich) benötigt den
  Release-/Apply-Workflow aus **AP04** – der Resolver stellt die Daten jetzt
  deterministisch bereit, Runtime-Konsum folgt in AP04.

## Testnachweise (Stand 15.06.2026)
- Neue Offline-Solltests: `backend/tests/analysis_regression/` → **54 passed**
  (läuft mit `KI_OFFLINE_TESTS=1 pytest tests/analysis_regression -n 0` und ohne Flag).
- Bestehende Suite (`pytest tests -m unit`): **1465+ passed**, 2 skipped.
  Ausnahme: `tests/test_iter38_watchdog_pnl_playbook_api.py` erwartet einen
  separaten Dev-Server auf `localhost:8055` (umgebungsbedingt, NICHT durch die
  Änderungen verursacht; ohne diesen Server schlagen diese ~13 Tests fehl).
- App-Smoke: Backend `/api/health` alive, Frontend lädt (lokale Dev-Instanz,
  lokale Mongo, keine Broker-Keys).

## Paket AP04 – Beobachtung, Apply, Bestätigung und Release trennen ✅ (26.06.2026)
- Bezug: Befunde **R04, R09, R13, T04** · Commit-Basis: Branch `conflict_150926_2016`.
- Vorgefundener Stand: `services/strategy_release.py` + CAS-Grundlagen in
  `check_one` (pending mit command_id/observed_version/expires_at, blocked-Pfad,
  `_record_application`, `unapply_dynamic`) existierten bereits aus einer
  abgebrochenen Session – aber ohne Router-Anbindung (approve-Endpoint fehlte,
  Confirm prüfte nichts, Apply/Save/Builds ungegated, Delete = Hard-Delete).
- Geprüfte Call-Sites: `routers/dynamic.py` (save/list/apply/confirm/dismiss/
  delete/settings), `routers/regime_lab.py::build/build_nnfx`,
  `dynamic_live.check_one/unapply_dynamic`, `ai_engine._setup_live_gate` +
  `live_gate_bypass_ok`, Frontend `DynamicPanel.js`.
- Geänderte Dateien & Verhalten:
  - `backend/services/strategy_release.py`: NEU `revised_release(prev, doc,
    verdict)` – erneuter Build derselben Definition behält den Release,
    geänderte Definition erzeugt neue Revision (alte Freigabe gilt nicht weiter).
  - `backend/routers/dynamic.py`:
    - `save` setzt `release` (Walkforward-Verdict ⇒ validated, sonst draft;
      Bestandsdokumente ohne Feld bleiben `legacy` OHNE Gate – rückwärtskompatibel).
    - `apply` + `confirm`: Release-Gate (draft/stale ⇒ 409 mit Begründung) und
      `_record_application` (applied/failed, Retry sichtbar).
    - `confirm` = Compare-and-swap: falsche command_id ⇒ 409; abgelaufener
      Vorschlag ⇒ 409 + pending geräumt; Zustandsversion ≠ beobachtete Version
      (neue Beobachtung seit Vorschlag) ⇒ 409 „neu prüfen"; Apply-Fehler ⇒ 400,
      pending bleibt für Retry erhalten; Doppel-Confirm ⇒ 400 (kein 2. Apply).
      Body additiv (`{command_id}` optional – alte Aufrufer funktionieren).
    - NEU `POST /api/dynamic/{id}/approve`: ausdrückliche Freigabe der
      AKTUELLEN Definition (Fingerprint-gebunden; Edit ⇒ wieder stale).
    - `DELETE` = Archivieren: scoped `unapply_dynamic` (nur eigene Overrides/
      Locks), `archived=True`, Wechsel-Protokoll bleibt ERHALTEN (vorher
      gelöscht); `list` filtert archivierte und liefert additiv `release_status`.
  - `backend/routers/regime_lab.py`: `build` setzt Release aus Walkforward-
    Verdict; `build-nnfx` nutzt `revised_release` (idempotenter Re-Build
    behält Freigabe, geänderte Definition ⇒ neue Revision/draft).
  - `backend/services/ai_engine.py` (T04): `_setup_live_gate` bei Exception
    jetzt FAIL-CLOSED (Trade ⇒ Datensammlung statt ungeprüft live);
    `live_gate_bypass_enabled` Default AUS (Bypass = explizites Opt-in;
    bestehende DB-Settings werden weiter respektiert).
  - `frontend/src/components/DynamicPanel.js` (additiv): Release-Badge
    (Validiert/Freigegeben/Entwurf/Validierung veraltet, data-testid
    `dyn-release-{id}`), „Freigeben"-Button (`dyn-approve-{id}`), Confirm
    sendet `command_id`, 409-Detail wird angezeigt + Liste neu geladen.
- Solltests: `tests/analysis_regression/test_ap04_release_confirm.py` (20 neu:
  Release-Statusmodell inkl. stale/legacy/approve/revision, CAS-Confirm alle
  Konfliktfälle, Apply-Gate + approve-Workflow, Archiv mit Log-Erhalt,
  T04 fail-closed + Bypass-Default). Alt-E2E `test_iter12_dynamic_optimizer`
  auf neuen Vertrag (409 ⇒ approve ⇒ apply) umgestellt.
- Test-Hygiene (vorbestehende Fails aus Econ-Session 14.09, NICHT AP04):
  `tests/test_asset_class_setups.py` (Event-Setup-Beschreibungsbudget 230),
  `tests/test_iter44_playbook_classes.py` (Login-Creds aus Env statt
  hartkodiertem Prod-Passwort!), `tests/test_risk_sizing_and_playbook_v2.py`
  (SETUP_ENUM endet seit Event-Setups nicht mehr auf "divergence").
- Migrationswirkung: NUR neue/neu gebaute dynamische Strategien werden gated;
  Bestand (`legacy`) verhält sich unverändert. Auto-Apply blockte draft schon
  vorher (check_one) – jetzt gilt dasselbe konsistent für manuelle Wege.
- Rückfall: Freigabe jederzeit über den Approve-Endpoint/Button; kein
  bestehender Stop/Trade wird durch Gate oder Archivierung angefasst.

## Testnachweise (Stand 26.06.2026, nach AP04)
- Offline-Solltests `backend/tests/analysis_regression/`: **74 passed** (54 + 20 neu).
- Backend-Unit-Suite (`pytest tests -m unit`, ohne iter38-Umgebungstests):
  **1540 passed**, 2 skipped – keine Regression.
- Root-Suite `/app/tests`: **169 passed** (3 vorbestehende Fails behoben, s.o.).
- Testing-Agent (iteration_5, 26.06.2026): Backend **17/17 grün** (Login/401,
  Draft⇒409-Gate, approve⇒apply, validated ohne Gate, Confirm-400, Archiv +
  Log-Erhalt, list-Filter), Frontend-E2E **100%** (Login ⇒ Tools ⇒ Optimizer ⇒
  DynamicPanel: Draft-Badge, Freigeben-Toast, Badge „Freigegeben", Delete).
  Neue E2E-Datei `backend/tests/test_ap04_api_flows.py` (Env-Creds/Fallback +
  Skip ohne Backend – CI-sicher, auto-markiert live).
- App-Smoke: `/api/health` alive, Frontend lädt (lokale Dev-Instanz,
  lokale Mongo, keine Broker-/LLM-Keys).

## Nächste zulässige Schritte (laut Plan, noch NICHT umgesetzt)
1. **AP05** Reproduzierbare Datenstände (feste Anker/Manifeste, überlappender
   Merge nach Candleidentität, UTC-Kerzengrenzen, R16-K-Means-Präzision,
   `legacy_unpinned`-Kennzeichnung alter Analysen).
2. **AP06** Referenzsimulation (Same-Bar-Reihenfolge +4 statt +6, offene
   Endpositionen im Report, ehrliche Kosten, Paritätsgrenze Fast-Sim).
3. **AP07–AP09** Forschungsvalidierung ohne Holdout-Tuning, gemeinsamer
   MarketContext, Policy-/Lernprovenienz.
4. **AP10–AP13** UI-Zustände, Worker-Vertrag, gestufte Abnahme, Bereinigung.

