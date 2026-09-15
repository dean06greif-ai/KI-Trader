# UMSETZUNGSPLAN: Live-Qualitäts-Offensive (Stand 23.06.2026 – aktualisiert)

> **Für die umsetzende KI:** Dieses Dokument ist die verbindliche Arbeitsanweisung.
> Grundsatz: Die Website läuft produktiv (Render-Deploy). Alle Änderungen sauber,
> modular, rückwärtskompatibel, Original-Ordnerstruktur beibehalten.
> Testlauf: `cd backend && python -m pytest tests/ -m unit -q` (muss 100% grün bleiben).
> Neue Tests als Unit-Tests ohne Netzwerk. KEINE Keys hardcoden, alles über Env/Settings.

## Analyse: Live-vs.-Paper-Kluft (14,3% live vs. 55,1% paper)
1. **Statistik-Vorbehalt:** Die Live-Winrate basiert auf sehr wenigen Trades – Warnsignal,
   kein Beweis. Die 55,1% Paper stammen großteils aus der Zeit VOR den realistischen
   Paper-Fills (Baustein D) – die Kluft ist teils Mess-Artefakt und wird ab jetzt ehrlicher.
2. **Strukturell real:** Taker-Fees + Slippage bei 15-min-Analyse-Rhythmus fressen enge
   Setups auf. Die Richtung der KI-Analyse stimmt.
3. **Werkzeug-Check (WICHTIG – die KI kennt ihren eigenen Werkzeugkasten nicht komplett):**
   - Option 1 (Limit an Key-Levels) = **BEREITS GEBAUT** (`services/key_level_limits.py`):
     entry_type market|limit, TTL `limit_valid_min` 15–480 min, Neu-Bewertung je Zyklus
     (`cancel_limit`/Ersetzen), Distanz-Guards (Scalp 2,5% / Swing 8%), Prompt-Kontext
     „WARTENDE LIMIT-ORDERS“, Frontend-Karten. Dazu Post-Only `_maker_entry` (45s).
     → Offene Frage ist nicht „bauen?“, sondern „bringt es messbar was?“ → Baustein B.
   - Option 2 (ATR-Filter) = **HALB GEBAUT**: `fee_guard_check` blockt SL-Distanzen unter
     Fees×Mult bzw. ATR-Rauschband (+Funding). Es FEHLT der explizite Low-Vol-Market-Block
     → Baustein A.
   - Option 3 (1m-Hybrid-Trigger) = nicht gebaut. Bewusst ZURÜCKGESTELLT: höchste
     Komplexität, Overfitting-Risiko, konkurriert mit Key-Level-Limits. Erst entscheiden,
     wenn A+B 2–4 Wochen Daten geliefert haben.

## Status der Bausteine
- ✅ Baustein C (Key-Level-Limit-Entries): fertig (`key_level_limits.py`, Tests grün)
- ✅ Baustein D (Ehrliches Paper): fertig (`paper_execution.py`: Orderbuch-Spread +
  Sqrt-Impact-Slippage, gedeckelt, `paper_exec` am Trade)
- ✅ Baustein E (NEU, 23.06.): **MFE/MAE-Tracking** – `peak_price` (bester Stand) und
  `trough_price` (schlechtester Gegenlauf) werden pro Trade im Tick-Monitor getrackt
  (`update_peak`/`update_trough` in bitunix_trade.py, Anzeige via `_enrich_trade`:
  `mfe_pct`/`mae_pct`, UI: Preis-Leiter + Meta + Trade-Chart-Linien). Datenbasis für
  Entry-Timing-/Adverse-Selection-Auswertung.
- ✅ Baustein A (ATR-Market-Block) – **FERTIG 23.06.2026** (Details unten)
- ✅ Baustein B (Slippage-/Fill-Qualitäts-Messung) – **FERTIG 23.06.2026** (Details unten)

**Nächster Schritt: 2–4 Wochen MESSPHASE** (nichts weiter ändern!), danach Auswertung
über `GET /api/autotrade/slippage-stats` und erst dann Entscheid über Option 3
(1m-Hybrid) oder Feintuning der Schwellen.

---

## Baustein A: ATR-Market-Block (Low-Vol-Schutz) – ✅ FERTIG (23.06.2026)
**Umgesetzt:**
1. `atr_market_block(atr_pct, threshold_pct)` – reine Funktion in
   `backend/services/bitunix_trade.py` (fail-open bei ungültigen Werten, Schwelle 0 = aus).
2. Konfig in `DEFAULT_AI_CONFIG` (ai_engine.py): `low_vol_market_block_enabled`
   (Default True) + `low_vol_atr_threshold_pct` (**Default 0.10**, Grenzen 0–2%),
   Handler in `AIEngine.set_config()` (Muster live_gate_bypass_*).
3. In `on_signal()` (bitunix_trade.py, nach maker_requested): greift nur bei
   `mode == "live"`, `strategy_id == "ai_trader"`, nicht bei manual/ai_limit_fill.
   ATR unter Schwelle → Maker-Entry erzwungen (`low_vol_forced_maker: True` am Trade);
   endet der Maker im Fallback → KEIN Market-Fallback, sondern `_notify_reject`
   ("Low-Vol-Block …") + return None. `orphan`-Pfad unverändert.
4. Tests: `backend/tests/test_atr_market_block.py` (4 Tests, grün).

---

## Baustein B: Slippage-/Fill-Qualitäts-Messung – ✅ FERTIG (23.06.2026)
**Umgesetzt:**
1. `on_signal()`: `signal_price` wird VOR Maker-/Paper-Fill-Anpassungen fixiert;
   bei Live-Market-Fills wird der reale avg-Fill-Preis nur zur Messung geholt
   (`parse_order_fill(get_order_detail)`, Entry/SL/TP unverändert). Am Trade:
   `signal_price`, `slippage_pct` (signiert via `compute_slippage_pct`, + = teurer),
   `slippage_usdt`. Gilt live UND paper einheitlich.
2. `GET /api/autotrade/slippage-stats?days=30` (routers/autotrade.py) →
   `core/utils.slippage_aggregate()`: Gruppen strategy_id × mode × order_kind
   (market/maker/taker_fallback/limit_fill): Anzahl, Ø slippage_pct,
   Σ slippage_usdt, Ø mfe_pct / Ø mae_pct (Adverse-Selection-Check).
3. KI-Kontext: Fill-Qualitäts-Block in `ai_engine._strategy_performance_text()`
   (die KI sieht ihre eigenen Ausführungskosten je Order-Art).
4. Tests: `backend/tests/test_slippage_stats.py` (7 Tests, grün).
**Hinweis:** Daten laufen erst ab jetzt auf – Auswertung nach der Messphase.
**Update 24.06.2026:** UI-Karte "FILL-QUALITÄT (SLIPPAGE)" ist jetzt gebaut
(`frontend/src/components/SlippageStatsCard.js`, Analyse-Panel → Trades-View,
Zeitraum 7/30/90 Tage, Empty-State solange `measured_trades=0`) – die
Auswertung ist nach der Messphase direkt in der UI sichtbar.

---

## Backlog-Fixes P1+P2 – ✅ FERTIG (24.06.2026, Trading-Logik unangetastet)
1. **Forex-Chart (P1):** `core/instruments.kline_available()`; Trades liefern
   `computed.chart_available`; Chart-Endpoint meldet für Forex
   `chart_available=false` + `reason`; Frontend blendet den Trade-Chart-Button aus.
2. **Not-Fallback-Kennzeichnung (P1):** `role_manager.team_chain()` (Team ohne
   Not-Kette); `health_status()`-active_fallbacks tragen `outside_team`;
   AITradingPanel zeigt gelben Badge "NOT-FALLBACK außerhalb des Teams".
3. **Modularisierung (P2):** `TradeDetailCard.js` + `TradeFilters.js` aus
   `PerformanceAnalytics.js` extrahiert (1029 → ~705 Zeilen, data-testids identisch).
4. **record_result (P2):** globaler `_current_role`-Fallback entfernt – Rolle
   kommt immer explizit von generate_chain/stream_chain.
- Tests: `tests/test_backlog_p1_p2_fixes.py` (5) + `tests/test_iteration13_api.py`
  (13, Testing-Agent); Unit-Suite 865 passed.

---

## Messphase & Erfolgskriterien (nach A+B)
- 2–4 Wochen laufen lassen, dann auswerten:
  1. Ø Slippage market vs. maker vs. limit-fill (Ziel: limit/maker deutlich < market)
  2. MAE nach Fill je order_kind (Adverse Selection der Limit-Orders sichtbar?)
  3. Live-Winrate/PnL-Trend vs. (ehrliches) Paper derselben Periode
  4. Wie oft blockt der Low-Vol-Block, und was hätten die geblockten Trades gemacht?
- Erst DANACH über Option 3 (1m-Hybrid-Trigger) entscheiden.

## Abschluss-Checkliste (nach jedem Baustein)
1. `cd backend && python -m pytest tests/ -m unit -q` → 0 Fails (`-m live` NICHT lokal).
2. Keine Änderung an Env-Handling, Ordnerstruktur, bestehenden API-Verträgen.
3. Neue Settings dokumentieren (Kommentar am DEFAULT-Dict).
4. Telegram über `services/notifications.telegram_notify(db, telegram, ntype, text)`
   mit eigenem `ntype` (im Notify-Panel abschaltbar).
5. Frontend: `data-testid` auf alle neuen Elemente.
6. Render-Kompatibilität: keine neuen System-Abhängigkeiten.
