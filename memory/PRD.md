# PRD – KI-Trader (Crypto Scanner / Daytrading-Plattform)

## Original-Problemstellung
Bestehende, produktiv laufende externe Daytrading-Website (GitHub: dean06greif-ai/KI-Trader, feature-branch, Deploy auf Render – Originalstruktur muss erhalten bleiben). Verbesserungen sauber, modular und rückwärtskompatibel in die bestehende Architektur integrieren:
1. Setups/Strategien des KI-Traders analysieren, schlecht laufende verbessern; KI-Trader soll Setups im Datensammel-Modus selbst anpassen (ohne Overfitting) und neue Setups autonom entdecken (Nutzerwahl: volle Autonomie).
2. Funding-Fade-Setup (klassisch: extrem positive Funding → Short, extrem negativ → Long).
3. Session-Open-Setup (London-/US-Open Opening-Range; Breakout UND Fade, KI wählt je Vol-Regime).
4. Teilfill-Buchung: teilgefüllte Börsen-Limit-Orders anteilig als Position verbuchen statt auf Vollfill zu warten (Kontext: Teilfüllungen bei knappem Kapital).

## Architektur (Bestand, unverändert gelassen)
- FastAPI-Backend (`backend/server.py` + `routers/` + `services/` + `strategies/`), React-Frontend (`frontend/`), MongoDB Atlas (Produktions-DB `crypto_scanner`), Supabase-Spiegel fürs KI-Gedächtnis, Bitunix-LIVE-Börsenanbindung, IBKR-Gateway, Telegram-Bot, Multi-LLM (Cerebras/OpenRouter/Groq/Mistral mit Backup-Keys).
- Setup-System: `services/ai_playbook.py` (SETUPS, Reife-Gate Paper→Live, Profile/Tuning) + `services/setup_lifecycle.py` (Promotion/Demotion, Parameter-Tuning ±20% mit Cooldown, Custom-Setup-Discovery durch die KI – erfüllt bereits die geforderte autonome Selbstoptimierung ohne Overfitting).
- Limit-Order-Live-Sync: `services/limit_live_sync.py` (Armierung echter Limit-Orders, Fill-Buchung über normale Pipeline via `_live_prefill`).

## Umgesetzt am 05.06.2026 (diese Iteration)
- Repo (feature-branch) in die Umgebung übernommen, Original-Ordnerstruktur unangetastet (Render-kompatibel), Backend+Frontend lauffähig mit Produktions-Env.
- **Funding-Fade-Setup**: neues Modul `services/funding_fade.py` (rein/testbar; Schwellen 0.05%/8h „überhitzt“, 0.10%/8h „extrem“, OI-Lesart); FUNDING-FADE-RADAR-Zeilen im Makro-Block (`ai_engine_context._macro_block`, nutzt vorhandene funding_oi-Daten); Setup `funding_fade` im Playbook.
- **Session-Open-Setup**: neues Modul `services/session_open.py` (London 09:00 / US 15:30 Berlin, Mo–Fr, 90-min-Fenster, Opening-Range 15 min, Vol-Regime via ATR-Ratio 15/96 → hoch=Breakout, niedrig=Fade, normal=frei); SESSION-OPEN-Block in `_analysis_extra_blocks`; Setup `session_open` im Playbook.
- **Divergence-Setup** (Empfehlung aus STRATEGIEN_BEWERTUNG.md) im Playbook ergänzt.
- **Verbesserung schwacher Setups**: REGIME-FILTER-Regel im Playbook (breakout/squeeze_breakout/momentum_news nur im Trend-/High-Vol-Regime; im Chop range_fade/mean_reversion/htf_range). Neue Setups laufen automatisch durchs Reife-Gate (Phase „sammelt“, Paper zuerst → kein Overfitting-Risiko).
- **Teilfill-Buchung** in `limit_live_sync.py`: Config `partial_book_min` (10 min); reine Funktion `should_book_partial` (sofort bei knappem Kapital < scarce_reserve, sonst nach Timeout); `_book_partial` storniert erst den Rest an der Börse (Doppelbuchungs-Schutz), liest den finalen Fill-Stand und verbucht den gefüllten Teil anteilig über die normale Pipeline; Telegram-/Feed-Meldungen; Registry-Resolve.
- **Tests**: `tests/test_funding_session_partialfill.py` (30+ neue Regressionstests); 2 veraltete Setup-Anzahl-Assertions (13→16, 10→16) aktualisiert. Testing-Agent: Backend & Frontend 100%, keine kritischen Issues.

## Bekannte Umgebungs-Hinweise (kein Bug)
- Teile der alten Repo-Testsuite setzen eine leere DB voraus → schlagen gegen die Produktions-Atlas-DB fehl (exakte Trade-Zählungen); standalone/reine Tests grün.
- `/api/autotrade/status` existiert nicht (korrekt sind `/api/autotrade/config`, `/api/autotrade/balance`).

## Backlog / Nächste Aufgaben
- P1: Playbook-/Reife-Übersicht im Frontend sichtbar machen (neue Setups + Phase im UI).
- P1: BTC-Korrelations-Filter für Alt-Setups (Empfehlung aus STRATEGIEN_BEWERTUNG.md).
- P2: Backtest der neuen Setups über den vorhandenen Backtest-Optimizer.
- P2: Konfigurierbare Funding-Schwellen / Session-Fenster über Settings-UI.
