# KI-Trader – Backtest-Seeding (Phase 1)

## Original-Problemstellung (Kurz)
Bestehende, produktiv laufende Daytrading-Website (Repo `dean06greif-ai/KI-Trader`, Branch
`conflict_060926_1107`, Deploy auf Render – Ordnerstruktur unverändert lassen). Verbesserung:
Datensammlung/Reife-Gates des KI-Traders beschleunigen → **Phase 1 aus `BACKTEST_SEEDING_PLAN.md`**
als Funktion im Backtester („KI Trader“ auswählen): Setups je Anlageklasse regelbasiert auf der
Vergangenheit testen, Einmal-Durchlauf oder Auto-Schleife (Anpassung → erneut testen), Overfitting
vermeiden, immer innerhalb der ganzen Anlageklasse testen. Ein erfolgreicher Backtest macht ein
Setup NICHT live-reif – echte Paper-Trades bleiben Pflicht.

## Nutzer-Entscheidungen
- Nur manuell im Backtester (kein Hintergrund-Scheduler – RAM/Worker).
- Nur Reife-Gate-Zähler seeden (klar getrennt), Walk-Forward/OOS zur Overfitting-Bremse erwünscht.
- Produktive .env (MongoDB Atlas) in der Preview.
- Bereits live-reife Setups einer Klasse werden nicht angefasst.

## Architektur (neu, modular)
- `backend/services/setup_backtest/`
  - `detectors.py` – 9 reine Detektoren (breakout, squeeze_breakout, range_fade, mean_reversion,
    htf_range, session_open, trend_follow, pullback, divergence), je 3 Parameter-Varianten,
    Handelszeit-Filter je Klasse + ATR-Boden, kein Look-Ahead (15m/1h nur geschlossene Kerzen).
  - `simulator.py` – konservativ (SL vor TP, TP1 50 % + Break-Even, Zeit-Exit, Gebühren je Klasse,
    `setup_asset_class.clamp_levels`), Notional 100 USDT.
  - `weights.py` – Backtest ×0.5, gedeckelt auf 3 gewichtete Trades; Boost nur mit ≥2 echten,
    profitablen Paper-Trades; TTL 60 Tage; Collection `setup_backtest_trades`.
  - `runner.py` – Job (JOBS), Kerzen via `candle_cache` (+ Disk-Persist), IS/OOS 70/30,
    Varianten-Schleife (`single`/`loop`), State `settings.setup_backtest_state`, Feed-Meldung,
    `ai_playbook.refresh()`.
- `backend/routers/setup_backtest.py`: `GET /api/ai/playbook/backtest`, `POST …/run`
  (`asset_classes|symbols, days, mode, setups`), `GET …/status/{job}`, `POST …/cancel/{job}`,
  `POST …/reset` (Admin).
- Playbook-Einbindung (`ai_playbook.py`): `_refresh_scope` → `bt_stats`/`bt_promoted`, Boost nur
  Sammeln→Live (nie Rückstufung/live_stats), `_class_cache.stats[sid].backtest_promoted` →
  `setup_capital.SETUP_FACTOR["backtest"]=0.4`, `maturity_overview(... backtest, bt_promoted)`,
  Prompt-Blöcke `BACKTEST-EDGE` / `BACKTEST OHNE EDGE`, `invalidate_cache()`.
- Frontend: `AITraderSeeding.js` (Chip „🤖 KI Trader · Setups“ im Backtester, Modus, Start,
  Fortschritt, Ergebnis-Tabelle, Reset), Spalte „BT“ + Badge in `SetupMaturityTable.js`.
- `routers/backtest.py`: gegenseitiger 409-Schutz normaler Backtest ↔ Seeding.

## Umgesetzt (06.09.2026)
- Phase 1 komplett, 24 Regressionstests `tests/test_setup_backtest_seeding.py`; bestehende
  Playbook-Suites grün (65 Tests). E2E-Läufe Indizes/Rohstoffe 90 Tage: Pipeline ok, Ergebnis
  ehrlich „kein Edge“ (alle Varianten IS/OOS negativ) – nichts geseedet.

## Backlog
- P1: Phase 2 (order_block/fvg_fill via smc_zones, liquidity_sweep, funding_fade mit Funding-Historie).
- P1: Krypto-Lauf (11 Symbole, 90 Tage) – Laufzeit/RAM auf Render beobachten; ggf. lokaler Worker.
- P2: Detektor-Feintuning nur mit Blick auf OOS (keine weiteren Varianten ohne Hypothese).
- P2: Seeding-Ergebnis-Historie (History je Setup) in der UI ausklappbar.
