# Plan: Backtest-Seeding je Anlageklasse (Punkt 3 – Phase 1 UMGESETZT 06.09.2026)

## Status Phase 1 (umgesetzt)
- Modul `backend/services/setup_backtest/` (detectors, simulator, weights, runner), Router
  `backend/routers/setup_backtest.py` (`/api/ai/playbook/backtest[...]`), UI im Backtester
  (Chip „KI Trader · Setups“, `frontend/src/components/AITraderSeeding.js`), Spalte „BT“ in
  der Setup-Reife-Tabelle. Tests: `tests/test_setup_backtest_seeding.py`.
- Abweichungen vom ursprünglichen Plan (bewusst, für Stabilität/Anti-Overfitting):
  * Speicherung in **eigener Collection `setup_backtest_trades`** statt `auto_trades`
    (Rewards, Learning, Analytics, Equity, ML-Gate lesen `auto_trades` – bleiben unberührt).
  * **In-Sample/Out-of-Sample-Split** (70/30): In-Sample wählt die Parameter-Variante
    (3 je Setup), Out-of-Sample bestätigt; nur OOS-Trades werden gespeichert, nur bei Edge in
    beiden Fenstern (≥10 IS-/≥10 OOS-Trades, PnL > 0, promotion_ok OOS).
  * Gewichtung ×0.5, zusätzlich **gedeckelt auf 3 gewichtete Trades**: mindestens 2 echte,
    in Summe profitable Paper-Trades bleiben Pflicht (`weights.merge_stats`). Der Backtest ist
    ausschließlich eine Beschleunigung des ersten Schritts Sammeln → Live.
  * Handelszeit-Filter je Klasse + ATR-Boden (dünne Übernacht-Phasen der Index-/Rohstoff-Perps).
  * Kein Housekeeping-Task (nur manuell, Einmal-Durchlauf oder Auto-Schleife) – RAM/Worker.
  * Kapital: `SETUP_FACTOR["backtest"] = 0.4` für per Backtest freigeschaltete Setups.
- Offen (Phase 2): SMC-Setups (order_block/fvg_fill), liquidity_sweep, funding_fade.

---
Ursprünglicher Plan (Stand 06.06.2026):

Stand 06.06.2026. Ziel: Die Reife-Gates der Setups (5 gute Trades je Setup × Anlageklasse,
6 Trades je Setup × Asset für die Kapital-Eskalation) bekommen schneller belastbare Daten,
ohne echtes Kapital und ohne LLM-Tokens.

## Problem
- 64 geschlossene KI-Trades verteilt auf 16 Setups × 4 Klassen = 64 Zellen → die meisten
  Zellen haben 0–2 Trades. Indizes (2 Symbole) und Rohstoffe (3 Symbole) brauchen Monate,
  bis dort überhaupt ein Setup live-reif wird.
- Die Setup-Regeln stehen bisher nur als Prompt-Text (`ai_playbook.SETUPS`), nicht als
  ausführbarer Code → ein Backtest braucht pro Setup einen regelbasierten Detektor.

## Architektur (modular, kein Eingriff in bestehende Workflows)
```
backend/services/setup_backtest/
├── __init__.py
├── detectors.py      # je Setup eine reine Funktion detect_<setup>(candles_5m, candles_15m, ctx) -> Optional[Signal]
├── simulator.py      # Signal + Folgekerzen -> Ergebnis (SL/TP1/TPf-Fill, MFE/MAE, Dauer), rein
├── runner.py         # async: Kerzen laden (core/market_data / candle_feed), Detektoren laufen lassen,
│                     #   Ergebnisse als auto_trades mit mode="backtest", data_collection=True, backtest=True
└── weights.py        # Gewichtung Backtest- vs. echte Paper-Trades (rein)
```
- **Detektoren** nur für Setups mit klar formalisierbaren Regeln (Phase 1):
  `breakout`, `squeeze_breakout`, `range_fade`, `mean_reversion`, `htf_range`, `session_open`,
  `trend_follow`, `pullback`, `divergence`. Nicht formalisierbar (Phase 1 auslassen):
  `momentum_news`, `hedge`, `funding_fade` (braucht Funding-Historie), `order_block`/`fvg_fill`
  (SMC-Zonen aus `smc_zones.py` wiederverwenden → Phase 2), `liquidity_sweep` (Phase 2).
- **Simulator**: konservativ – Fills nur, wenn die Kerze das Level durchhandelt; bei SL und TP
  in derselben Kerze zählt der SL; Gebühren/Spread je Klasse (Krypto 0.06 % rt, Forex 1 Pip,
  Indizes/Rohstoffe 0.03 %). Verwendet die **Klassen-Grenzen** (`setup_asset_class.clamp_levels`)
  und die aktiven **Parameter-Profile** je Klasse (`setup_lifecycle`) für SL/TP.
- **Speicherung**: `auto_trades` mit `mode="backtest"`, `backtest=True`, `strategy_id="ai_trader"`,
  `setup`, `symbol`, `timeframe`, `opened_at/closed_at` (historische Zeit), `realized_pnl` auf
  Basis einer fixen Margin (z. B. 100 USDT), `margin_used`, `entry/initial_sl/exit_price/peak/trough`
  → damit funktionieren `setup_stats`, `setup_symbol_stats`, `setup_diagnosis` und die
  Profile-Evolution unverändert.
- **Gewichtung** (`weights.py`): Backtest-Trades zählen für das Reife-Gate mit Faktor 0.5
  (2 Backtest-Trades = 1 Paper-Trade), niemals für `live_stats`, niemals für die Rückstufung
  eines live laufenden Setups. Umsetzung: `ai_playbook.setup_stats(..., include_backtest=True)`
  → `$addFields weight` und gewichtete Summen; `lifecycle.promotion_ok` bekommt `weighted_trades`.
  Standard bleibt `include_backtest=False` → **Rückwärtskompatibel**, UI zeigt Backtest-Anteil separat.

## Ablauf
1. `runner.seed(db, asset_class, days=90, symbols=None)` – idempotent (Marker
   `settings.setup_backtest_state.<cls>.<setup>.<symbol>.last_ts`), läuft im Hintergrund
   (Housekeeping-Task alle 6 h, oder manuell `POST /api/ai/playbook/backtest/{asset_class}` Admin).
2. Pro Symbol: 5m + 15m + 1h Kerzen der letzten 90 Tage laden (vorhandene Feeds; Forex/Indizes
   nur Handelszeiten), Detektoren im Walk-Forward laufen lassen (kein Look-Ahead: Signal nur
   aus Kerzen ≤ t, Simulation ab t+1).
3. Ergebnisse speichern; Feed-Meldung „Backtest-Seeding Krypto: 212 Trades für 9 Setups“.
4. `ai_playbook.refresh()` berücksichtigt die gewichteten Trades → Setups werden „test“ →
   „neutral/bewährt“ (Backtest) und dürfen als **klein dimensionierte Live-Antests**
   (setup_capital: neuer Faktor `SETUP_FACTOR["backtest"] = 0.4`) freigeschaltet werden.
   Echte Paper-/Live-Ergebnisse überschreiben die Backtest-Sicht mit der Zeit automatisch
   (Lookback 30 Tage für echte Trades, Backtest-Trades verfallen nach 60 Tagen).

## Sicherheitsregeln
- Backtest-Trades **nie** als Live-Nachweis: `live_stats`, Rückstufung, Divergenz-Gate
  und Kapital-Eskalation je Asset arbeiten weiterhin nur mit echten Trades.
- Overfitting-Bremse: Profile (SL/TP) dürfen sich nicht aus Backtest-Trades allein weiterentwickeln
  (`refresh_profiles` filtert `backtest != True`).
- Kill-Switch: `ai_config.backtest_seeding_enabled` (Default False bis Phase 1 getestet ist).

## Tests (vor Produktivnahme)
- Reine Detektor-Tests mit synthetischen Kerzen (Breakout aus Range, Squeeze, Range-Fade …).
- Simulator: SL vor TP in derselben Kerze; Gebühren; Klassen-Grenzen; MFE/MAE.
- Gewichtung: 10 Backtest-Trades = 5 gewichtete; Promotion nur mit ≥ 5 gewichteten.
- Regression: `tests/test_asset_class_setups.py`, `test_risk_sizing_and_playbook_v2.py`,
  `backend/tests/test_playbook_demotion.py` müssen unverändert grün bleiben
  (Default `include_backtest=False`).

## Aufwand (Schätzung)
- Phase 1 (9 Detektoren, Simulator, Runner, Gewichtung, UI-Spalte „BT“): ~1 Session.
- Phase 2 (SMC-Setups, Funding-Historie, Sweep): ~½ Session.
