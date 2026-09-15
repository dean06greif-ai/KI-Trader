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

## Nächste zulässige Schritte (laut Plan, noch NICHT umgesetzt)
1. **AP04** Beobachtung/Apply/Confirm/Release trennen (CAS-Confirm, idempotenter
   Apply, Draft/Validation/Approval-Status) – baut direkt auf AP03-Resolver auf.
2. **AP05** Reproduzierbare Datenstände (feste Anker/Manifeste, Candlegrenzen, R16).
3. **AP06** Referenzsimulation (Same-Bar-Reihenfolge, offene Endpositionen, Kosten).
4. **AP07–AP09** Forschungsvalidierung, MarketContext, Policy-Provenienz.
5. **AP10–AP13** UI-Zustände, Worker-Vertrag, gestufte Abnahme, Bereinigung.
