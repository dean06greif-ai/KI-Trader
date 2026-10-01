# PLAN: Regime-Cockpit, KI-Regime-Kontext & Aufräumen (Stand 18.09.2026)

Ausgangsbefund (read-only Probe `backend/scripts/probe_regime_bridge_prod.py`, Prod-Atlas 18.09.):
- 12 Lab-Analysen, **0 Freigaben** → Struktur-Brücke (Prompt-Block, Gate-Quelle `lab`, Reward-Split) hat noch nie gewirkt.
- KI-Trader sieht nur das Kurzfrist-Regime des Market-Observers (1m, EMA20/50) – eine andere Welt als der Lab-Detektor.
- Keine Regime-Bilanz (Winrate/PnL je Regime) und keine Erkennungs-Note im KI-Prompt.
- Lab-„Regime-Prüfung“ besteht bei 12/12 mit 100 % → tautologisch (prüft Labels gegen ihre eigenen Kerzen).
- Einzige aktive dynamische Strategie verweist auf gelöschte Analyse (`retention` hält nur 8 `regime_analyses`), Auto-Check aus, Stand 30.07.
- Keine Vorwärts-Kontrolle im Betrieb („was sagte die Erkennung damals – was kam danach?“).

Leitlinien: additiv, hinter Flags, keine bestehende API/Collection verändern; reine Funktionen testbar; Render-Struktur unangetastet.

---

## Bausteine

### B0 – Aufräumen / Ehrlichkeit (klein, risikofrei)
- [x] `GET /api/dynamic/list`: Feld `orphaned` + `orphan_reason`, wenn `settings.analysis_id` auf keine vorhandene Analyse zeigt (`routers/dynamic.py`, rein: `dynamic_live.orphan_info`).
- [x] `DynamicPanel.js`: Badge „Analyse gelöscht – verwaist“ (data-testid `dyn-orphan-<id>`).
- [x] `RegimeValidation.js`: Umbenennung „Plausibilitäts-Prüfung (Selbstkonsistenz)“ + Hinweistext, dass 100 % kein Beweis für Erkennungsqualität ist.

### B1 – Struktur-Historie (Datenbasis fürs Cockpit)
- [x] Neue Collection `structural_regime_history` (ein Eintrag je Wechsel von regime_id/direction/state/stage, plus Heartbeat max. 1×/6 h): `services/regime_cockpit.record_structural()`; Hook additiv in `structural_regime.resolve()` (try/except, nie Exception nach außen).
- [x] Retention: 90 Tage (`services/retention.py` DEFAULT_POLICY).

### B2 – Cockpit-API (nur lesend)
- [x] `services/regime_cockpit.py` (rein + assemble):
  - `observer_direction(label)` trend_up*→up, trend_down*→down, range*/drift*→side, breakout*→None
  - `segments(points)` gleiche Labels zusammenfassen
  - `forward_hits(points, prices, horizon_ms, flat_pct)` – Vorwärts-Trefferquote je Label: up = Kurs danach höher, down = tiefer, side = |Bewegung| < flat_pct (Median der Horizont-Bewegungen im Fenster)
  - `agreement(observer_points, structural_points)` Anteil Zeit mit gleicher Richtung
  - `assemble(db, symbol, days)` → prices(1h), observer(points+segments+hits), structural(history+segments+hits+stage), trades(ai_trader), agreement
- [x] `routers/regime_cockpit.py`: `GET /api/regime-cockpit/{symbol}?days=14`, `GET /api/regime-cockpit/overview?days=14` (je Symbol: Hits + Agreement + Stufe, gecacht 10 min). Registriert in `routers/__init__.py`.
- [x] Tests `backend/tests/test_regime_cockpit.py` (unit, reine Funktionen).

### B3 – Cockpit-UI im KI-Trader (Reiter „Analyse“)
- [x] `frontend/src/components/RegimeCockpit.js` + `.css`: Symbol-/Zeitraum-Wahl, Chart (Kurs + Band Struktur (Lab) + Band Kurzfrist (Observer) + Trade-Marker Win/Loss), Tabelle Vorwärts-Trefferquote je Regime, Übereinstimmung der Ebenen, Stufe/Freigabe-Hinweis.
- [x] Einbau in `AITradingPanel.js` (Analyse-Sektion, vor `AIRewardPanel`). Farben aus `lib/regimeColors`.
- [x] Übersicht alle Symbole (18.09., ehem. „Optional“): `RegimeCockpitOverview.js` als eigene, einklappbare Karte unter dem Detail-Cockpit (`GET /api/regime-cockpit/overview`, Klick auf Zeile wählt das Symbol; data-testid `regime-cockpit-overview*`).

### B4 – KI-Kontext: Regime-Bilanz + Erkennungs-Note
- [x] `services/regime_context.py`: `prompt_block(db, symbols)` → „=== REGIME-BILANZ & ERKENNUNGS-QUALITÄT ===“: Winrate/PnL je Kurzfrist-Regime (30 d, `ai_rewards.by_regime`), je Struktur-Regime, Vorwärts-Trefferquote je Symbol (Cockpit-Cache), Note der freigegebenen Lab-Analyse je Anlageklasse (`regime_quality`), klare Handlungsregel („niedrige Trefferquote = Regime-Label misstrauen“).
- [x] Flag `regime_context_enabled` (Default **an**, reine Info) in `ai_engine.DEFAULT_CONFIG` + Update-Handler; Einbau in `ai_engine_context._analysis_extra_blocks` (nicht für `trade_review`).
- [x] UI-Schalter im KI-Setup neben dem Regime-Sperrfilter (`ai-regime-context-select`).
- [x] Tests: Formatierung des Blocks (unit).

### B5 – Abnahme
- [x] Unit-Tests grün (`tests/test_regime_cockpit.py`, `tests/test_guard_cockpit_api.py` unit-Teil).
- [x] Live-Check gegen Prod-Atlas (lesend, `scripts/live_check_regime_context.py` mit PROD_MONGO_URL, 18.09.): Block wird gebaut –
  Kurzfrist-Bilanz 30 d (z. B. trend_up_volatil 8 Trades · WR 12 %), Vorwärts-Trefferquote BTC 51 % (998 Punkte), ETH 48 % (⚠).
  `structural_regime_history` in Prod = 0 Einträge (erwartet: noch keine Lab-Freigabe).
- [x] Testing-Agent (Backend + Frontend-Smoke) – siehe `test_reports/`.
- [x] Fortschritt hier + in `memory/PRD.md` dokumentiert.

---

## Fortschritts-Log (bei Abbruch hier weiterlesen)
- 18.09. 15:10 – Plan erstellt. Start mit B0 + B1 + B2 (Backend zuerst, dann Tests), danach B3/B4.
- 18.09. 15:40 – Backend B0/B1/B2/B4 implementiert: `services/regime_cockpit.py`, `services/regime_context.py`,
  `routers/regime_cockpit.py` (registriert), Hooks in `structural_regime.resolve`, `retention.py`, `dynamic_live.orphan_info`
  + `routers/dynamic.py`, Flag `regime_context_enabled` in `ai_engine.py` + Block in `ai_engine_context._analysis_extra_blocks`.
  `tests/test_regime_cockpit.py`: 20 unit grün. NÄCHSTER SCHRITT: Backend-Neustart + Live-Check der API, dann UI (B3, B0-Badge, B4-Schalter).
- 18.09. 18:15 – Abnahme (B5): UI-Bausteine B0-Badge, RegimeValidation-Text, RegimeCockpit + Einbau, B4-Schalter waren bereits im
  Branch, Häkchen nachgezogen. Neu: `RegimeCockpitOverview.js` (Übersicht alle Symbole). Unit-Tests grün, Live-Check gegen
  Prod-Atlas lesend ok (Block gefüllt), Cockpit-UI mit echten Snapshots/Trades (Kopie aus Prod in lokale Dev-DB) geprüft.
  Plan damit ABGESCHLOSSEN.

## Offen / Nächstes (nach diesem Plan)
- Trader-Aktion: erste Lab-Freigabe (Shadow) – erst dann füllt sich die Struktur-Ebene im Cockpit und im Prompt.
- Optional: Vorwärts-Trefferquote als ML-Gate-Feature.

---

## Nachtrag 18.09. – Härtung der Brücke (P1/P2, additiv, hinter Flags)
- [x] **Retention-Pinning** (`services/retention.py`): Regel `regime_analyses` hat `protect`; `pinned_analysis_ids()` (rein) schützt
  freigegebene Analysen (Shadow/Aktiv) und Basen nicht archivierter dynamischer Strategien vor `keep_last`. Lokal verifiziert
  (11 Analysen, keep 8 → nur die ungeschützte älteste gelöscht). In Prod aktuell geschützt: `ra_c96b100f` (Basis `dyn_a59ca705`).
- [x] **Brücken-Gesundheit** (`services/regime_bridge_health.py`, neu): Checks `orphaned_dynamic`, `dynamic_idle` (Auto-Check aus /
  Check > 7 d), `no_release`, `observer_low_hit`; `GET /api/regime-cockpit/health`; Loop alle 6 h, Warnung max. 1×/Tag als
  Website-Glocke + Telegram (Toggle `regime_bridge` in Meldungen). UI: Banner `RegimeBridgeHealth.js` oben im Regime-Cockpit
  (`regime-bridge-health`, je Check `regime-bridge-health-<name>`). Prod-Befund lesend bestätigt: 3 Warnungen (verwaist, inaktiv seit 50 d, 0 Freigaben).
- [x] **Label-Zuverlässigkeit im Prompt** (Flag `regime_label_reliability_enabled`, Default AUS; KI-Setup „Label-Zuverlässigkeit (Prompt)“,
  `ai-regime-reliability-select`): `regime_context.annotate_label()` (rein) hängt an jedes Kurzfrist-Label im Markt-Beobachter-Block
  die 14-d-Vorwärts-Trefferquote; < 52 % → „unzuverlässig, nicht als Begründung nutzen“. Nie blockierend (Cockpit-Cache).
- Tests: `tests/test_regime_bridge_hardening.py` (10 unit). Bestehende Suiten `test_ai_lab`, `test_adaptive_modules`, `test_regime_cockpit` grün.

## Nachtrag 18.09. (Teil 2) – ML-Gate-Feature, Kopplung, Freigabe-Vorbereitung
- [x] **ML-Gate-Feature `regime_hit_pct`** (`services/ml_gate.py` GATE_FEATURES, `gate_feature_row`; 50 = neutral/unbekannt).
  Quelle: `ai_market_observer` schreibt beim Snapshot `features.regime_hit_pct` (Cockpit-Cache, 14 d, je Symbol) → landet über
  `entry_market_snapshot` in Decisions/Signals. `_to_matrix(rows, features)` + `predict_row` nutzen die Feature-Liste des geladenen
  Modells → ältere Modelle laufen unverändert weiter; das nächtliche Retraining übernimmt das Feature, sobald genug Zeilen es tragen
  (Plan: nach 4–6 Wochen Cockpit-Daten wirksam).
- [x] **Kopplung Dyn-Strategie ↔ Lab-Freigabe** (`settings.follow_release_enabled`, Checkbox „An Lab-Freigabe koppeln“ im DynamicPanel,
  `dyn-follow-release-<id>`, Endpoint `/api/dynamic/{id}/settings`): Health-Check `release_mismatch` warnt (Banner + 1×/Tag Telegram)
  mit Ziel-Analyse, sobald die Basis-Analyse nicht mehr der Freigabe der Anlageklasse entspricht. Bewusst KEIN automatischer Umbau:
  ein Neuaufbau braucht Optimierung je Regime + Walk-Forward (Lab-Job-Kette) – das bleibt Trader-Entscheidung.
- [ ] **Lab-Freigabe Shadow – Trader-Aktion (nicht aus der Dev-Umgebung möglich, Prod nur lesend):** Bester Kandidat lt. Qualitäts-Karte
  `ra_60fce9cc` („Regime-Analyse 5m · 360d“, 4 Symbole, Note **gut 67,2 %** Live=Final Holdout); Alternative `ra_8fe52dd6` (1h/90d, 1 Symbol,
  76 %). Freigabe-Gate verlangt: Regime(s) auf „behalten“ setzen (aktuell bei ALLEN 12 Analysen kept=0!), Kalibrierung + Ablation mit
  gleichen Symbolen/Timeframe gelaufen, dann Stufe Shadow.
- Tests: `tests/test_regime_bridge_hardening.py` (12 unit), ML-Gate-Suiten grün.
