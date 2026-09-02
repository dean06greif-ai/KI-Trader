# PRD – KI-Trader (externe Daytrading-Website, Render-Deployment)

## Original-Problemstatement (06/2026)
Bestehende produktive Website (GitHub: dean06greif-ai/KI-Trader, Branch conflict_280826_2023).
Verbessern ohne Struktur/Workflows zu ändern (Render-Deployment muss funktionieren):
1. Optimizer-Fortschrittsbalken flackert (Suchen/Optimieren/Endlos-Suche)
2. RAM-Crashes auf Render (512 MB Standard-Plan) trotz Local Worker
3. Mobile: Tools-Auswahl (Backtester/Optimizer/Regime-Lab) links abgeschnitten
4. Tiefenanalyst: zu hoher Token-Verbrauch (deepseek pro) bei vielen News
5. Erklärung Dynamische Strategie vs. Regime-Lab (nur Chat, kein Umbau)

## Architektur
- FastAPI Backend (Port 8001, /api-Prefix), React Frontend, MongoDB Atlas
- Local Worker (local_worker/worker.py) pollt Server, rechnet Backtests/Optimierungen lokal
- Deployment: Render (Backend 512 MB) – Originalstruktur unverändert beibehalten

## Umgesetzt (29.06.2026)
1. **Flacker-Fix**: robustes Polling (Optimizer.js + Backtester.js: kein Overlap,
   transiente 404/502 verstecken Balken nicht mehr, Fortschritt nie rückwärts);
   Backend: Status/Active-Fallback aus DB + Job-Restore.
2. **RAM/Neustart-Resilienz**: db.local_jobs spiegelt lokale Jobs (überleben
   Server-Neustarts, Worker-Ergebnisse gehen nicht mehr verloren);
   export_trades nach DB-Persist aus RAM entfernt (Cloud+Lokal), Roh-Upload
   sofort freigegeben, malloc_trim nach Result-Uploads, create_job pruned alte
   Exporte; UI: Standard "Lokal" wenn Worker online + Hinweis-Toast bei Cloud.
3. **Mobile-Fix**: tools-menu-dropdown als Bottom-Sheet (≤640px), Viewport-Clamp ≤968px.
4. **Tiefenanalyst**: HIGH-News → volle Analyse; MEDIUM-News → kompaktes
   Delta-Update (DEEP_UPDATE_SYSTEM, baut auf letzter Voll-Analyse
   ai_deep_report_full auf, Outlook-Merge); Tages-Limit DEEP_NEWS_DAILY_CAP=8
   (env-überschreibbar); geplante Session-Opening-Läufe unverändert.
- Tests: backend/tests/test_improvements_2906.py (12 Tests grün)

## Umgesetzt (01.09.2026 / Juni-2026-Session) – Worker-Token-Fix + OpenRouter
Branch-Basis: conflict_300826_2041 (Referenz alte Version: conflict_270826_1202).
1. **Worker-Token-Fix (Root Cause: Cache-Desync)**:
   - `services/local_exec.get_token`: cached KEIN Zufalls-Token mehr wenn db=None;
     Token-Anlage jetzt atomar (find_one_and_update + $setOnInsert) → Multi-Prozess-sicher.
   - Neu `refresh_token(db)` (3s-Drossel) + `require_worker` liest bei Token-Mismatch
     einmalig frisch aus Mongo → heilt veralteten Cache nach Deploy/Neustart/Regenerate
     auf anderer Instanz (vorher: dauerhaft 401 für gültige Worker).
   - Frontend `LocalWorkerPanel.js`: Token-Fetch & Regenerate prüfen r.ok; bei
     abgelaufenem Admin-JWT klare Fehlermeldung (lw-token-error) statt stillem
     Leer-Token; Copy-Button disabled ohne Token (verhindert Kopieren leerer Tokens).
2. **OpenRouter**: `_quota_cooldown_s` erkennt "free-models-per-day" (Bindestrich-
   Variante) als Tageslimit → Key-Cooldown bis UTC-Mitternacht statt 10-min-Hämmern.
3. **.gitignore**: Whitelist `!local_worker/*` ergänzt (Schutz gegen "Worker-Paket
   unvollständig"); worker_config.json/worker_data/ bleiben ignoriert.
4. **Tests**: tests/test_worker_token_fix.py (11 Tests) + Testing-Agent-Suite
   test_iter31_worker_token_review.py (13 Tests) – alle grün; E2E mit echtem worker.py.
OpenRouter-Key-Analyse: Limits gelten PRO KONTO (nicht pro Key): 50 Free-Requests/Tag
(<10$ lifetime), 1000/Tag nach einmaligem 10$-Kauf. Empfehlung an User dokumentiert.

## Umgesetzt (Follow-up Session 2) – OpenRouter-Rotation-Verbesserungen
User-Feedback: '~8 Keys aus verschiedenen Konten, trotzdem überlastet.'
Diagnose (live verifiziert): Key-ANZAHL ist nicht der Engpass (Haupt-Key = 10$-Konto
mit 1000 Free-Req/Tag, Backups Free-Tier je 50/Tag, Prod hat 9 Keys ≈ 1400/Tag
Kapazität vs. ~150-250/Tag Bedarf). Echte Ursachen behoben:
1. **Minuten-Limits** (20 req/min pro Konto) sperrten Keys fälschlich 10 min →
   jetzt nur 65s (MINUTE_LIMIT_COOLDOWN_S, _quota_cooldown_s klassifiziert
   minute/daily/402/default getrennt).
2. **Upstream-Überlastung** der Free-Modelle (z.B. Nemotron): Key-Wechsel half nie,
   verbrannte aber alle Keys → is_upstream_overload() in generate_chain+stream_chain:
   sofort nächstes Modell, Keys bleiben nutzbar.
3. **Key-Ampel**: GET /api/ai/openrouter/keys (Admin) fragt live openrouter.ai/api/v1/key
   pro Key ab (maskiert, free_tier, usage, Hinweise). Noch ohne UI (Backlog).
Tests: test_key_rotation_improvements.py (9) + test_iter32_key_endpoint_review.py (5),
Gesamt 129 relevante Tests grün (iteration_32.json).

## Umgesetzt (Follow-up Session 3) – RAM-Warteschlange + Daten-Ordner pro Worker
1. **RAM-Warteschlange** (services/ram_queue.py): Cloud-Jobs (Backtest, Optimizer,
   alle 6 Regime-Lab-Starts) starten nur bei genug freiem Container-RAM
   (cgroup-Limit, MIN 150 MB via RAM_QUEUE_MIN_FREE_MB); sonst FIFO-Queue mit
   Auto-Start durch Watcher (10s-Takt, malloc_trim vor Messung, 12h-Timeout,
   Cancel-Unterstützung). Job-Phase zeigt "Wartet auf freien Server-RAM…".
   Response-Feld ram_queued. Lokale Worker-Jobs unberührt.
2. **Daten-Ordner pro Worker**: settings.data_dirs{worker_id: pfad} in Mongo;
   Poll liefert jedem Worker SEINEN Pfad (get_settings_for_worker); neuer
   Endpoint POST /api/localworker/worker/{id}/data-dir; UI-Panel: eigenes
   Feld je Online-Worker (lw-set-datadir-{worker_id}); Worker v1.9.1 übernimmt
   Server-Pfad (validiert, persistiert worker_config.json, Live-Switch wenn idle)
   und meldet aktuellen Pfad in data.data_dir.
3. **Hauptkonto-Schonung**: :free-Modelle nutzen Backup-Keys zuerst, Primär-Key
   (10$-Konto, für Bezahl-Modelle) nur als Reserve (generate_chain+stream_chain).
Tests: test_ram_queue_and_worker_datadir.py (8) + test_iter33_ramqueue_datadir_review.py
(4) + Regression 44/44 grün (iteration_33.json).

## Umgesetzt (Follow-up Session 4) – OOM-Crash-Schleife (RAM-Bremse)
User-Bug: OOM alle ~30 min OHNE Nutzung (Render 512 MB, 35 Events). Ursache:
nach jedem Boot lädt boot_backfill 23 Symbole × 14 Tage parallel zu KI-Zyklen
→ Crash-Schleife. Fix: ram_queue.wait_for_ram() (RAM-Bremse mit trim+Timeout);
eingebaut in boot_backfill (je Symbol, 120 MB/900s) und ai_engine-Analysezyklus
(110 MB/600s); ram_guard loggt alle 3 min "Prozess X MB / Container Y MB frei"
(OOM-Diagnose in Render-Logs). Env-Hebel für User: BOOT_BACKFILL_DAYS,
RAM_QUEUE_MIN_FREE_MB, BOOT_BACKFILL_ENABLED=0. Tests 11/11 grün (iteration_34).

## Backlog / Nächste Schritte
- P1: strategy_coin_toggles-Bootmigration bulken (Boot auf Atlas langsam)
- P1: Erklärung/ggf. Vereinfachung Dynamische Strategie vs. Regime-Lab (User entscheidet)
- P2: Worker-seitige Kappung großer Result-Uploads
- P2: scanner.candle_buffer auf CandleArray umstellen (weitere RAM-Ersparnis, riskanter)

## Umgesetzt (02.09.2026) – Render Pro 2 GB, Positionsgröße, Setups, Setup-Entdeckung
Branch-Basis: conflict_020926_0652. Prod-Befund (read-only, scripts/sizing_check_prod.py):
Live-Trades 30 Tage n=40, Marge Ø 11,6 / Median 8,2 USDT bei ~700 USDT Equity, Hebel
Median 8,5x, Risiko/Trade ~0,1–0,5 USDT. Kette: Coin-max_capital 30 × capital_pct
(LLM-Modus 20–30 %, getrieben von "defensiven" Lektionen) × ml_risk_scale 0,5.
squeeze_breakout: 48 % Win Paper, 7 % Win live (n=15) → Divergenz.
1. **RAM (2 GB)**: `is_big_ram` schaltete Kerzen-Cache/Orderflow/ML bereits um – neu:
   Start-Log `RAM-Profil …` + `/api/system/ram.container` zur Verifikation auf Render;
   `ram_queue._cgroup_free_mb` zieht `inactive_file` (Page-Cache) ab → RAM-Bremse vor
   KI-Analysen blockiert nicht mehr grundlos bei großem Datei-Cache.
2. **Bugfix upstream**: `services/orderflow.py` und `services/ai_ml_lab.py` nutzten `os`
   ohne Import → Module scheiterten beim Import (kein echter Bitunix-Orderflow für die KI,
   kein ML-Auto-Training). Behoben.
3. **Risiko-basierte Positionsgröße** (`services/position_sizing.py`, Modus `risk`,
   Boot-Migration `risk_sizing_v1`): Risiko = Equity × risk_per_trade_pct (2 %) × Skalierung;
   Notional = Risiko / SL-Abstand; Hebel = Liq `auto_lev_value` % hinter SL (Deckel
   risk_max_leverage 50, Coin-Max, Swing-Cap); Marge ≤ risk_max_margin_pct (15 %) × Equity.
   Skalierung = MIN(Überzeugung [Boden 0,5], ML-Faktor) statt Produkt. Legacy-Pfad
   unverändert wählbar (`sizing_mode=legacy`). Prompt-Block erklärt capital_pct neu.
   Trade-Dokument speichert `sizing` (Budget, Skalierung, Deckel).
4. **Playbook**: neue Setups `order_block`, `fvg_fill`, `htf_range` (Shadow über Reife-Gate);
   `SMC-Zonen`-Zeile (OB/FVG 5m/15m/1h, `services/smc_zones.py`) je Asset im Prompt;
   Paper/Live-Divergenz-Gate (`live_blocked`, nur echte Live-Trades, kein Bypass, Re-Test 14 d);
   KI-eigene Setups (`new_setups` im Analyst-JSON → `propose_custom_setup`, max 6,
   Shadow-Test, Ausmusterung bei 'schwach'). UI: Reife-Tabelle mit Live-Spalte + KI-Badge.
5. **UI**: KI-Panel Abschnitt Positionsgröße (Modus, Risiko %, Max-Marge %, Max-Hebel,
   Überzeugungs-Boden).
- Tests: tests/test_risk_sizing_and_playbook_v2.py (12 Blöcke grün, inkl. on_signal-E2E:
  Legacy 4,5 USDT → Risiko 40 USDT @ 50x). Backend-Suite 934 grün; 3 Failures + 4 Errors
  sind upstream identisch (fix_custom_ai_trades, iter49_retry, strategy_insights quota,
  test_credentials.md fehlte) – nicht durch diese Änderungen verursacht.

## Backlog / Nächste Schritte
- P0: Nach Render-Deploy Log prüfen: `RAM-Profil: 2048 MB … GROSS`, `Boot-Migration: KI-Positionsgröße -> Risiko-Modus`.
- P1: Sweep-Trigger (1m-Wick-Detektor → gezielte Einzel-Symbol-Analyse) – siehe ML_REBUILD_STATUS.
- P1: Coin-Auto-Hebel wieder aktivieren / Hebel-Proposal-Migration überdenken (im Risiko-Modus irrelevant, da Hebel dort aus SL abgeleitet wird).
- P2: Per-Setup-Risiko-Faktor (bewährt 1,0 / test 0,5) statt LLM-capital_pct.

---

## Iteration 02.09.2026 – Badge-Bug, Rohstoff-Feed, Setup-Lebenszyklus, Worker

### Auftrag
1. Badge-Bug: BTC-Trades blieben nach Wechsel auf ETH als Chart-Badges stehen.
2. Setup-Konzept „erst Daten sammeln → live → bei Schwäche zurück“ inkl. Auto-Rollback der Parameter.
3. Warum wurde `squeeze_breakout` live pausiert?
4. Veraltete Kursdaten GOLD/SILVER/OIL (11 min) → Stale-Price-Guard.
5. Guards auf Sinn prüfen (799 Blockaden).
6. Lokaler Worker (Windows): ganzen RAM nutzen; Binance-Download-Fehler – Worker sonst nicht verändern.

### Umgesetzt
- **Badge-Bug** – `frontend/src/hooks/useTradeMarkers.js`: bei Symbolwechsel Badges/Zähler/Pin sofort
  zurücksetzen; `apply()` setzt Badges auch ohne fertige Chart-Serie; `sameSymbol`-Vergleich.
  `MainChart.js` filtert Badges zusätzlich auf das aktuelle Symbol.
- **Rohstoff-Feed** – `core/instruments.py`: GOLD/SILVER/OIL Live-Kurse von Bitunix (XAUUSDT/XAGUSDT/CLUSDT)
  statt Yahoo-Futures (10–15 min verzögert, ~1 % Preisabweichung zum Fill). Yahoo = `live_fallback`.
  Neue Properties `yahoo_ref`, `has_trading_pauses` (scheduler/_is_stale, paper_execution, routers/general).
- **Setup-Lebenszyklus** – neues Modul `services/setup_lifecycle.py`, eingehängt in `services/ai_playbook.py`:
  Freischaltung ≥5 Trades & (PnL>0 ODER WR≥55 %); Rückstufung ≥8 Live-Trades seit Freischaltung &
  (PnL ≤ −3 % Margin ODER WR<35 %); wieder live nach 5 guten Paper-Trades seit Rückstufung;
  Parameter-Profile (SL %, TP-Ratio, TF, Hebel) versioniert, Tuning max. ±20 %/Schritt ab 10 Trades,
  Auto-Rollback auf beste Version, Prompt-Block `SETUP-PROFILE`. API `/api/ai/playbook`: `rules`,
  `lifecycle`, `maturity[].phase/profile/paper_since_demotion`. UI `AIEquityPanel.js`: Spalten Profil/Phase.
  Tests: `tests/test_setup_lifecycle.py`.
- **Binance-Downloads** – `services/history_sources.py`: 6 Hosts im Wechsel, 8 Versuche, echte Fehlerursache
  im Log, 429/418 mit Retry-After.
- **Lokaler Worker 1.9.2** – `ram_limit_mb` 0 = automatisch (RAM − 1 GB); Limit live in `candle_cache`
  nachgezogen; Download-Session mit certifi + `trust_env`; Server-Default 0; UI-Label; requirements + certifi.

### Befunde (Prod-DB, 30 Tage)
- `squeeze_breakout`: Paper 64 Trades/48 %/+61.85 USDT, live 15 Trades/1 Gewinn/−21 USDT → Pause korrekt
  (Slippage bei Momentum-Einstiegen, GOLD 1,16 % Yahoo/Bitunix-Abweichung – jetzt behoben).
- Guards: Richtungs-Guard 788× (Limit 3→5→8), Korrelations-Guard 193× (jetzt aus), Stale 184× davon 182×
  GOLD/SILVER/OIL (Feed-Fix), Playbook 31×, Momentum-Bremse 29× (datenbasiert), Cluster 23×, Duplikat 4×.

### Offen
- P1: Pre-existing Testfehler `backend/tests/test_fix_custom_ai_trades.py::test_reject_deregisters_strategy_and_closes_trades`
  (fällt auch im unveränderten Original-Repo).
- P2: Profil-Versionsverlauf im UI; Guard-Blockaden im Diagnose-Panel ins Verhältnis setzen.
