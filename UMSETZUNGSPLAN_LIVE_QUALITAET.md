# UMSETZUNGSPLAN: Live-Qualitäts-Offensive (4 Bausteine)

> **Für die umsetzende KI:** Dieses Dokument ist die verbindliche Arbeitsanweisung.
> Grundsatz des Projekts: Die Website läuft produktiv (Render-Deploy). Alle Änderungen
> sauber, modular, rückwärtskompatibel, Original-Ordnerstruktur beibehalten.
> Vor jedem Baustein die genannten Dateien lesen, danach Regressionstests schreiben.
> Testlauf: `cd backend && python -m pytest tests/ -m unit -q` (muss 100% grün bleiben,
> aktuell 824 passed). Neue Tests als Unit-Tests ohne Netzwerk (Fakes wie in
> `backend/tests/test_entry_order_registry.py`). KEINE Keys hardcoden, alles über Env/Settings.

## Kontext: Vorhandene Infrastruktur (NICHT neu bauen, wiederverwenden!)
- `backend/services/bitunix_trade.py`
  - `AutoTradeManager.on_signal()` – zentraler Trade-Eröffnungspfad (Paper + Live).
    Live-Pfad: `entry_meta` (Strategie/SL/TP/clientId) → optional `_maker_entry()`
    (Post-Only-Limit nahe Mark, 45s Wartezeit) → sonst Market-Order. Jede Order trägt
    `client_id` (`make_client_id()`, Präfix `KIT-`) und wird in der Registry registriert.
  - `fee_guard_check(ai_cfg, cfg, entry, sl, atr, tp, funding_pct)` – Fee-Wächter
    (Roundtrip-Fees + Funding + ATR-Floor). 1m-ATR ist im Signal (`atr`) vorhanden.
  - `_manage_trade()` – lokaler Monitor (BE, ATR-Trailing, Key-Level-Trailing,
    Gewinnsicherung, Marge-Freisetzung).
  - `effective_fee_percent(cfg)` / `effective_maker_fee_percent(cfg)`.
- `backend/services/entry_order_registry.py` – Registry offener Entry-Limit-Orders
  (Collection `pending_entry_orders`, `register/resolve/mark_orphan/find_match/cleanup`,
  Auto-Cleanup nach 36h). Der Positions-Watchdog (`services/position_watchdog.py`,
  `_adopt_from_registry`) übernimmt späte Fills automatisch als korrekten Strategie-Trade
  inkl. Telegram-Signal. **Diese Mechanik unbedingt für Key-Level-Limits nutzen.**
- `backend/services/ai_engine.py` – KI-Entscheidungen (`_emit_signal`, `_setup_live_gate`
  mit Live-Gate-Bypass), Decision-Felder wie `ai_horizon`, `ai_maker_ok`.
  Marktzustands-Features (Order-Blocks `ob_bull`/`ob_bear`, POC/VAL/VAH) kommen aus
  `services/ai_market_observer.py` (`compute_features` / `features_for(symbol)`).
- API: `GET /api/autotrade/pending-entry-orders` (Order-Karte im Frontend existiert:
  `frontend/src/components/PendingEntryOrders.js`).
- KI-Konfig: `db.settings _id='ai_trader_config'`, Update-Handler in
  `AIEngine.set_config()` (`ai_engine.py`, Muster: `live_gate_bypass_*`).

**Empfohlene Umsetzungs-Reihenfolge (getrennte Commits, je Baustein einzeln testbar):**
1) Baustein A (ATR-Market-Block) → 2) Baustein B (Slippage-Messung) →
3) Baustein D (Ehrliches Paper) → 4) Baustein C (Key-Level-Limits).
A+B sind Voraussetzung, um C später bewerten zu können.

---

## Baustein A: ATR-Market-Block (Low-Vol-Schutz)
**Ziel:** Bei zu niedriger 1m-Volatilität keine Market-Entries mehr (Gebühren > erwartbare
Bewegung); stattdessen nur Maker-/Limit-Entries erlauben.

**Umsetzung (backend/services/bitunix_trade.py):**
1. Neue reine, testbare Funktion auf Modul-Ebene:
   `atr_market_block(atr_pct: float, threshold_pct: float) -> bool`
   (True = Market-Order blocken; `atr_pct` = 1m-ATR in % vom Preis: `atr / entry * 100`).
2. Konfig in `DEFAULT_COIN_CFG`/KI-Config: `low_vol_market_block_enabled` (Default True,
   NUR für `strategy_id == 'ai_trader'` anwenden!), `low_vol_atr_threshold_pct`
   (Default 0.10). In `AIEngine.set_config()` Handler ergänzen (Muster live_gate_bypass_*).
3. In `on_signal()` im Live-Pfad VOR der Order-Platzierung: wenn Block greift und
   `maker_requested` False ist → Maker-Modus für diesen Entry erzwingen
   (`maker_requested = True`); wenn der Maker-Entry in `fallback` endet → **KEIN**
   Market-Fallback, sondern Trade ablehnen via `_notify_reject(symbol, side,
   'Low-Vol-Block: 1m-ATR x.xx% < Schwelle – kein Market-Entry')` und `return None`.
   Achtung: bestehenden `orphan`-Pfad nicht verändern.
4. Event/Feld am Trade: `low_vol_forced_maker: True` wenn erzwungen (für spätere Auswertung).

**Tests (neu: `backend/tests/test_atr_market_block.py`):** Schwellen-Logik, erzwungener
Maker-Modus, Ablehnung statt Market-Fallback, Config-Handler-Grenzen (0–2%).

---

## Baustein B: Slippage-/Fill-Qualitäts-Messung
**Ziel:** Bei jedem Live-Trade messen, wie viel zwischen Signalpreis und echtem Fill
verloren geht – Datenbasis für alle weiteren Optimierungen.

**Umsetzung:**
1. `on_signal()` kennt beide Preise bereits: `signal['entry']` (Signalpreis, vor
   Order-Platzierung sichern als `signal_price`) und `entry` (nach Fill ggf. mit
   `fill['avg_price']` überschrieben). Am Trade-Dokument speichern:
   - `signal_price`, `fill_price` (= entry), `slippage_pct` = signierte Abweichung in %
     (LONG: (fill − signal)/signal × 100 → positiv = teurer gekauft; SHORT invers),
     `slippage_usdt` = slippage_pct/100 × signal_price × qty, `order_kind` (existiert).
2. Paper-Trades: `slippage_pct` aus Baustein D übernehmen (dort simuliert).
3. Auswertung-API: `GET /api/autotrade/slippage-stats` (Datei `backend/routers/autotrade.py`,
   Muster: `pending-entry-orders`-Endpoint): Aggregation über geschlossene+offene Trades
   der letzten N Tage (`?days=30`), gruppiert nach `strategy_id` und `order_kind`:
   Anzahl, Ø slippage_pct, Summe slippage_usdt, Ø Gebühren. `/api`-Präfix beachten.
4. KI-Kontext: In `ai_engine._strategy_performance_text()` einen Kurzblock ergänzen
   („Fill-Qualität letzte 14 Tage: maker Ø x.xx%, market Ø x.xx%“), damit die KI selbst
   aus den Kosten lernt.

**Tests (`backend/tests/test_slippage_stats.py`):** Vorzeichen-Logik LONG/SHORT, Aggregation
mit FakeDB, Endpoint-Antwortform.

---

## Baustein C: Key-Level-Limit-Entries (Limit-Orders an Order-Blocks/POC) – MIT GUARDRAILS
**Ziel:** Die KI darf statt „sofort rein“ eine ruhende Limit-Order an einem präzisen
Key-Level platzieren (Order-Block, POC, VAL/VAH, Range-Grenze) – mit Ablauf (TTL) und
Neu-Bewertung in jedem Analysezyklus. **Adverse-Selection-Schutz ist Pflicht.**

**Design-Regeln (nicht verhandelbar):**
- SL/TP hängen IMMER direkt an der Order (macht `place_order(tp_price, sl_price)` schon).
- TTL: Order lebt max. bis zur nächsten Analyse (`ttl_min`, Default = KI-Intervall,
  z.B. 15–20 min; hart gedeckelt 120 min). Nach Ablauf: stornieren (verifiziert! Muster:
  `_maker_entry`-Timeout-Pfad mit `cancel_ok`-Check und `mark_orphan` bei Unsicherheit).
- Neu-Bewertung: Im nächsten Analysezyklus entscheidet die KI erneut – Order bestätigen
  (Restlaufzeit verlängern), Preis anpassen (cancel + neu) oder streichen.
- Max. 1 offene Key-Level-Order pro Symbol+Richtung; zählt gegen die normalen Slots.
- Entfernungs-Guard: Level darf max. `max_level_dist_pct` (Default 1.5%) vom aktuellen
  Kurs entfernt sein, sonst ablehnen (sonst wird die These bis zum Fill uralt).

**Umsetzung:**
1. **KI-Entscheidung erweitern** (`ai_engine.py`): Decision-JSON um optionale Felder
   `entry_mode: "market"|"limit_level"`, `entry_level: float`, `entry_level_kind:
   "ob_bull"|"ob_bear"|"poc"|"val"|"vah"|"range"` erweitern (Prompt-Schema dort ergänzen,
   wo `ai_maker_ok`/`horizon` definiert werden). Plausibilisierung im Code: Level muss auf
   der richtigen Seite des Kurses liegen (LONG: Level < Kurs; SHORT: Level > Kurs) und
   innerhalb `max_level_dist_pct`, sonst Downgrade auf bisherigen Maker-/Market-Pfad.
2. **Neuer Ausführungspfad** in `on_signal()` (bitunix_trade.py): neue Methode
   `_key_level_entry(symbol, side, qty, level, ttl_min, tpf, sl, meta)` analog
   `_maker_entry`, aber: Limit-Preis = Level, `effect='GTC'` (Post-Only optional wenn
   Level passiv liegt), KEINE Wartezeit-Schleife – Order registrieren
   (`entry_order_registry.register(..., kind='key_level', meta={..., 'ttl_min': ttl,
   'expires_at': iso})`) und `on_signal` gibt einen „pending“-Status zurück (KEIN lokaler
   Trade-Insert – der Fill wird vom Watchdog über die bestehende
   `_adopt_from_registry`-Mechanik als vollwertiger KI-Trade übernommen, inkl.
   Telegram-Signal „TRADE ERÖFFNET (Limit-Fill)“ – das funktioniert bereits!).
   Wichtig: Slots/Cooldowns wie bei echten Trades zählen (offene Registry-Einträge
   kind='key_level' beim Slot-Check in `on_signal` mitzählen).
3. **TTL-Enforcement:** In `entry_order_registry.cleanup()` zusätzlich Einträge mit
   `kind='key_level'` und `expires_at < now` stornieren+löschen (Cancel verifizieren);
   Aufruf existiert bereits im Watchdog-Zyklus. Telegram-Info bei Ablauf (ntype
   `key_level_expired`, mit Symbol/Level/Grund).
4. **Neu-Bewertung je Zyklus** (`ai_engine`): Vor jeder Analyse offene `key_level`-Einträge
   des Symbols in den Prompt-Kontext geben („Du hast eine wartende Limit-Order @ X,
   läuft ab um Y – bestätigen/anpassen/streichen?“); Antwortfeld `pending_order_action:
   "keep"|"move"|"cancel"` auswerten (move = cancel + neue Order via `_key_level_entry`).
5. **Frontend:** Die Karte `PendingEntryOrders.js` zeigt die Orders bereits (Countdown
   nutzt `expires_in_s`) – nur sicherstellen, dass `expires_at` aus meta in den Endpoint
   `GET /api/autotrade/pending-entry-orders` einfließt (kürzere TTL statt 36h anzeigen).

**Tests (`backend/tests/test_key_level_entry.py`):** Level-Plausibilisierung (Seite/Distanz),
Registry-Eintrag mit kind/ttl, TTL-Cleanup storniert & meldet, Watchdog-Adoption eines
key_level-Fills als KI-Trade (Fakes aus `test_entry_order_registry.py` wiederverwenden),
Slot-Zählung inkl. wartender Orders.

---

## Baustein D: Ehrliches Paper (simulierter Spread + Slippage)
**Ziel:** Paper-Trades sollen dieselben Reibungskosten tragen wie Live, damit die
Live/Paper-Statistik vergleichbar wird und die KI nicht auf geschönten Zahlen lernt.

**Umsetzung (backend/services/bitunix_trade.py):**
1. Reine Funktion `simulate_fill_price(entry: float, side: str, atr_pct: float,
   spread_pct: float = 0.02, slip_atr_frac: float = 0.05) -> float`:
   Fill = entry ± (spread_pct/100 × entry) ± (slip_atr_frac × atr_pct/100 × entry)
   (LONG: schlechter = höher; SHORT: schlechter = tiefer). Konservativ, deterministisch
   (kein Zufall – reproduzierbare Tests und keine Streuung im Lernsignal).
2. In `on_signal()` im Paper-Zweig (`mode != 'live'`): `entry` durch simulierten Fill
   ersetzen, `signal_price`/`fill_price`/`slippage_pct` wie in Baustein B setzen,
   Event „PAPER-FILL simuliert: Spread+Slippage x.xx%“ anhängen.
   Auch der Paper-EXIT in `_manage_trade()`/Paper-Close: Exit-Preis mit derselben
   Funktion verschlechtern (Richtung invers zum Entry).
3. Konfig: `paper_realistic_fills` (Default True), `paper_spread_pct` (0.02),
   `paper_slip_atr_frac` (0.05) – in KI-/Autotrade-Settings + set_config-Handler.
   WICHTIG: `data_collection`-Trades ebenfalls simulieren (sie sind die Lernmasse).
4. Kennzeichnung: Feld `realistic_fill: True` am Trade, damit Alt-Statistiken (vor der
   Änderung) von neuen unterscheidbar bleiben; Auswertungen/ML nicht rückwirkend mischen.

**Tests (`backend/tests/test_realistic_paper_fills.py`):** Richtungs-Logik LONG/SHORT
(Entry schlechter, Exit schlechter), Default-Werte, Toggle aus → identischer Preis,
data_collection-Pfad inkludiert.

---

## Abschluss-Checkliste (nach jedem Baustein)
1. `cd backend && python -m pytest tests/ -m unit -q` → 0 Fails (Marker `unit` reicht,
   `-m live` NICHT lokal ausführen).
2. Keine Änderung an: Env-Handling, Ordnerstruktur, bestehenden API-Verträgen.
3. Neue Settings dokumentieren (README-Abschnitt oder Kommentar am DEFAULT-Dict).
4. Telegram-Nachrichten über `services/notifications.telegram_notify(db, telegram, ntype,
   text)` mit eigenem `ntype` (damit sie im Notify-Panel abschaltbar sind).
5. Frontend-Änderungen: `data-testid` auf alle neuen interaktiven/informativen Elemente.
6. Render-Kompatibilität: keine neuen System-Abhängigkeiten; neue Python-Pakete (falls
   nötig – hier voraussichtlich keine) in `backend/requirements.txt`.
