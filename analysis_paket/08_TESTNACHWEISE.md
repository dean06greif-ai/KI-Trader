# 08 Testnachweise – Agentenprüfung und abschließende Nachprüfung

Stand: Commit-Quelle `/app/review_source @792ff0ac44861df447d3e619042ecef1704c37c5`  
Scope: **Backend-only / offline / isoliert** (kein Frontend, kein Serverstart, keine DB/Broker/LLM-Calls)

## Maßgeblicher finaler Stand

Nach der zweiten Agenteniteration wurden T02/T03 von nachmodellierten Branches auf **AST-extrahierte Originalbranches** umgestellt. Zwei fehlende Fake-Abhängigkeiten bzw. eine zunächst zu unspezifische Branchauswahl wurden ausschließlich im Testharness korrigiert. Die finale Wiederholung ist erfolgreich; keine Produktdatei wurde geändert.

- Gezielte Suite: **25 passed, 3 xfailed, 0 failed**; `test_reports/final_offline.xml`.
- Erweiterte Baseline: **59 passed, 3 deselected, 0 failed**; `test_reports/baseline_additional.xml`.
- Somit **84 bestandene Prüfungen** in diesen zwei maßgeblichen Läufen. Die früher separat ausgeführten4 Einzeltests sind Teil der59 und werden nicht doppelt gezählt.
- Die3 xfails sind2 erwartungsgemäß nicht erfüllte TP-Solltests und1 offene R05-Forschungsprobe.
- T02/T03 führen nun Originalcodebranches aus, weiterhin **nicht** die gesamte Order-/Brokerkette.
- Die Floating-Loss-Fixture besitzt im finalen Test konsistente OHLC-Werte.
- `dotenv` wird auch neutralisiert, wenn ein Plugin es bereits importiert hatte. Finale Läufe deaktivieren automatisches Pytest-Pluginladen und aktivieren nur den benötigten AnyIO-Runner.

### Finale Befehle (in der Analyseumgebung)

```bash
KI_TRADER_SOURCE_DIR=/app/review_source/backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p anyio.pytest_plugin \
 /app/analysis/tests/test_offline_extended_findings.py \
 /app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py \
 --confcutdir=/app/analysis/tests -q --tb=short \
 --junitxml=/app/test_reports/pytest/final_offline.xml

KI_TRADER_SOURCE_DIR=/app/review_source/backend PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p anyio.pytest_plugin /app/analysis/tests/baseline \
 --confcutdir=/app/analysis/tests -q \
 -k 'not performance_on_intraday_history and not scenario_bank_all_plausible and not wiring_decision_signal_trade' \
 --junitxml=/app/test_reports/pytest/baseline_additional.xml
```

### Erweiterte bestehende Baseline

Unveränderte Kopien von `test_regime_engine.py`, `test_regime_v2_and_gate_domain.py`, `test_regime_gate.py`, `test_policy_fingerprint.py`, `test_risk_budget.py` unter `tests/baseline/`.

Bewusst nicht ausgewählt: großer Performancefall, Szenariobank mit zusätzlicher Fixtureabhängigkeit, ein breiter Import-/Verdrahtungstest. Dies ist kein vollständiger Lauf aller bestehenden Unit-Tests.

Warnungen: Legacy-K-Means erzeugte `invalid value encountered in divide` (`regime.py:301`) → als **R16** aufgenommen, nicht ignoriert. In der gezielten Suite gab es zusätzlich einen Bibliotheks-Deprecationhinweis zur `python_multipart`-Importweise; keine Änderung von Bibliotheksversionen im Produkt vorgenommen.

Testumgebung (keine Empfehlung für ein Produktupgrade): Python3.11-Umgebung; pytest9.1.1, anyio4.15.1, numpy2.4.6, pandas3.0.5, fastapi0.110.1, motor3.3.1, aiohttp3.14.3. Ergebnisse gelten für diese lokale Testumgebung, nicht als Beweis identischer Produktivabhängigkeiten.

Die nachfolgenden Abschnitte dokumentieren die vorherige Agenteniteration; bei Abweichungen gilt der finale Stand oben.

## Isolations-Setup
- Tests ausschließlich unter `/app/analysis/tests` ausgeführt.
- `pytest --confcutdir=/app/analysis/tests` verwendet.
- Import-/Laufzeit-Netzwerk im Testprozess via Socket-Blocker gesperrt.
- Umgebungsvariablen mit Credential-Mustern im Testprozess entfernt.
- `dotenv.load_dotenv` im Testprozess neutralisiert (keine `.env`-Nutzung durch Testharness).
- Verlagerung durchgeführt:  
  `/app/review_source/backend/tests/test_regime_dynamic_r01_r13_regressions.py` → `/app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py`

## Lauf A – Isolierte Regressionssuite
Command:
`pytest -v /app/analysis/tests --confcutdir=/app/analysis/tests --junitxml=/app/test_reports/pytest/iteration_2_offline.xml`

Ergebnis: **25 passed, 3 xfailed, 0 failed**

### Neue Befunde (T01–T05, R08, R05, R14)

#### T01 – Watchdog-Adoption kann fremde Vollposition schließen
- Test: `test_t01_watchdog_adopt_closes_full_foreign_position_without_identity_proof`
- Status: **passed** (Reproduktionsbeweis)
- Repro-Werte:
  - kürzlich geschlossener BTC LONG vorhanden
  - neue Position `position_id=new-foreign-pos-99`, `qty=5.0`, `reg=None`, `registry.find_match=None`
- Beobachtung:
  - `flash_close(..., full=True)` wird für die neue Position aufgerufen, ohne Identitäts-/Dust-Nachweis.

#### T02 – Recovery >=50% untracked akzeptiert, Qty-Semantik bleibt voll
- Test: `test_t02_exception_recovery_branch_accepts_half_fill_but_keeps_planned_qty_semantics`
- Status: **passed** (Branch-Repro)
- Repro-Werte:
  - `qty=10`, `untracked=6` → recovered `{"code":0}`
  - `qty=10`, `untracked=4` → reject-Pfad
- Beobachtung:
  - Bei 60% untracked wird Erfolgsantwort gesetzt, geplante qty bleibt 10.

#### T03 – SL-Check: `_ensure_live_sl == None` setzt `sl_exchange_missing` nicht
- Test: `test_t03_sl_verification_none_does_not_set_sl_exchange_missing_and_countercases`
- Status: **passed**
- Gegenfälle:
  - `sl_ok=None` → `sl_exchange_missing=False`
  - `sl_ok=True` → `False`
  - `sl_ok=False` + Close-Fehler → `True`

#### T04 – `_setup_live_gate` lässt in mehreren Pfaden durch
- Test: `test_t04_setup_live_gate_allows_missing_setup_exception_path_and_default_bypass`
- Status: **passed**
- Repro-Werte:
  - missing `setup` → `None` (durchgelassen)
  - Exception in `cached_setup_stats` → `None` (durchgelassen)
  - Bypass default: confidence 70, min_conf 65, opened_today 0 → `live_gate_bypass=True`

#### T05 – Policy-Fingerprint unvollständige Policy-Identität
- Test: `test_t05_policy_fingerprint_ignores_min_conf_fee_guard_and_regime_config_but_tracks_real_sizing_change`
- Status: **passed**
- Beobachtung:
  - Änderungen an `min_confidence`, `fee_guard_enabled`, `regime_mode` ändern `sizing_hash`/`combined` nicht.
  - Echter Sizing-Change (`risk_per_trade_pct`) ändert Hash.

#### R08 – Optimistischer Same-Bar-TP-PnL + offene Trades fallen aus Metrics
- Tests:
  - `test_r08_same_bar_tp1_tpf_characterizes_optimistic_pnl[...]` (LONG/SHORT)
  - `test_r08_open_trade_with_floating_loss_and_entry_fee_still_reported_as_zero_metrics`
  - Soll-Abnahme: `test_r08_acceptance_same_bar_tp_should_be_sequential_not_optimistic[...]`
- Status:
  - Charakterisierung: **passed**
  - Soll-Abnahme: **xfail (strict)**
- Repro-Werte:
  - LONG: entry 100, TP1 101, TPF 103, SL 90, qty 2, partial 50%, fee 0, bar high 104/low 99
  - Beobachtet: aktueller PnL 6 (statt sequenziell 4)
  - SHORT symmetrisch: ebenfalls 6 statt 4
  - Offener Trade-Endfall mit Floating-Loss und Entry-Fee-Kontext berichtet `trades=0, pnl=0, fees=0`

#### R05 – Prefix-Invarianz (kausal) vs. retrospektive Final-Labels
- Test: `test_r05_prefix_invariance_causal_vs_retrospective_labels_with_fixed_model_and_config`
- Status: **xfail**
- Ergebnis:
  - Kausale Labels (`classify_series`) sind prefix-invariant bei fixem Modell/Config (nachgewiesen).
  - Forschungsanteil „final_labels ändern sich durch Future-Append“ war auf diesem synthetischen Datensatz nicht robust nachweisbar → xfail markiert, **nicht als behoben gewertet**.

#### R14 – Candle-Cache Tail-Start kann letzte offene Minute nicht aktualisieren
- Test: `test_r14_candle_cache_tail_start_skips_last_open_minute_update`
- Status: **passed**
- Repro-Werte:
  - Cache-Ende `ts=70000`, Tail-Start wird `70000+60000`
  - Update derselben letzten Minute (`ts=70000`) wird nicht erneut angefordert
- Beobachtung:
  - Letzte Kerze bleibt mit altem Wert im Cache.

### Re-Run der 16 bestehenden Charakterisierungstests (R01–R13-Datei)
- Datei: `/app/analysis/tests/test_regime_dynamic_r01_r13_regressions.py`
- Status: **16/16 passed** (Reproduktionsbeweise, keine Qualitätsfreigabe)

## Lauf B – Auswahl bestehender reiner Unit-Tests (ohne conftest)
Command:
`PYTHONPATH=/app/review_source/backend pytest -v --noconftest ...`

Ausgeführt:
- `test_policy_fingerprint.py::test_short_hash_key_order_stable` → passed
- `test_policy_fingerprint.py::test_sizing_hash_reacts_only_on_sizing_keys` → passed
- `test_risk_budget.py::test_trade_risk_usdt` → passed
- `test_risk_budget.py::test_open_risk_filters_mode_and_collection` → passed

Ergebnis: **4 passed**

## Grenzen / Stub-Grenzen
- T02 und T03 enthielten in der Agenteniteration nachmodellierte branch-nahe Reproduktion; der finale Stand oben ersetzt sie durch AST-extrahierte Originalbranches. Der vollständige Live-Order-Flow bleibt außerhalb Scope.
- T04 wurde per AST-extrahierter Originalmethodik mit injizierten Fake-Abhängigkeiten geprüft (kein Import von `ai_engine`-Modul notwendig).
- R05-Forschungsanteil (final_labels-Änderung) ist als **unbewiesen auf diesem Datensatz** markiert (xfail).

## Frontend-Hinweis (Scope-Korrektur)
- Frontend war hier explizit out-of-scope.
- Der frühere Placeholder-Befund aus iteration_1 wird als **false positive für die Nutzerwebsite** zurückgenommen.
