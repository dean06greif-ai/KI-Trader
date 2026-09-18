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
- [ ] `DynamicPanel.js`: Badge „Analyse gelöscht – verwaist“ (data-testid `dyn-orphan-<id>`).
- [ ] `RegimeValidation.js`: Umbenennung „Plausibilitäts-Prüfung (Selbstkonsistenz)“ + Hinweistext, dass 100 % kein Beweis für Erkennungsqualität ist.

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
- [ ] `frontend/src/components/RegimeCockpit.js` + `.css`: Symbol-/Zeitraum-Wahl, Chart (Kurs + Band Struktur (Lab) + Band Kurzfrist (Observer) + Trade-Marker Win/Loss), Tabelle Vorwärts-Trefferquote je Regime, Übereinstimmung der Ebenen, Stufe/Freigabe-Hinweis.
- [ ] Einbau in `AITradingPanel.js` (Analyse-Sektion, vor `AIRewardPanel`). Farben aus `lib/regimeColors`.

### B4 – KI-Kontext: Regime-Bilanz + Erkennungs-Note
- [x] `services/regime_context.py`: `prompt_block(db, symbols)` → „=== REGIME-BILANZ & ERKENNUNGS-QUALITÄT ===“: Winrate/PnL je Kurzfrist-Regime (30 d, `ai_rewards.by_regime`), je Struktur-Regime, Vorwärts-Trefferquote je Symbol (Cockpit-Cache), Note der freigegebenen Lab-Analyse je Anlageklasse (`regime_quality`), klare Handlungsregel („niedrige Trefferquote = Regime-Label misstrauen“).
- [x] Flag `regime_context_enabled` (Default **an**, reine Info) in `ai_engine.DEFAULT_CONFIG` + Update-Handler; Einbau in `ai_engine_context._analysis_extra_blocks` (nicht für `trade_review`).
- [ ] UI-Schalter im KI-Setup neben dem Regime-Sperrfilter (`ai-regime-context-select`).
- [x] Tests: Formatierung des Blocks (unit).

### B5 – Abnahme
- [ ] Unit-Tests grün, Live-Check gegen Prod-Atlas (lesend), Testing-Agent (Backend + Frontend-Smoke).
- [ ] Fortschritt hier + in `memory/PRD.md` dokumentieren.

---

## Fortschritts-Log (bei Abbruch hier weiterlesen)
- 18.09. 15:10 – Plan erstellt. Start mit B0 + B1 + B2 (Backend zuerst, dann Tests), danach B3/B4.
- 18.09. 15:40 – Backend B0/B1/B2/B4 implementiert: `services/regime_cockpit.py`, `services/regime_context.py`,
  `routers/regime_cockpit.py` (registriert), Hooks in `structural_regime.resolve`, `retention.py`, `dynamic_live.orphan_info`
  + `routers/dynamic.py`, Flag `regime_context_enabled` in `ai_engine.py` + Block in `ai_engine_context._analysis_extra_blocks`.
  `tests/test_regime_cockpit.py`: 20 unit grün. NÄCHSTER SCHRITT: Backend-Neustart + Live-Check der API, dann UI (B3, B0-Badge, B4-Schalter).

## Offen / Nächstes (nach diesem Plan)
- Trader-Aktion: erste Lab-Freigabe (Shadow) – erst dann füllt sich die Struktur-Ebene im Cockpit und im Prompt.
- Optional: Cockpit-Übersicht (alle Symbole) als eigene Karte; Vorwärts-Trefferquote als ML-Gate-Feature.
