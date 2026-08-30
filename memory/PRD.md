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

## Backlog / Nächste Schritte
- P1: strategy_coin_toggles-Bootmigration bulken (Boot auf Atlas langsam)
- P1: Erklärung/ggf. Vereinfachung Dynamische Strategie vs. Regime-Lab (User entscheidet)
- P2: Worker-seitige Kappung großer Result-Uploads
- P2: scanner.candle_buffer auf CandleArray umstellen (weitere RAM-Ersparnis, riskanter)
