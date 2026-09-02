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

## Backlog / Nächste Schritte
- P1: strategy_coin_toggles-Bootmigration bulken (Boot auf Atlas langsam)
- P1: Erklärung/ggf. Vereinfachung Dynamische Strategie vs. Regime-Lab (User entscheidet)
- P2: Worker-seitige Kappung großer Result-Uploads
- P2: scanner.candle_buffer auf CandleArray umstellen (weitere RAM-Ersparnis, riskanter)
