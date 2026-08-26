# PRD – KI-Trader (externe Render-Deployment, Repo: dean06greif-ai/KI-Trader, Branch conflict_240826_1008)

## Original-Problemstellung
Bestehende, produktiv laufende Daytrading-Website (FastAPI + React + MongoDB, deployt auf Render) verbessern – sauber, modular, rückwärtskompatibel, Originalstruktur beibehalten. Konkrete Bugs:
1. Korrelations-Guard im AI Trading Panel wird nach manuellem Ausschalten ständig wieder eingeschaltet.
2. Lektionen: KI schränkt sich selbst zu oft ein (Verbote statt dynamischer Anpassung) → Overfitting-Gefahr.
3. 402-Spam: erschöpfte Cerebras-Free-Keys (Payment Required) werden jeden Zyklus alle 16 erneut probiert.
Zusätzlich: überflüssige/unausgereifte Funktionen identifizieren (entfernen nur wenn eindeutig, sonst melden).

## Architektur
- backend/ FastAPI (server.py + routers/ + services/ ~80 Module), MongoDB (lokal: mongodb://localhost:27017, DB crypto_scanner; produktiv: Atlas via Render-Env)
- frontend/ React (CRA/craco), AITradingPanel.js als KI-Cockpit
- Struktur unverändert gelassen (Render-Deploy-kompatibel). Lokale .env bewusst OHNE externe API-Keys (Cerebras/OpenRouter/Bitunix/Telegram/Supabase), um Produktions-Quotas & Live-Handel nicht zu beeinflussen.

## Umgesetzt (Juni 2026)
1. **Korrelations-Guard-Hoheit** (`ai_engine.py::_tuning_guard`, `ai_knowledge.py`): JEDE KI-Änderung an correlation_guard (an UND aus) wird nur noch als Vorschlag geparkt (needs_confirmation) – gilt auch für den Autonomie-Review geparkter Vorschläge. Manuelles Umschalten durch den Trader wirkt sofort und bleibt bestehen.
2. **402-Cooldown-Fix** (`ai_providers.py::usable_key_indices`): Keys mit 402/Tages-Quota-Cooldown werden bis UTC-Mitternacht übersprungen; der frühere "alle im Cooldown → alle erneut probieren"-Fallback gilt nur noch für kurze Minuten-Rate-Limits. Kette wechselt direkt zum nächsten Provider.
3. **Lektionen-Lebenszyklus & Overfitting-Schutz** (`ai_lessons.py`, `ai_learning.py`):
   - Abgelaufene Lektionen → status "dormant" (zurückgestellt, NICHT gelöscht); nach 45 Tagen ohne Re-Validierung endgültig entfernt (DORMANT_DELETE_DAYS).
   - Reaktivierung: erneute Bestätigung im Lernlauf (exakt gleicher Titel) reaktiviert + verlängert Gültigkeit progressiv (renewal_valid_until: 14–90 Tage, wächst mit Bestätigungen).
   - Kontextgebundene Lektionen bekommen Default-Verfall (21 Tage), müssen sich neu validieren.
   - Pauschale Verbots-Lektionen ohne Marktkontext (is_absolute_rule) werden verworfen; Prompt fordert adaptive Wenn-Dann-Regeln statt Verboten; hartes Verbot nur bei >=20 entschiedenen Trades + Kontext.
   - Lern-Reset "veraltet" stellt zurück statt zu löschen; dormant-Block im Lernprompt.
   - Frontend: dormant-Lektionen gedimmt + Badge "zurückgestellt – reaktivierbar" (data-testid ai-lesson-dormant-{id}), Zähler zeigt nur aktive.
4. **Regressionstests**: backend/tests/test_fixes_guard_402_lessons.py (17 Tests) + Live-API-Suiten (test_regression_iter15_guard_lessons_api.py, test_iter16_park_reactivate_api.py). Gesamt-Suite: 888 passed.
5. **Trader-Verwaltung für Lektionen** (Iteration 2): POST /api/ai/lessons/{id}/park und /reactivate (admin-geschützt); UI-Buttons zum Zurückstellen/Reaktivieren, "N× validiert"-Badge (confirmations >= 2), Key-Status-Dropdown zeigt "frei in ca. X min/h" für limitierte Keys.
6. **Keys aus verschiedenen Konten** (Trader-Info): 429-Streak sperrt restliche Keys NICHT mehr mit (nur Skip für den aktuellen Aufruf) – gesunde Keys anderer Konten bleiben nutzbar.

## Cleanup-Analyse (gemeldet, nichts entfernt)
Kein Service-Modul ist tot – alle ~80 services/ werden importiert und sind testabgedeckt. Kandidaten zur späteren Konsolidierung (nur nach Rücksprache):
- Regime-Familie stark fragmentiert: regime.py, regime_engine, regime_reactive, regime_lab, regime_opt, regime_truth (~5.6k Zeilen).
- Mehrfache Sim-Engines: backtester, fast_sim, parallel_sim, gpu_accel (gpu_accel nur von fast_sim genutzt; auf Render ohne GPU wirkungslos, aber harmlos).
- ai_engine.py (4.1k Zeilen) – Aufteilung in Module würde Wartbarkeit erhöhen.

## Backlog / Nächste Aufgaben
- P0 (Sicherheit, Nutzer-Aktion): API-Keys/Secrets wurden im Klartext geteilt → rotieren (Bitunix, Mongo Atlas, Telegram, alle LLM-Keys)
- P1: Regime-Module konsolidieren (nach Freigabe des Traders)
- P1: GET /api/ai/lessons & /insights sind unauthentifiziert lesbar (bewusstes Design lt. core/auth.py – ggf. absichern)
- P2: LessonStore.all() Write-on-Read (Lifecycle-Persist bei GET) beobachten
- P2: ai_engine.py modular aufteilen

## Test-Zugang
Admin / Dean06Greif!/Admin (siehe /app/memory/test_credentials.md)

## Update 2026-08-26 (10. Handover — neuer Agent, Branch conflict_250826_1035)
- Repo neu nach /app eingerichtet; ML_REBUILD_STATUS.md aus User-Repo (Branch 0.83) wiederhergestellt und mit 10. Handover aktualisiert — MASSGEBLICHE Übergabedatei, Pflicht-Update je Schritt.
- Großer Read-only-Prod-Check durchgeführt (scripts/big_check_prod.py + deep_check_prod.py + config_check_prod.py).
- RCAs: Cerebras-Free-Tier zum 17.08.2026 eingestellt (alle 16 Keys 402 payment_required — kein Quota-Sharing-Problem); Hebel-Proposals wirkungslos solange auto_leverage_enabled=true (effective_leverage ignoriert bestätigten Hebel); custom_23a30b65-Slippage ~5% = Messartefakt (price-Feld-Fallback = Bitunix-Schutzpreis); "Reward>0: 0/315" war Check-Skript-Bug (Feld heißt score; real 169/316 positiv).
- Umgesetzt + getestet (892 Unit-Tests grün, testing_agent iteration_18 100%): smart_skip_move_pct-Migration 0.02→0.10 (idempotent, Marker), Slippage-Fill-Source-Fix (allow_price_fallback=False + fill_price_for_slippage mit 2%-Guard + Retry), cleanup_slippage_artifacts.py (Dry-Run-Default, Prod-Schutz), big_check_prod.py-Fixes.
- Offene User-Entscheidungen: Klick-Liste Prod-UI (Heatmap aus, Analyst auf Groq, Hebel/auto_lev, Trade-Manager max_lev 200→25, Stale-Limit 15, Cleanup-Skript auf Render); Code-Fix Proposal-Apply (auto_leverage_enabled mitsetzen) nur nach Freigabe.
- Backlog: momentum_news/Konfidenz-70-74-Analyse, Stale-Limit je Asset-Klasse, days=0-Klemme slippage-stats, Gate-Neubewertung nach 2-4 Wochen sauberer Daten (Kriterium oos_auc >= 0.60 stabil).

## Update 2026-08-26 (Schritt 2 — Klick-Liste automatisiert, testing_agent iteration_19 100%)
- Boot-Migrationen (services/boot_migrations.py, Marker settings.boot_migrations, laufen beim Render-Deploy von selbst): Cerebras-Slots ersetzt (Analyst: nemotron:free -> gpt-oss-20b:free -> deepseek-v4-flash), use_heatmap_data aus, Slippage-Artefakte bereinigt, Hebel-Senkungs-Proposals angewendet (Erhöhungen bleiben liegen, restriktive abgelehnt).
- Dauerhaft: _apply_changes setzt bei Hebel-Changes auto_leverage_enabled=false mit; Duplikat-Guard (dup_entry_window_min=30, DB-basiert); Rohstoff-Stale-Limit (stale_price_max_min_commodity=20).
- User-Entscheidungen: Trade-Manager-Kappen 200 bleiben (Absicht); kein neuer Analyst-Provider nötig (OpenRouter-Beweis dokumentiert; Groq-Korrektur: 200k TPD/Konto, 8k TPM ungeeignet).
- Tests: tests/test_boot_migrations_and_guards.py 7/7 grün, 892 Unit-Tests grün.
- Offen: Render-Deploy anstoßen + Migrations-Log prüfen; Erhöhungs-Proposals (z.B. AVAX 9.6x) manuell; Backlog: momentum_news/Bucket-70-74-Analyse, Gate-Neubewertung nach Messphase.

## Update 2026-08-26 (Schritt 3 — Guard-Trennung Sammel/Live, testing_agent iteration_20 100%)
- Bugfix (User-Report): Richtungs-/Korrelations-/Cluster-Guard, MasterPrompt-max_open_trades und Tagesrisiko zählten Sammel-Trades mit → jetzt getrennte Welten (Live prüft nur echte Risiko-Trades, Sammel nur Sammel mit eigenem Limit collection_max_same_direction, vorher tote Config).
- Proposal-Aufräumer: _insert_proposal supersedet ältere offene Duplikate (gleicher Scope+Symbol+Keys) automatisch.
- Hebel-Wirkungs-Check: scripts/leverage_effect_check.py (read-only); Prod-Lauf 26.08.: 15/16 Symbole Auto-Hebel/Mismatch, nur POL OK — bestätigt RCA; nach Deploy erneut laufen lassen.
- Tests: tests/test_guard_separation.py 7/7 grün, 892 Unit-Tests grün, Suiten Schritt 1/2 weiter grün.
