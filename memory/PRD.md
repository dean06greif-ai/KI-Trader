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

## Umgesetzt am 06.06.2026 (Iteration „Setups je Anlageklasse + Kapital-Zuweisung“)
- **Recovery**: Die vorherige Session (Branch conflict_060926_0736) enthielt nur ein leeres Template – der echte Stand lag auf `conflict_050926_2336` und wurde nach /app übernommen (Originalstruktur unverändert, Render-kompatibel). Backend/Frontend laufen wieder mit Produktions-Env. Ein in der DB hinterlassener Zwischenstand der abgebrochenen Session (Klassen-Scopes mit deutschen Schlüsseln `krypto/indizes/rohstoffe/forex`) wird automatisch auf die kanonischen IDs migriert und zusammengeführt.
- **Neu `services/setup_asset_class.py`**: 4 Anlageklassen (crypto/indices/resources/forex) aus `core/instruments.py`; alle Setups in jede Klasse kopiert, Ausnahme: `funding_fade` nur Krypto (Perp-Funding-Daten gibt es nur dort). Kompakte Klassen-Hinweise für den Prompt (Forex: kleine SL-Prozente/Range-Fokus, Indizes: US-Session/Gaps, Rohstoffe: Makro/News).
- **`services/ai_playbook.py` klassenbasiert**: Reife-Gate, Rückstufung, Wieder-Freischaltung, Parameter-Profile (SL/TP/TF/Hebel) und Statistik laufen je Klasse (`settings.ai_playbook_state.classes.<cls>`); globale Felder bleiben abgeleitet (live-reif = in einer Klasse reif; rückgestuft global = in allen Klassen) für Alt-Aufrufer/UI. Migration kopiert den bisherigen Status in jede Klasse (live-reife Setups bleiben live). KI-Revisionen: rückgestufte Setups darf die KI pro Klasse neu fassen (`setup_revisions` im Analyse-JSON → `revise_setup`, max. 1 je Setup/14 Tage, Validierung startet neu). KI-eigene Setups werden erst ausgemustert, wenn sie in keiner Klasse bewährt sind.
- **Gesamtbild-Regel** (`setup_lifecycle.breadth_ok`): Ein einzelnes schlecht laufendes Asset stuft ein Setup in seiner Klasse NICHT zurück – erst wenn ≥ 1/3 der gehandelten Assets (≥3 Trades) negativ sind.
- **Neu `services/setup_capital.py`** (regelbasiert, ohne LLM): Kapital-Faktor 0.25–1 unter dem Max-Kapital je Asset aus Setup-Urteil in der Klasse × Setup×Asset-Historie × Einstiegsqualität (Konfidenz + ML-p_win). Eskalation je Setup×Asset: PnL<0 & WR<45 % → ×0.5; ab 6 Trades mit WR ≤30 % oder PnL ≤ −5 % Margin → Live AUSGESETZT (Trade läuft nur als Paper-Datensammlung). Wirkt als `setup_asset_scale` am Signal in `bitunix_trade` (Legacy-Pfad), `position_sizing` (Risk-Modus) und `limit_live_sync`; Live-Gate in `ai_engine._setup_live_gate` prüft Klasse + Aussetzung.
- **Token-Optimierung**: Playbook- und Coin-Einstellungs-Block werden je Gruppen-Lauf nur für die Klassen/Symbole der Gruppe eingesetzt (Platzhalter in `ai_engine_context`, `resolve_group_blocks`); Setup-Beschreibungen/REGELN/TF-Zeilen gekürzt (Forex-Block ~760 statt ~1900 Tokens, Krypto ~1500); bei HOLD liefert die KI nur symbol/action/confidence + max. 8 Wörter reasoning, leere Listen entfallen (Output-Tokens); refresh()-Cache 60 s für die 3 Gruppen-Läufe eines Zyklus.
- **UI**: Setup-Reife-Tabelle (KI-Trader → Verlauf) mit Tabs Gesamt/Krypto/Indizes/Rohstoffe/Forex, Spalte „Assets“ (×0.5 reduziert / ⏸ ausgesetzt), Badge „Rev.n“ für KI-Revisionen, Hinweis auf ausgeschlossene Setups (`SetupMaturityTable.js`). API `/api/ai/playbook` liefert zusätzlich `classes` + `class_order`.
- **Tests**: neu `tests/test_asset_class_setups.py` (17 Tests: Mapping, Ausschlüsse, Kapital-Faktoren, Sizing, Gesamtbild-Regel, Migration, Klassen-Cache, Revisionen, Maturity); bestehende Suiten auf das Klassenmodell angepasst (test_risk_sizing_and_playbook_v2, test_playbook_demotion, test_setup_live_gate, test_playbook_tf – dort zuvor 3 veraltete Assertions). Bekannte umgebungsbedingte Fehler: `test_maturity_feed_e2e` (zählt gegen Produktions-DB), `test_iter38_*` (Port 8055).

## Backlog / Nächste Aufgaben (aktualisiert 06.06.2026)
- P1: Manuelle Setup-Revision/Reset je Klasse über die UI (Admin-Endpoint auf `revise_setup`).
- P1: Klassen-Hinweise (HINTS) und Ausschlüsse über Settings konfigurierbar machen.
- P2: BTC-Korrelations-Filter für Alt-Setups; Backtest der neuen Setups je Klasse.
- P2: Weitere Token-Einsparung: Lektionen-/Performance-Blöcke je Klasse filtern.


- Teile der alten Repo-Testsuite setzen eine leere DB voraus → schlagen gegen die Produktions-Atlas-DB fehl (exakte Trade-Zählungen); standalone/reine Tests grün.
- `/api/autotrade/status` existiert nicht (korrekt sind `/api/autotrade/config`, `/api/autotrade/balance`).

## Backlog / Nächste Aufgaben
- P1: Playbook-/Reife-Übersicht im Frontend sichtbar machen (neue Setups + Phase im UI).
- P1: BTC-Korrelations-Filter für Alt-Setups (Empfehlung aus STRATEGIEN_BEWERTUNG.md).
- P2: Backtest der neuen Setups über den vorhandenen Backtest-Optimizer.
- P2: Konfigurierbare Funding-Schwellen / Session-Fenster über Settings-UI.
