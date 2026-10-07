# Fix-Paket 06/2026: Watchdog-Duplikate · echter PnL · Copilot-Einheiten · Setup-Rückstufung

Alle Änderungen sind in die bestehende Architektur eingepflegt (keine Schnelllösungen),
rückwärtskompatibel (API-Felder bleiben, neue kommen dazu) und mit Regressionstests
abgesichert. Ordner-/Dateistruktur unverändert → Render-Deploy wie bisher.

## 1) Watchdog legt KI-Limit-Trades nicht mehr doppelt als „Manuell (Bitunix)“ an

**Ursache (Analyse):** `AutoTradeManager.on_signal` platziert die Order, wartet im
Maker-Modus bis zu 45 s auf den Fill, setzt TP1/SL und schreibt den Trade erst DANACH
in die DB. Läuft in diesem Fenster der Positions-Watchdog (alle 120 s), findet er eine
Börsen-Position ohne lokalen Trade und übernimmt sie. Zusätzlich wählte
`resolve_position_id` bei mehreren Positionen auf Symbol+Seite (Hedge-Modus, z.B. zwei
XAUUSDT-Shorts mit verschiedenem Hebel) die ERSTE statt der neuen Position.

**Lösung (KI-Trade bleibt führend):**
- `services/entry_inflight.py` (neu): `on_signal` meldet Symbol+Seite als „in Arbeit“
  an (referenzgezählt, Prozess-lokal). Der Watchdog übernimmt solche Positionen nicht
  (`adopt_deferred` im Status).
- Karenz `adopt_grace_sec` (Default 90 s, Watchdog-Config-API): unbekannte Positionen
  werden erst übernommen, wenn sie laut Börsen-`ctime` alt genug sind (Fallback: erste
  Sichtung). Registrierte KI-Entry-Orders (`entry_order_registry`) weiterhin sofort.
- Registry-Abgleich hat jetzt Vorrang vor der „Rest nach Bot-Close“-Bereinigung (ein
  KI-Wiedereinstieg kurz nach einem Close wird nicht mehr an der Börse geschlossen).
- Dedupe: Hängen KI-Trade und Watchdog-Übernahme an derselben Position-ID, wird die
  Übernahme entfernt (`deduped` im Status, Event am KI-Trade). Greift auch für bereits
  vorhandene Alt-Duplikate. Nach dem Insert räumt `on_signal` selbst auf.
- `pick_new_position` / `_resolve_new_position_id`: neue Position = nicht bereits an
  andere Trades gebunden, beste Mengen-Übereinstimmung, dann jüngste.
- Symbol+Seite-Fallback im Watchdog bevorzugt die Mengen-Übereinstimmung.

Live verifiziert mit echter Mini-Order (XRPUSDT, Börsen-Minimum):
`python scripts/live_e2e_watchdog_dedupe.py` – nur gegen DEV-DB!

## 2) Echter Bitunix-PnL nach dem Schließen (inkl. Fees/Funding)

**Ursache:** Der Monitor rechnet Trigger-Exits (SL/TP) zum Level-Preis ab; die Börse
füllt zum Marktpreis (Slippage/Gap) → SL auf Break-Even zeigte 0 statt −3 USDT.

**Lösung:** `services/pnl_reconcile.py` (neu)
- `AutoTradeManager._after_close` gleicht JEDEN geschlossenen Live-Trade mit
  `get_history_positions(positionId)` ab, BEVOR Telegram, Rewards und Kill-Switch den
  Wert sehen (kurzer Retry, Historie läuft nach).
- Hintergrund-Loop (alle 5 min, `server.py`) für Trades, deren Historie beim Close noch
  fehlte (48 h, max. 6 Versuche). Felder: `pnl_exchange_exact`, `pnl_local_estimate`,
  `pnl_reconcile_diff`, `funding_paid`, Event „PNL-ABGLEICH (Bitunix)“.
- Schutz: nur Live-Trades mit Position-ID; manuell aufgestockte Positionen (Menge >5 %
  Abweichung) werden nicht überschrieben (bestehende `_exchange_close_truth`-Logik).
- API: `GET /api/autotrade/pnl-reconcile/status`, `POST /api/autotrade/pnl-reconcile/run`
  (Admin) – auch zum Nachziehen alter Trades der letzten 48 h.

## 3) Strategie-Copilot liest Metriken mit Einheiten

**Ursache:** Ergebnis wurde als rohes (ggf. gekürztes) JSON übergeben; `max_drawdown:
1600` ohne Einheit → als 1600 % interpretiert. Der Backtester schickte gar keine Metriken.

**Lösung:** `strategy_copilot.py`
- `describe_metrics` / `metrics_summary`: eindeutig beschriftete Zeilen
  („Max. Drawdown: 1600.00 USDT (= 16.0 % des Startkapitals 10000 USDT)“) für
  `metrics`, `per_strategy`, `per_pair`; Startkapital aus result/config/settings.
- `sanity_check(metrics, capital)`: Drawdown-Einordnung + Warnung bei inkonsistenter %.
- System-Prompt: explizite EINHEITEN-Regel. Roh-JSON bleibt als Detail (mit Kürzungs-Hinweis).
- `Backtester.js` übergibt jetzt `per_strategy`-Metriken in den Copilot-Kontext.

## 4) Setups werden nicht mehr gesperrt, sondern zurückgestuft

**Ursache:** Urteil „schwach“ (≥8 Trades/30 d, WR ≤35 % oder PnL<0 & WR<45 %) führte zu
14 Tagen harter Sperre (`disabled`), auch für Paper – selbst wenn sich das Setup (z.B.
momentum_news, breakout) zwischenzeitlich erholt hatte.

**Lösung:** `ai_playbook.py` / `setup_lifecycle.py`
- Kein `disabled` mehr (bleibt leer für API/UI-Kompatibilität, Alt-Einträge werden
  automatisch in `live_blocked` migriert). „schwach“ ⇒ Rückstufung: kein Live, Paper-
  Datensammlung läuft weiter (Live-Gate leitet um).
- Wieder live nach ≥5 guten Paper-Trades SEIT der Rückstufung (oder Re-Test-Datum + gute
  Gesamtstatistik). Danach zählt für Reife und erneute Rückstufung nur die Statistik seit
  der Rückstufung (`eval_since`) → kein Ping-Pong durch alte 30-Tage-Verluste.
- `live_ready_for()` (Reife-Cache) wird vom Live-Gate (`ai_engine`) und der Diagnose genutzt.
- KI-eigene Setups: Ausmusterung nur noch, wenn sie nach dem Re-Test weiterhin schwach sind.
- UI: Phase „rückgestuft · Paper x/5“ (bestehend), Diagnose zeigt „↩ rückgestuft“.

## Tests
- Neu: `tests/test_watchdog_inflight_dedupe.py`, `tests/test_pnl_reconcile.py`,
  `tests/test_playbook_demotion.py`, `tests/test_copilot_metric_units.py` (59 Tests).
- Angepasst (Karenz-Default): `test_position_watchdog.py`, `test_watchdog_sync_only.py`,
  `test_multi_position_and_lesson_quality.py` (setzen `adopt_grace_sec=0`).
- Gesamte Unit-Suite: 915 bestanden; 3 vorbestehende Fehler unabhängig von diesem Paket
  (`test_fix_custom_ai_trades`, `test_iter49_empty_response_retry`, `test_strategy_insights`).

## 5) Nacht-Serie / Job-Warteschlange (neu)

Backtests, Optimierungen und Regime-Lab-Analysen lassen sich als Serie einplanen, die
nacheinander automatisch durchläuft (sofort oder ab planbarer Startzeit, pausierbar).

- `services/job_series.py` (neu): Einträge speichern nur `kind` + den Request-Body des
  direkten Starts; der Worker startet über die bestehenden Router-Funktionen
  (`/api/backtest/run`, `/api/optimizer/run`, `/api/regime-lab/analyze` → gleiche
  Validierung, Cloud/Lokal, RAM-Warteschlange), wartet auf das Job-Ende und speichert
  `job_id` + kompakte Zusammenfassung (USDT/%). Volle Ergebnisse liegen wie bisher in
  `backtests` / `optimizer_runs` / Regime-Analysen. Früh-Validierung beim Einreihen.
- `routers/job_series.py` (neu): `GET /api/series`, `POST /api/series/add`,
  `POST /api/series/state` (paused, start_at, notify_each, notify_done),
  `POST /api/series/reorder`, `DELETE /api/series/{id}`, `DELETE /api/series/finished`,
  `GET /api/series/{id}/result`.
- Telegram-Toggle `job_series`: Meldung je fertigem Job + Zusammenfassung, wenn die Serie
  komplett ist.
- UI: Analyse-Tools → „Nacht-Serie“ (`JobSeriesPanel.js`, `SeriesResultDetail.js`);
  „+ Serie“-Buttons in Backtester, Optimizer und Regime-Lab. Ergebnis-Detail mit
  Metriken (Einheiten) und „Als Strategie übernehmen“ / „Parameter übernehmen“ /
  „Strategie-Setup übernehmen“ über die bestehenden Apply-Endpoints.

## 6) Paper-Statistik ohne Datensammel-Trades · Strategie-Vergleich ohne Karteileichen

- Datensammel-Trades des KI-Traders (`data_collection=true`) sind technisch Paper-Trades,
  aber keine Paper-Performance: `strategy-comparison` (Default `include_collection=false`),
  `/api/autotrade/balance` Paper-Overlay, `/api/performance` und der Live/Paper-Filter der
  Analyse (neuer Filter „Sammlung“ zeigt sie separat) blenden sie aus.
- Karteileichen (Trades gelöschter/nicht mehr existierender Strategien) sind im Vergleich
  standardmäßig ausgeblendet (`include_stale=true` zeigt sie mit Badge „gelöscht“;
  `stale_strategies` listet die IDs).
- Tests: `tests/test_job_series.py`, `tests/test_paper_stats_collection_stale.py`.
