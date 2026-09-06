# KI-Trader – Analyse & Maßnahmen (06.09.2026)

## 1. Datenverlust – Ursache (RCA)

**Befund aus `audit_log` und `settings.ai_master_prompt.history` der Produktiv-DB (read-only geprüft):**

| Zeit (UTC) | Quelle | Aktion | Wirkung |
|---|---|---|---|
| 20.08. 08:03 | `python-requests`, GCP-IP 34.16.x | `POST /api/ai/trader/reset` | **134 Paper-Trades + 169 Signale + 119 Rewards gelöscht** |
| 15.08., 05.09. (10:49–12:07, 20:44–21:10) | `python-requests`, GCP-IPs 34./35./104. | 23× `analytics/clear`, 9× `ai_rewards_clear` | weitere Paper-Trades/Rewards/Trade-Stats gelöscht |
| 05.09. 11:50–21:04 | dito | 20× `POST /api/ai/master-prompt` | MasterPrompt-Text → `UI-TEXT-42`, Lektions-Regeln → `COMBO-POLICY-42`, Regeln (Hebel 0/50, `max_trades_per_day 7`, `forbidden_terms martingale/all-in`) hin- und hergeschaltet |
| 05.09. | dito | `POST /api/ai/lessons` | Test-Lektion `TEST_Lektion_ChatCmd` (locked!) angelegt; MasterPrompt-Audit mit Test-Regeln löschte echte Lektionen |

Die IPs/User-Agents sind **automatisierte Test-Läufe (Testing-Agent) der letzten Entwicklungs-Sessions**, die
gegen die Preview liefen – und die Preview hing laut altem `memory/PRD.md` („Produktive .env (MongoDB Atlas) in
der Preview“) **direkt an der Produktiv-Datenbank**. Kein Bug im Code, sondern ein Prozess-Fehler.

**Nicht mehr rekonstruierbar:** Paper-Trades, die per SL/TP-Treffer geschlossen wurden (kein Protokoll mehr;
132 Stück). **Rekonstruierbar (160 Stück):** Trades, die der Trade-Manager/Trader per Aktion geschlossen hat –
`ai_trade_actions` enthält Symbol, Seite, Modus, PnL, Ergebnis, Zeiten; Entry/SL/TP kommen aus dem passenden
Signal (26 Trades). MasterPrompt: letzter echter Stand (v118 ≙ v102: Standard-Text, Hebel-Deckel 50) liegt in
der History.

## 2. Umgesetzt

### Wiederherstellung (`services/data_recovery.py`, `routers/recovery.py`, Boot-Migration `data_recovery_0906_v1`)
- läuft **einmalig automatisch beim nächsten Deploy** (idempotent, Marker in `settings.boot_migrations`)
- oder manuell: `GET /api/admin/recovery/preview` (nur lesen) → `POST /api/admin/recovery/run`; Report unter
  `GET /api/admin/recovery/report`
- MasterPrompt: neuester History-Stand ohne Test-Marker; Lektionen: nur eindeutige `TEST_`/`[QA` Titel entfernt;
  Paper-Trades: rekonstruiert mit `recovered: True` (Sekunden-Trades manuell per API = Test-Artefakte werden
  ausgelassen; Reward-Backfill ignoriert rekonstruierte Trades)
- lokal gegen eine **Kopie** der Produktiv-Daten verifiziert: MasterPrompt v118 zurück, 1 Test-Lektion weg,
  160 Paper-Trades zurück (26 mit Levels), 14 Test-Artefakte übersprungen

### Schutz vor Wiederholung (strukturell)
- **Trade-Papierkorb** `services/trade_trash.py`: `analytics/clear` und `ai/trader/reset` löschen Trades nicht
  mehr endgültig, sondern als wiederherstellbaren Batch (`auto_trades_trash`, 30 Batches).
  UI: im Lösch-Dialog der Analyse („Papierkorb“), API `GET /api/analytics/trash`,
  `POST /api/analytics/trash/restore/{batch}`.
- **MasterPrompt-History** 20 → 50 Versionen + `POST /api/ai/master-prompt/restore {version}`; UI-Knopf
  „Frühere Versionen“ im MasterPrompt-Panel.
- **Lektions-History** `ai_lessons_history` (Snapshot vor jeder Bestandsänderung, 30 Stände) +
  `GET /api/ai/lessons/history`, `POST /api/ai/lessons/restore/{id}`.
- **Prozess-Regel** (`tests/README_TESTING.md`): Preview/Tests NIE gegen die Produktiv-DB. Diese Session lief
  komplett gegen eine lokale DB (`crypto_scanner_dev`).

### Backtest-Seeding – vom Klick-Werkzeug zum Prozess
Vorher: nur manueller Start; „Anpassung“ = nächste von 3 festen Varianten; nach `exhausted` fing der
Einmal-Durchlauf wieder bei Variante 1 an (sichtbar in der Prod-History: identische Wiederholungen). Keine
Automatik, kein echtes Verbessern.

Jetzt:
- **Feintuning-Schritt** (`detectors.tune_candidates`, `runner.tune`): scheitern alle Varianten, werden um die
  beste In-Sample-Variante nur Risiko/Ziel (`sl_atr`, `tp_r` ×0,8/×1,25, max. 4 Kandidaten) variiert; nur ein
  Out-of-Sample-bestandener Kandidat wird übernommen (Status `tuned`, Parameter im State, wird beim nächsten
  Lauf zuerst erneut bestätigt). Overfitting-Bremse bleibt: Signal-Logik unverändert, wenige Kandidaten, OOS-Pflicht.
- **Automatik** (`services/setup_backtest/auto.py`): Schalter im Seeding-Panel, Intervall (12 h–7 Tage),
  Zeitraum, Anlageklassen; läuft im selben Job-Pfad wie der manuelle Start (Fortschritt sichtbar), nie parallel
  zu anderen Backtests, nie bei RAM-Knappheit, Verlauf der letzten 10 Läufe im State.
  API `GET/POST /api/ai/playbook/backtest/auto`. Standard **aus** (bewusste Entscheidung des Traders).
- Verifiziert: Lauf Indizes/30 Tage/Auto-Schleife → 9 Setups, je 3 Varianten + Feintuning (`tried 7`), Ergebnis
  ehrlich `exhausted` (auf 30 Tagen QQQ/SPY kein Edge nach Gebühren).

## 3. KI-Trader – Bewertung (was ist zu viel / fehlt)

**Datenbasis (Prod):** 73 230 `ai_decisions` (≈ 92 % HOLD), 82 279 `ai_chat_archive`, 4 011 `ai_knowledge`
(davon 2 481 `ml_finding`), 33 Strategie-Kandidaten mit **0 Ghost-Trades**, 2 015 Proposals, 57 ML-Gate-Modelle.

**Zu viel / fraglich**
1. **Strategie-Labor (`ai_strategy_lab`)**: 33 Kandidaten, `ai_ghost_trades` leer, `auto_develop` alle 7 Tage
   an. Die Ghost-Phase wird faktisch nie durchlaufen → das Labor erzeugt Kandidaten und LLM-Kosten ohne
   Ergebnis. Empfehlung: `allow_ai_create`/`auto_develop` abschalten oder Kandidaten direkt als Playbook-Setup
   (`new_setups`) führen – dort existiert bereits der komplette Lebenszyklus (Paper → Reife-Gate → Live).
   Zwei parallele „neue Strategie“-Pfade sind einer zu viel.
2. **Zwei Lern-Pfade für Lektionen**: `learning_memory`/`ai_knowledge` (Forschungs-Analyst, ML-Findings 2 481)
   und `ai_lessons`. Die 2 481 ML-Findings landen nie als prüfbare Lektion. Empfehlung: ML-Findings nur noch
   in den ML-Gate-Report, nicht ins Gedächtnis.
3. **Rollen-Anzahl** (9 LLM-Rollen + Radar, Supervisor, Copilot, Model-Watch): auf Free-Tier-Modellen
   (Cerebras tot, OpenRouter `:free`) bedeutet das viele Ausfälle (`news watcher LLM failed`). Kern-Rollen sind
   Analyst, Trade-Manager, Lern-Modul, News-Wächter. Markt-Beobachter (62 230 Snapshots) und Summarizer sind
   rein deterministisch/berichtend und bräuchten kein LLM.
4. **Chat-Archiv** 82 k Einträge ohne Nutzen für Entscheidungen → Housekeeping-Fenster verkürzen.
5. `custom_14f031fftest`-Signale (50) und eine Collection namens `db.ai_lesson_candidates` sind Alt-Artefakte.

**Was fehlt / war schwach**
1. **Kein Schutz destruktiver Endpunkte** → jetzt Papierkorb + Historien (oben).
2. **Backtest → Verbesserung → erneuter Test** war kein Kreislauf → jetzt Feintuning + Automatik.
3. **Sichtbarkeit rekonstruierter/gelöschter Daten** → `recovered`-Flag, Papierkorb-UI, Recovery-Report.
4. Offen (Empfehlung, nicht umgesetzt): Ghost-Trade-Pfad entweder reparieren oder entfernen; ML-Findings aus
   dem Gedächtnis nehmen; Rollen-Konsolidierung – jeweils erst nach deiner Entscheidung, weil es Workflows ändert.

## 4. Deploy-Hinweise (Render, Struktur unverändert)
- Keine neuen Abhängigkeiten. Neue Dateien: `backend/services/data_recovery.py`, `backend/services/trade_trash.py`,
  `backend/services/setup_backtest/auto.py`, `backend/routers/recovery.py`,
  `frontend/src/components/TradeTrashPanel.js`, Tests `tests/test_data_recovery_and_trash.py`,
  `tests/test_seed_auto_and_tuning.py`.
- Nach dem Deploy: Log-Zeile `Boot-Migration Data-Recovery: …` prüfen oder `GET /api/admin/recovery/report`.
- Danach optional im Backtester → „KI Trader · Setups“ → Automatik einschalten.
