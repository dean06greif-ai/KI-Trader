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

## Paket AP05 – Reproduzierbare Datenstände & saubere Candlegrenzen ✅ (26.06.2026)
- Bezug: Befunde **R06, R07, R11, R14, R16** · Basis: Branch `conflict_150926_2127`.
- Vorgefundener Stand: Die komplette AP05-Implementierung lag bereits im Repo
  (abgebrochene Session, Commit `3b55e7e`, nicht dokumentiert). Diese Session hat
  den Stand VERIFIZIERT, einen Bug im Solltest behoben und abgenommen.
- Implementierung (verifiziert):
  - `backend/services/research_dataset.py` (NEU, rein): `candles_hash`
    (IEEE754-binär, kein Rundungsdrift), `symbol_manifest`, `dataset_manifest`,
    `verify_histories` (Abweichung ⇒ verständliche Begründung je Symbol),
    `dataset_status` (`pinned` vs. `legacy_unpinned`).
  - `backend/services/regime_lab.py`:
    - R06 `fetch_histories(start_ts=, dataset=)`: Anker aus gespeicherten
      `bounds` fixieren das Datenfenster; Manifest-Abweichung ⇒ ERKLÄRTER
      RuntimeError statt stiller Ergebnisdrift. `run_analysis` speichert das
      Manifest (`dataset`) an jeder neuen Analyse.
    - R07 `_segments_payload`: `to_ts` = letzte Kerze des Segments (inklusiv),
      additiv `end_exclusive_ts`; `segments_from_ranges` liest neue Dokumente
      halboffen (`bisect_left(end_exclusive_ts)`), Altdokumente unverändert
      über `bisect_right(to_ts)` (Schemaadapter, Legacy-Verhalten erhalten).
    - R11: Aggregation in fetch_histories mit `drop_partial=True`.
  - `backend/services/regime_opt.py`: `_build_regime_segments` +
    `run_walkforward` übergeben Anker + Manifest (Manifest-Prüfung nur bei
    gleichem Timeframe – anderer TF lädt dieselben Anker, andere Aggregation).
  - `backend/services/candle_cache.py` (R14): Tail-Reload überlappend
    (3 min), `_merge_tail` ersetzt überlappende Zeitstempel – eine beim Cachen
    noch offene 1m-Kerze wird mit ihrem endgültigen Stand überschrieben.
  - `backend/services/dynamic_live.py`, `regime_gate.py` (R11): alle
    Regime-Aggregationen `drop_partial=True` (Teilkerze ist kein Regime-Input).
  - `backend/services/regime.py` (R16): serialisierte `norm_std` ≥ 1e-9
    (Rundung drückt Mindest-Std nicht mehr auf 0); `classify_point`/
    `classify_matrix` fluten Alt-Modelle mit std=0 (kein divide-by-zero).
  - `backend/routers/regime_lab.py`: `list` liefert additiv `dataset_status`
    (`legacy_unpinned` für Bestandsanalysen – nichts wird rückwirkend
    „validiert“).
- Fix dieser Session: `tests/analysis_regression/test_ap05_dataset_candles.py::
  TestR14CacheOverlapReload::test_get_candles_refetches_with_overlap` –
  Fake-Quelle erzeugte durch RNG-Verbrauch (n=12 vs. n=10) eine DIVERGIERENDE
  Historie; jetzt Basis+2 neue Kerzen (deterministisch). Produktionscode war korrekt.
- Testnachweise: Offline-Solltests **87 passed** (74 + 13 AP05);
  Unit-Suite **1553 passed**, 2 skipped (ohne iter38-Umgebungstests).
- Migrationswirkung: Bestandsanalysen bleiben lesbar (Legacy-Reader),
  erhalten aber sichtbar `legacy_unpinned`; nur NEUE Analysen sind gepinnt.
- Rückfall: Manifest-Prüfung greift nur, wenn `dataset` am Dokument existiert;
  kein bestehender Workflow ändert sich ohne neues Analyse-Dokument.

## Paket AP06 – Referenzsimulation: ehrliches Positions-/Kostenmodell ✅ (26.06.2026)
- Bezug: Befund **R08** (Kern von AP06; R12 bereits in AP03/AP04 abgedeckt,
  W02-Statistik folgt in AP09) · Basis: Branch `conflict_150926_2127`.
- Geänderte Dateien & Verhalten:
  - `backend/services/backtester.py::simulate_pair`:
    - R08-2 Same-Bar TP1+TPFull: TP1-Teilverkauf wirkt jetzt AUCH, wenn TPFull
      in derselben Kerze erreicht wird (TP1 liegt näher am Entry und wird auf
      dem Weg zuerst berührt). Befund-Fixture Long 100→TP1 101/TPFull 103,
      50 %, Fee 0: **+4 statt +6**; Short spiegelbildlich. Same-Bar-Priorität
      bleibt konservativ: SL/Liquidation VOR jedem TP.
    - R08-1 Offene Endposition: wird am letzten verfügbaren Schlusskurs
      Mark-to-Market geschlossen (inkl. Exit-Gebühr) statt stillschweigend
      verworfen (vorher: schwebender Verlust + Entry-Fee = trades=0/pnl=0).
      Additive Felder: Trade-Row `end_forced`, Report `end_forced_trades`.
      PnL/Fees/Drawdown/Winrate enthalten diese Trades jetzt ehrlich.
    - R08-3 NEU Parameter `entry_allowed_from_ts` (additiv): Warmup baut nur
      Indikatoren auf, Entries erst ab dem Anker. Modellannahme dokumentiert:
      Close-on-close-Fill auf der Signalkerze.
  - `backend/services/dynamic_strategy.py::simulate_segment` +
    `backend/services/parallel_sim.py::sim_segment_task`: übergeben
    `entry_allowed_from_ts=Segmentstart` – ein Warmup-Trade kann keinen Entry
    im eigentlichen Segment mehr blockieren; die am Segmentende (=
    Regimewechsel) offene Position wird gemäß Docstring jetzt wirklich
    geschlossen (end_forced), beide Pfade (sequenziell/Kind-Prozess) identisch.
  - Geprüft, KEINE Änderung nötig: `setup_backtest/simulator.py` ist bereits
    konservativ korrekt (SL zuerst, TP1-Teilverkauf mit `continue` – TPf
    frühestens Folgekerze, Zeit-Exit rechnet Restmenge ab);
    Fast-Sim nutzt dieselbe simulate_pair-Engine (Parität T4 besteht weiter);
    `fee_model` deckt Krypto-/Forex-Gebühren ab. Funding/Spread-Daten liegen
    nicht vor und werden lt. Plan NICHT erfunden (dokumentierte Modellgrenze).
- Solltests: `tests/analysis_regression/test_ap06_reference_sim.py` (9 neu:
  +4-Fixture Long/Short, SL-Priorität Same-Bar, TP1-then-open, MtM-Endclose
  mit Fees, Flat-Breakeven, kein Phantom-end_forced, Anchor-Skip,
  simulate_segment-Blockade-Nachweis).
- Testnachweise: Offline-Solltests **96 passed** (87 + 9);
  Unit-Suite **1562 passed**, 2 skipped – KEINE Regression (bestehende
  Backtest-/Optimizer-/Dynamic-Tests unverändert grün).
- Migrationswirkung: Backtest-/Optimizer-/Dynamic-Ergebnisse können durch die
  ehrliche Abrechnung SCHLECHTER (realistischer) ausfallen – bewusste
  Entscheidung laut Plan („Ergebnisse dürfen durch ehrliche Kosten schlechter
  werden“). Gespeicherte Alt-Ergebnisse werden nicht angefasst.
- Rückfall: `entry_allowed_from_ts=None` (Default) = altes Entry-Verhalten;
  end_forced-Trades sind über das Flag identifizierbar und herausfilterbar.

## Paket AP07 – Forschungsvalidierung ohne Holdout-Tuning ✅ (26.06.2026)
- Bezug: Befunde **R05, R10** (R09 wurde bereits in AP04 über das
  Release-Statusmodell geschlossen; W02-Blockbootstrap folgt in AP09).
- Geänderte/neue Dateien & Verhalten:
  - `backend/services/research_validation.py` (NEU, rein + Zähler):
    `inner_anchor_ts` (innere Validierung = letzte 25 % des TRAININGS-Fensters),
    `select_best_row` (Auswahl NIE nach Holdout; Fallback Altdaten =
    `train_only`, klar benannt), `evidence_verdict` (<50 Holdout-Bars ⇒
    `insufficient_evidence`), `experiment_manifest` (Suchraum/Datenbezug/
    Rollen-Deklaration VOR Suchstart), `register_attempt` (persistenter
    Versuchszähler `research_attempts` je Suchziel, fail-safe).
  - `backend/services/regime_lab.py`:
    - `_live_agreement`/`_symbol_payload`: additiv `inner_direction_pct`/
      `inner_bars` (Fenster (inner_start, train_end], disjunkt zum Holdout).
    - R10 `run_ema_compare`: **best_period wird über die INNERE Validierung
      gewählt** (vorher max(holdout_direction_pct) = Holdout-Tuning). Der
      Holdout ist jetzt reiner finaler Test (Report). Ergebnis additiv:
      `selection_basis`, `holdout_role=final_test`, `evidence`, `attempt_no`,
      `manifest`, je Zeile `inner_direction_pct`/`holdout_bars`.
    - R10 `run_kombi_calibrate`: Score = innere Trefferquote − Zielband-Strafe
      (vorher Holdout im Score); Holdout nur Bericht; gleiche additive Felder.
  - `backend/services/regime_opt.py`:
    - R05 `run_regime_optimizer`-Ergebnis: `label_basis=
      "retrospective_reference"` – Suchsegmente stammen aus rückblickenden
      Final-Labels (Diagnose/Training, kein handelbarer Beleg).
    - R05/R10 `run_walkforward`-Ergebnis: `label_basis="causal_live"`
      (klassifiziert ausschließlich kausal via classify_series) +
      `attempt_no` (jede Wiederverwendung desselben sichtbaren Holdouts
      wird gezählt und ausgewiesen).
- Solltests: `tests/analysis_regression/test_ap07_research_validation.py`
  (9 neu: Auswahl ignoriert Holdout-Sieger, Fallback=train_only, Anker im
  Trainingsfenster, Evidenz-Verdikt, EMA/Kombi-Verdrahtung (AST), Zähler-
  Inkrement, disjunkte Inner-/Holdout-Fenster, **R05-Präfix-Stabilität der
  kausalen Live-Labels (positiv-numerisch)**, label_basis-Kennzeichnung).
- Testnachweise: Offline-Solltests **105 passed**; Unit-Suite **1571 passed**,
  2 skipped – keine Regression. `/api/health` alive.
- Migrationswirkung: `best_period`/Kombi-`best` können sich gegenüber früher
  ändern (ehrlichere Auswahl); alte gespeicherte Runs bleiben unangetastet.
  UI-Beschriftung der Holdout-Spalten („finaler Test statt Auswahl“) folgt
  gebündelt in AP10 (R17), Backend liefert die Felder bereits.
- Rückfall: Felder sind additiv; Altverhalten wäre nur durch Code-Revert
  erreichbar (bewusst kein Schalter – Holdout-Tuning soll nicht wählbar sein).

## Paket AP08 – Gemeinsamer MarketContext ✅ (26.06.2026)
- Bezug: Befund **R15** (T05-Fingerprint-Erweiterung folgt in AP09 und nutzt
  den hier eingeführten Modell-Fingerprint).
- Geänderte/neue Dateien & Verhalten:
  - `backend/services/market_context.py` (NEU, rein): versionierte Taxonomie
    (TAXONOMY_VERSION 1) mit drei getrennten EBENEN `structural` /
    `setup_context` / `risk_overlay`; `direction_from_regime_id` (Richtung aus
    Regime-ID via split_id – keine Label-Substrings als Identität);
    EXPLIZIT benannter Legacy-Adapter `legacy_phase_from_label` (nur für
    KMeans-Cluster ohne Richtungs-IDs); `observer_context` (Kurzfrist-Regime
    des Observers → eigene setup_context-Zustände `short_term_*`, bewusst
    KEINE strukturelle Identität); `structural_context` mit echten Zuständen
    `ok|stale|unknown`, `confidence_kind="heuristic"` (kein kalibriertes
    Gewinn-Wahrscheinlichkeits-Versprechen) und `model_fingerprint`
    (Provenienz: welcher Artifact-Stand lieferte den Kontext).
  - `backend/services/regime_gate.py`: v2-Modelle → Richtung aus der
    Regime-ID; KMeans → benannter Legacy-Adapter; Antwort additiv um
    `market_context`/`state` erweitert; Erkennungsfehler markiert einen
    abgelaufenen Cache-Stand sichtbar als `stale` (Verhalten bleibt
    fail-open, kein Live-Bruch); `phase_from_label` delegiert an den Adapter.
  - `backend/services/ai_market_observer.py`: Features tragen additiv
    `context_layer="setup_context"` + `taxonomy_version` – Alttrades werden
    NICHT neu gelabelt (nur neue Snapshots tragen die Ebene).
- Solltests: `tests/analysis_regression/test_ap08_market_context.py` (8 neu:
  ID-Richtung über alle Modi 3/5/9, Legacy-Adapter-Grenzen, Observer-Ebene
  getrennt, Feature-Layer, unknown/stale/ok, Fingerprint stabil+sensitiv,
  Gate-stale-Pfad, **gleicher Artifact ⇒ identische Richtung in Lab- und
  Gate-Pfad** (funktional, v2-Modell)).
- Testnachweise: Offline-Solltests **113 passed**; Unit-Suite **1579 passed**,
  2 skipped – keine Regression.
- Rückfall: Alle Felder additiv; Gate-Blockverhalten und Config-Werte
  (`regime_block_phases`) unverändert – nur die Herleitung ist jetzt
  vertraglich statt zufällig.

## Paket AP09 – Policy-/Lernprovenienz, idempotente Ergebnisse ✅ (26.06.2026)
- Bezug: Befunde **T05, T07, T08, W02**.
- Geänderte Dateien & Verhalten:
  - `backend/services/policy_fingerprint.py` (T05): NEU `POLICY_CONFIG_KEYS`
    (min_confidence, collection_min_confidence, fee_guard_*, live_gate_bypass_
    enabled) + `policy_config_hash` – min_confidence gehört ausdrücklich NICHT
    in den Sizing-Hash. `build(..., policy_config_h=, regime_artifact=)` ⇒
    **fingerprint_schema=2** (erweiterter combined); Alt-Aufrufer ohne neue
    Teile liefern unverändert Schema 1 (Alttrades bleiben stabil gruppierbar).
    `ai_engine` übergibt den Config-Hash zyklusweit (neue Decisions = Schema 2).
  - `backend/services/ai_learning.py`:
    - T07 `sync_outcomes`: Decisions-Update kommt jetzt VOR der Markierung;
      Markierung trägt `ai_learn_outcome_version` (reine Funktion
      `outcome_version`: Hash aus PnL/Result/closed_at/Fees/Funding); Fehler
      je Trade ⇒ Trade bleibt unsynchronisiert (Retry im nächsten Lauf).
    - T08 `_bump_lesson_candidate`: erneute Bestätigung derselben Lektion
      zählt NUR mit ≥ N neuen geschlossenen Trades seit der letzten Zählung
      (`lesson_evidence_min_trades`, Default 2; Evidenzbasis `evidence_ts` –
      Verallgemeinerung von real_confirmations aus 2.8). LLM-Wiederholung
      allein erhöht keinen Zähler mehr.
    - T08 `aggregate_performance`: Datensammel-Trades in eigenem Bucket
      `collect`, aus paper/live und `totals.total_pnl` herausgehalten.
  - `backend/services/pnl_reconcile.py` (T07): materielle Broker-PnL-Revision
    setzt `ai_learn_synced=False` ⇒ neue Outcome-Version wird exakt einmal
    nachkonsumiert (kein Hängenbleiben am Boolean).
  - `backend/services/policy_promotion.py` (W02): `bootstrap_diff_lower(...,
    block=)` = Block-Bootstrap über zusammenhängende Trade-Blöcke
    (korrelierte Trades), `promotion_check` nutzt `bootstrap_block` (Default 5).
- Solltests: `tests/analysis_regression/test_ap09_provenance.py` (11 neu:
  Schema-2-Combined ändert sich bei min_confidence-Änderung trotz identischem
  Sizing-Hash, Schema-1-Kompatibilität, Regime-Artifact im Hash,
  Outcome-Version deterministisch/revisionssensitiv, Sync-Reihenfolge (AST),
  Revision-Reset, Collection-Trennung, Lektions-Evidenz-Pflicht,
  Block- konservativer als IID-Bootstrap, Promotion-Determinismus).
- Testnachweise: Offline-Solltests **124 passed**; Unit-Suite **1590 passed**,
  2 skipped – keine Regression (bestehende Promotion-/Learning-Tests grün).
- Migrationswirkung: Neue Decisions tragen Schema-2-Fingerprints (im
  Policy-Report erscheinen sie als neue Version – korrekt, die Policy-
  Beschreibung wurde vollständiger). Alt-Daten unverändert.
- Rückfall: `bootstrap_block=1` stellt IID-Bootstrap wieder her;
  `lesson_evidence_min_trades=0` das alte Zählverhalten.

## Paket AP10 – Labor-Pilot & verständliche UI-Zustände ✅ (26.06.2026)
- Bezug: Befund **R17** · Basis: Branch `conflict_150926_2313` (Commit `054e768c`).
- Vorgefundener Stand: Große Teile von AP10 lagen bereits im Repo (abgebrochene
  Session): Holdout-als-finaler-Test-Beschriftungen, selection_basis/attempt_no/
  evidence in EMA-/Kombi-Karten, dataset_status-Badge in der Liste, WF-Badge
  bestanden/nicht bestanden/nicht geprüft, Worker-Offline-Gate beim Start.
  Diese Session hat die Lücken geschlossen und Solltests ergänzt.
- Geänderte Dateien & Verhalten:
  - `backend/services/research_validation.py`: NEU `walkforward_status(doc)`
    (rein): passed True/False/None + **stale** (Strategie-Zuordnung im
    getesteten Scope wurde NACH dem Walk-Forward geändert/bestätigt).
  - `backend/routers/regime_lab.py::list_analyses`: nutzt den Helper, liefert
    additiv `walkforward_stale`.
  - `frontend/src/components/RegimeLab.js`:
    - Analyse-Liste: 4-stufiger WF-Status „nicht geprüft / bestanden /
      nicht bestanden / **veraltet**“ (testid `wf-status-{id}`).
    - WF-Ergebnisbox: NEU Provenienz-Zeile (testid `regime-wf-provenance-*`):
      Klassifikationsbasis (kausal live vs. rückblickende Referenz, AP07-Feld
      `label_basis`), Versuchszähler `attempt_no`, Testzeitpunkt, Warnung
      „< 10 Trades im Holdout – wenig belastbar“.
    - R17 „negative Werte nicht positiv färben“: die Dynamisch-Karte trägt
      `best`-Styling nur noch bei tatsächlich besserem Verdict.
    - Analyse-Detail-Kopf: NEU Datenversion (testid `regime-detail-dataset`):
      „Daten gepinnt“ (Manifest vorhanden) vs. „Daten nicht gepinnt“.
  - `frontend/src/components/DynamicPanel.js`: tatsächlicher **applied_state**
    (R04-Backendfeld `application_status` war UI-los): Badges „Übernahme
    fehlgeschlagen – Retry“ / „Übernahme blockiert“ (testid
    `dyn-apply-status-{id}`, Tooltip mit Versuch/Zeit/Fehler) + „Stand
    angewendet ✓“ an „Zuletzt übernommen“ (testid `dyn-last-applied-{id}`).
- Solltests: `tests/analysis_regression/test_ap10_ui_states.py` (12 neu:
  walkforward_status alle Fälle inkl. Scope-Trennung/fehlendes created_at,
  Router-Verdrahtung (AST), Quelltext-Regressionen der neuen UI-Zustände).
- Migrationswirkung: nur additive Felder/Anzeigen; bestehende Flows und
  data-testids unverändert.
- Rückfall: reine Anzeige – kein Handelsverhalten betroffen.

## Paket AP11 – Worker-/Rechen- und Strukturvertrag ✅ (26.06.2026)
- Bezug: Befund **W01**.
- Geänderte Dateien & Verhalten:
  - `backend/services/local_exec.py`:
    - NEU `canonical_hash`/`payload_input_hash` (rein, deterministisch,
      Transportschlüssel `_input_hash` ausgeschlossen).
    - `enqueue_compute`: Input-Hash am Job, am LOCAL_JOBS-Meta und im Payload
      (`_input_hash`) – der Worker echot ihn im Ergebnis.
    - `apply_result`:
      1. **Idempotenter Jobabschluss**: Ergebnis für bereits beendeten Job
         (Doppel-Upload, verspätetes Ergebnis nach Cancel) wird verworfen.
         Vorher REALE Lücke: zweiter Upload konnte nach restore_job den
         Terminal-Status überschreiben und DOPPELT persistieren.
      2. **Input-Hash-Prüfung**: Echo ≠ erwartet ⇒ verständliche Ablehnung
         („Ergebnis passt nicht zum Auftrag … neu starten“) statt „done“ mit
         unbrauchbaren Daten. Alte Worker ohne Echo bleiben zulässig
         (kompatible alte Jobs, additiver Vertrag).
      3. **Evidenz**: `job.evidence` {input_hash, result_hash, worker_id,
         worker_version, received_at}; Ergebnis-Dict additiv
         `evidence_hash`/`input_hash` (wandert in die Persistenz).
  - `backend/routers/local_worker.py`:
    - Paketmanifest: NEU `file_hashes` (sha256/16 je Datei), 
      `code_fingerprint` (Gesamt-Hash), `commit` (RENDER_GIT_COMMIT/git).
    - NEU `MODULE_SUBPACKAGES`-Allowlist (rekursiv, nur .py, __pycache__/
      tests/Configs/.env hart gefiltert): `services/setup_backtest` liegt
      jetzt wirklich im Paket (W01: ZIP-Builder verpackte nur unmittelbare
      .py-Dateien); __init__.py aller gepackten Ordner garantiert.
  - `local_worker/worker.py`: VERSION **1.11.0**; alle 3 Rechen-Job-Uploads
    echoen `input_hash` (Daten-Jobs tragen keinen – Prüfung greift nur, wenn
    beide Seiten den Hash haben). REQUIRED_WORKER_VERSION 1.11.0 (alte Worker
    funktionieren weiter für ihre Job-Typen, UI zeigt „outdated“).
- Solltests: `tests/analysis_regression/test_ap11_worker_contract.py` (15 neu:
  Hash-Determinismus, Enqueue-Verdrahtung, Mismatch-Ablehnung, Evidenz,
  Alt-Worker-Kompatibilität, Doppel-/Spät-Upload-Idempotenz, Manifest-Hashes/
  Fingerprint/Commit, rekursive Allowlist nur .py, keine Secrets im ZIP,
  ZIP-Inhalt inkl. Unterpaket, Worker-Echo-Quelltext).
- Bekannte Grenze (ehrlich): „Cloud und Worker rechnen identisch“ ist mit
  Hash-/Paketparität VORBEREITET, aber ohne echten laufenden Worker in dieser
  Umgebung nicht numerisch nachgewiesen (kein Worker-Prozess verfügbar).
- Rückfall: Prüfungen sind additiv (greifen nur bei beidseitigem Hash);
  Verhalten alter Worker/Jobs unverändert.

## Testnachweise (Stand 26.06.2026, nach AP10+AP11)
- Offline-Solltests `backend/tests/analysis_regression/`: **151 passed**
  (124 + 12 AP10 + 15 AP11).
- Backend-Unit-Suite (`pytest tests -m unit`, ohne iter38-Umgebungstests):
  **1617 passed**, 2 skipped – keine Regression.
- Testing-Agent (iteration_6, 26.06.2026): Backend **5/5 grün** (regime-lab/list
  Schema, Manifest-Hashes+Fingerprint+Commit+required_version 1.11.0, ZIP-Inhalt
  inkl. services/setup_backtest + kein .env, localworker/status, Auth 401),
  Frontend-E2E **4/4 grün** (Login, Regime-Lab-Overlay, ECHTE Mini-Analyse
  BTC/15m/30d in ~10 s → Zeile mit `wf-status-*` = „WF nicht geprüft“ und
  Detail-Kopf `regime-detail-dataset` = „Daten gepinnt“, DynamicPanel ohne
  JS-Fehler). Keine Bugs; neue E2E-Datei
  `backend/tests/test_ap10_ap11_regime_and_worker.py` (Env-Creds + Skip ohne
  Backend, auto-markiert live – CI-sicher).
- Hinweis: `/app/memory/test_credentials.md` (gitignored) nach Repo-Import
  neu angelegt – wird von test_regime_lab/test_regime_worker_regression gelesen.

## Paket AP12 – Gestufte Abnahme: Beweispaket ✅ (kodierbarer Kern, 26.06.2026)
- Bezug: AP12-Abnahme „Beweispaket aus Dataset/Release/Reports/Tests/Approval/
  Runtimehealth. Kein beliebiger Profitfaktor als alleinige Freigabe.“
- Geänderte/neue Dateien & Verhalten:
  - `backend/services/strategy_release.py`: NEU `evidence_bundle(doc,
    analysis, safety)` (rein, read-only) + `_wf_for_doc` (Scope-Wahl
    per_coin vor combined): bündelt Release-Status/Fingerprint, Datensatz-
    Manifest (`pinned`/`legacy_unpinned`/`missing_analysis`), Walk-Forward
    (passed/stale/label_basis/attempt_no/Metriken), `application_status` und
    Safety-Level; benennt **Blocker in Klartext**; `ready_for_live` NUR bei
    leerer Blocker-Liste (hoher Backtest-PnL allein genügt bewusst nicht,
    eigener Solltest). Legacy-Bestand = ehrlicher Blocker „ohne
    Validierungsnachweis“, operativ aber weiterhin erlaubt (AP04-Verhalten
    unverändert).
  - `backend/routers/dynamic.py`: NEU `GET /api/dynamic/{id}/evidence`
    (read-only, kein Write-Pfad – per AST-Test abgesichert).
  - `frontend/src/components/DynamicPanel.js`: Button „Beweispaket“
    (testid `dyn-evidence-{id}`) + Panel (testid `dyn-evidence-body-{id}`,
    `dyn-evidence-ready-{id}`): ✓ bereit für menschliche Freigabe ODER
    Blocker-Liste + Kurzfakten (Release/Datensatz/WF/Safety).
- WICHTIG: `ready_for_live` ist eine EMPFEHLUNG – die menschliche
  Live-Freigabe (AP12 Punkt 4) bleibt ausdrücklich Nutzer-Aktion.
- Solltests: `tests/analysis_regression/test_ap12_evidence.py` (12 neu: alle
  Blocker-Pfade einzeln, Scope-Wahl, „guter PnL ersetzt keine Validierung“,
  Read-only-AST, UI-Marker).
- E2E verifiziert: Endpoint mit echter gepinnter Analyse (ra_525a56b1) +
  Test-Strategie → korrekte Blocker (legacy, kein WF), Manifest mit
  bars/hash im Payload; UI-Screenshot: Panel zeigt „Noch nicht abnahmefähig –
  2 Blocker“ mit Klartext. 404 bei unbekannter ID.
- NICHT kodierbar in dieser Umgebung (offene AP12-Restpunkte, Betrieb):
  Shadow-Beobachtungszeit auf aktuellen Daten, Paper-Abdeckung über
  ausreichende Zeit/Regime und die menschliche Live-Freigabe selbst.

## Testnachweise (Stand 26.06.2026, nach AP12-Kern)
- Offline-Solltests: **163 passed** (151 + 12 AP12).
- Backend-Unit-Suite (ohne iter38-Umgebungstests): **1629 passed**, 2 skipped
  – keine Regression.

## Nächste zulässige Schritte (laut Plan, noch NICHT umgesetzt)
1. **AP12 (operativ, Nutzer/Betrieb):** Shadow-/Paper-Beobachtungszeit auf
   Render laufen lassen; danach menschliche Freigabe für engen Live-Scope –
   Beweispaket-Panel als Grundlage nutzen.
2. **AP13** Gezielte Bereinigung / weiterführende Forschung (P2, erst nach
   Pilotnachweis): Archiv-Katalog alter Experimental-Dateien,
   Indikator-Ablationen, Assetpooling, Unsicherheitskalibrierung.

