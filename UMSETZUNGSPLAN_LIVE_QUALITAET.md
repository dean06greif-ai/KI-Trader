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
- ⬜ Baustein A (ATR-Market-Block) – NÄCHSTER SCHRITT
- ⬜ Baustein B (Slippage-/Fill-Qualitäts-Messung) – DANACH

**Reihenfolge:** A → B → 2–4 Wochen Messphase → dann Entscheid über weitere Schritte
(Option 3 nur, falls die Daten zeigen, dass Limit-Entries allein nicht reichen).
Nicht alles gleichzeitig ändern – sonst ist nicht zuordenbar, was gewirkt hat.

---

## Baustein A: ATR-Market-Block (Low-Vol-Schutz) – OFFEN
**Ziel:** Bei zu niedriger 1m-Volatilität keine Market-Entries (Gebühren > erwartbare
Bewegung); nur Maker-/Limit-Entries erlauben.

**Umsetzung (backend/services/bitunix_trade.py):**
1. Reine, testbare Modul-Funktion: `atr_market_block(atr_pct, threshold_pct) -> bool`
   (True = Market blocken; atr_pct = 1m-ATR in % vom Preis: atr/entry*100).
2. Konfig: `low_vol_market_block_enabled` (Default True, NUR `strategy_id == 'ai_trader'`),
   `low_vol_atr_threshold_pct` (**Default 0.10 – vom User bestätigt**), Handler in
   `AIEngine.set_config()` (Muster live_gate_bypass_*), Grenzen 0–2%.
3. In `on_signal()` Live-Pfad VOR Order-Platzierung: Block greift + `maker_requested`
   False → Maker erzwingen; endet Maker im `fallback` → KEIN Market-Fallback, sondern
   `_notify_reject(symbol, side, 'Low-Vol-Block: 1m-ATR x.xx% < Schwelle …')` + return None.
   `orphan`-Pfad NICHT verändern. Feld `low_vol_forced_maker: True` am Trade.
4. Synergie: Wenn die KI ohnehin `entry_type='limit'` wählt, greift der Block nicht
   (Limit ist ja das gewünschte Verhalten).

**Tests (`backend/tests/test_atr_market_block.py`):** Schwellen-Logik, erzwungener Maker,
Ablehnung statt Market-Fallback, Config-Grenzen.

---

## Baustein B: Slippage-/Fill-Qualitäts-Messung – OFFEN
**Ziel:** Bei jedem Trade messen, was zwischen Signalpreis und Fill verloren geht –
Datenbasis für alle weiteren Optimierungen (insb. Bewertung der Key-Level-Limits).

**Umsetzung:**
1. `on_signal()`: `signal_price` (vor Platzierung), `fill_price` (= entry nach Fill),
   `slippage_pct` signiert (LONG: (fill−signal)/signal×100, positiv = teurer; SHORT invers),
   `slippage_usdt`. Live UND Paper einheitlich (Paper: aus `paper_exec` übernehmen).
2. `GET /api/autotrade/slippage-stats?days=30` (routers/autotrade.py): Aggregation über
   Trades der letzten N Tage, gruppiert nach `strategy_id` × `order_kind`
   (market/maker/taker_fallback/limit-fill): Anzahl, Ø slippage_pct, Σ slippage_usdt,
   Ø Gebühren – **PLUS Ø mfe_pct / Ø mae_pct pro Gruppe** (Adverse-Selection-Check:
   laufen Limit-Fills nach dem Fill im Schnitt stärker gegen uns als Market-Entries?).
3. KI-Kontext: Kurzblock in `ai_engine._strategy_performance_text()`
   („Fill-Qualität 14d: maker Ø x.xx%, market Ø x.xx%, limit-fill MAE Ø y.yy%“).
4. Optional UI: kleine Karte im Analyse-Panel (nach den Trades), erst wenn Daten da sind.

**Tests (`backend/tests/test_slippage_stats.py`):** Vorzeichen LONG/SHORT, Aggregation
mit FakeDB, Endpoint-Form.

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
