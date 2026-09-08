# KI-Trader Review 07.09.2026 – Aktiv-Filter, KI-Rollen, Keys, MasterPrompt & Lektionen

Grundlage: Branch `conflict_070926_1157`, Prod-Daten (Atlas `crypto_scanner`, **nur lesend**
kopiert nach lokaler Dev-DB), alle API-Keys live geprüft (nur Katalog-/Mini-Aufrufe),
8 Tage `ai_token_usage` (31.08.–07.09.).

---

## 1. Umgesetzt (Code, im Repo-Layout – 1:1 nach Render pushbar)

### 1.1 Paper-Badge + Strategie-Vergleich zählen nur aktive Strategie×Asset-Kombinationen
- Neu `backend/services/active_scope.py` (rein, testbar): aktiv = `strategy_coin_configs`
  `<strategie>_<SYMBOL>` mit Trade-Modus **paper oder live**. Deaktivierte (`off`/keine Config)
  bleiben in der DB, werden aber **nicht mehr eingerechnet**. Manuelle/externe Trades
  (Bitunix-App, Website-Manuell) gehören zu keiner Strategie und bleiben unberührt.
- `GET /api/autotrade/balance`: Paper-Kennzahlen (Header-Badge: PnL, offene/geschlossene
  Trades – im Paper-Modus auch die Hauptwerte) mit Aktiv-Filter; neues Feld `active_only: true`.
  **Live-Zahlen unverändert** (echtes Konto = echtes Konto).
- `GET /api/analytics/strategy-comparison`: neuer Parameter `only_active` (Default **true**),
  Antwort mit `inactive_hidden` (Anzahl ausgeblendeter Trades). `only_active=false` = altes
  Verhalten (rückwärtskompatibel).
- UI `StrategyComparison`: Schalter „nur aktive Assets (N ausgeblendet)“; Header-Paper-Badge
  Tooltip erklärt den Filter.
- Tests: `tests/test_active_scope_and_ai_review.py` (Unit), `tests/test_active_scope_api.py` (E2E).

Hinweis Prod-Daten: Die 39 wiederhergestellten Paper-Trades vom 06.09. tragen
`strategy_id: "manual"` (Recovery hat den *Close*-Auslöser „user“ als „manuell“ gewertet, obwohl
die KI sie eröffnet hat). Sie zählen deshalb als manuelle Trades weiter mit (alle betroffenen
Assets sind ohnehin für `ai_trader` aktiv → Zahl im Badge ändert sich dadurch nicht).
→ Backlog P2: Recovery-Trades nach *Open*-Quelle zuordnen.

### 1.2 KI-Provider / Modelle
- **`openai/gpt-oss-20b:free` und `nvidia/nemotron-nano-9b-v2:free` sind bei OpenRouter tot**
  (live: `404 – This model is unavailable for free. The paid version … openai/gpt-oss-20b`).
  Aus Katalog + Fallback-Kette entfernt; `MODEL_MIGRATIONS` mappt gespeicherte Configs auf das
  **gleiche Modell gratis bei Groq** (`groq/openai/gpt-oss-20b`). In Prod betroffen: Fallback 1 des
  Markt-Beobachters → wird beim Start automatisch migriert und persistiert.
- Rollen-Voreinstellungen (`ai_roles.ROLE_PRESETS`) ohne Cerebras (Free-Tier seit 17.08. weg,
  alle 16 Keys liefern **402**) und mit Kontext-Realität: Analyst/Deep/Research-Prompts haben
  10–20k Tokens, Groq-Free hat ~7k Input-Budget → Groq nur noch letzte Stufe dieser Rollen.
- `AIRoleManager._fill_missing_fallback2`: kritische Rollen (Trade-Manager, News-Wächter,
  Analyst, Lern-Modul) bekommen eine fehlende **Fallback-2-Stufe** aus dem Preset ergänzt –
  Primär/Fallback 1 bleiben unangetastet. In Prod ergänzt: Trade-Manager → `openrouter/
  nemotron-3-super:free`, News-Wächter → `gemini-3.1-flash-lite` (vorher: `null`).

### 1.3 MasterPrompt / Lektions-Grundregeln
- Neue harte Regel `block_coin_ranking_lessons` (Default an, im UI schaltbar): Lektionen wie
  „AVAX, ETH, POL, DOT, DOGE, BNB priorisieren – ADA, SOL, QQQ, SPY meiden“ (aktuell Lektion 1 in
  Prod, conf 5) sind reiner Rückspiegel/Zufall und verfälschen die Datensammlung → werden beim
  nächsten Lektionen-Audit automatisch entfernt. Geprüft wird nur der Titel (≥3 Ticker +
  Rangfolge-Verb), Belege im Detail schlagen nicht an.
- Lektions-Grundregeln (Vorlage) überarbeitet – alter Text war nie vom Trader geändert und wird
  beim Laden automatisch gehoben (eigene Texte bleiben unberührt):
  - Regel 4 widersprach Regel 9 („Hebel dürfen strenger werden“ vs. „keine Hebel-Deckel“) → Hebel
    aus Regel 4 herausgenommen.
  - Regel 6: bestehende Lektion schärfen statt zweite anlegen (Prod hat 6 Fast-Dubletten
    „mindestens zwei unabhängige Bestätigungen“).
  - Neu Regel 10 (Coin-Ranglisten) und Regel 11 (keine Meta-Lektionen wie „Paper-PnL nicht
    überbewerten“ – solche Sätze gehören in die Einschätzung, nicht in den Bestand).
- UI `AIGovernancePanel`: die drei Lektions-Qualitätsregeln sind jetzt sichtbar/schaltbar.

---

## 2. Befunde & Empfehlungen (nicht automatisch geändert – deine Entscheidung)

### 2.1 Keys – Ergebnis Live-Check
| Provider | Keys | Status | Bewertung |
|---|---|---|---|
| OpenRouter (Haupt) | 1 bezahlt + 7 Free-Backups | ok | **Guthaben 30 $, verbraucht 25,93 $ → ~4 $ übrig, Verbrauch ~1,10 $/Tag ⇒ in ca. 4 Tagen leer.** Danach 402 für `deepseek-v4-flash` (Trade-Manager primär!) und `deepseek-v4-pro` (Lern-Modul primär) → Ketten fallen auf Free-Modelle zurück. **→ Guthaben aufladen** (10–20 $ reichen ~2–3 Wochen) oder Auto-Top-up. |
| OpenRouter Copilot | 1 bezahlt + 2 Free | ok | ausreichend |
| Groq | 3 | ok | ausreichend für kleine Prompts (News, Observer, Trade-Manager-Fallback). Für Analyst-Prompts (17k Tokens) ungeeignet (7k Budget) – kein Key-Problem. |
| Gemini | **1** | ok | **Einziger Provider ohne Backup**, aber Fallback in 7 Rollen (Chat sogar primär). → `GEMINI_API_KEY_BACKUP` aus einem zweiten Google-Projekt anlegen. |
| Mistral | 2 | 429 beim Test | News-Wächter alle 5 min (516 Calls/8 Tage) reizt das Free-Limit aus; Fallback 2 jetzt gesetzt. Ein 3. Key (`MISTRAL_API_KEY_BACKUP1`) wäre sinnvoll ODER News-Wächter auf `groq/gpt-oss-20b` (Preset). |
| Cerebras | 16 | **402 alle** | Free-Tier eingestellt. Die 16 Keys bringen nichts mehr; sie werden zwar nur 1×/Tag angefasst, sind aber Rauschen im KI-Status. → Aus der Render-Env entfernen (oder Cerebras bezahlen). |

### 2.2 Weggefallener gratis OpenRouter-GPT – Ersatz
- Ursache: OpenRouter hat die `:free`-Varianten der OpenAI-OSS-Modelle eingestellt (nur noch bezahlt:
  `openai/gpt-oss-20b` 0,03 $/M Input, 0,13 $/M Output – praktisch gratis; `gpt-oss-120b`
  0,037/0,17 $/M).
- **Bester Ersatz ohne Kosten: dasselbe Modell bei Groq** (`openai/gpt-oss-20b`/`-120b`, schon im
  Katalog) – das macht die Migration jetzt automatisch.
- Neue Free-Kandidaten bei OpenRouter (live geprüft, Antwort ok): `minimax/minimax-m3:free`
  (1M Kontext, sehr stark), `thinkingmachines/inkling:free`, `google/gemma-4-26b-a4b-it:free`.
  Sie stehen bereits im Modell-Wächter unter „entdeckt“ → im KI-Team-Panel freigeben, wenn gewünscht
  (nicht automatisch eingebaut, wie von dir gewünscht).

### 2.3 KI-Rollen (Prod-Konfiguration) – passen sie?
| Rolle | Ist | Urteil / Empfehlung |
|---|---|---|
| Analyst | nemotron-3-super:free → groq gpt-oss-120b → gemini-3.5-flash-lite | 1 537 Calls, 26,7M Tokens (17k/Call). nemotron liefert öfter „leere Antwort“ (Upstream). **Fallback 1 Groq wird bei 17k-Prompts übersprungen** („Prompt zu groß“) → real landet die Analyse bei flash-lite (Gewicht 1). → Fallback 1 = `gemini-3.5-flash`, Fallback 2 = `nemotron-3.5-lightning:free`. |
| Tiefen-Analyst | nemotron-ultra:free → deepseek-v4-pro (bezahlt) → gemini-3.6-flash | gut; bezahlter Fallback hängt am Guthaben (2.1). |
| Forschungs-Analyst | groq gpt-oss-120b → nemotron-super → deepseek-v4-flash | nur 31 Calls; Groq-Budget 7k → häufig Skip. → Primär `nemotron-3-super:free`, Groq als letzte Stufe. |
| Trade-Manager | deepseek-v4-flash (bezahlt) → groq gpt-oss-120b → *(neu)* nemotron-super | 719 Calls, 4,3k Tokens/Call – Groq passt hier. **Kritisch: bei leerem Guthaben fällt der Trade-Manager auf Groq zurück** – funktioniert, aber bitte 2.1 beachten. |
| Lern-Modul | deepseek-v4-pro (bezahlt) → nemotron-ultra → gemini-3.1-pro | sehr gut besetzt (Lektionen wirken dauerhaft). |
| Chat | gemini-3.5-flash-lite → gemini-3.1-flash-lite → gemma-4-31b:free | Zwei Gemini-Stufen am einzigen Gemini-Key. → Fallback 1 `groq/gpt-oss-120b` (schnell, stark). |
| News-Wächter | mistral-small → ministral-8b → *(neu)* gemini-3.1-flash-lite | 429 bei Mistral gesehen; ok mit neuer Stufe. Intervall 5 min ist für News reichlich (15 min Preset spart 2/3 der Calls). |
| Markt-Beobachter | groq gpt-oss-20b → *(migriert)* groq gpt-oss-20b → gemini-3.1-flash-lite | LLM-Zusammenfassung aus (seit 06.09.) → keine Calls mehr, passt. |
| Tages-Reporter | mistral-small → ministral-8b → gemini-3.1-flash-lite | 7 Calls, passt. |

### 2.4 KI-Trader-Einstellungen (Prod `ai_trader_config` u.a.) – Widersprüche
1. **Hebel-Deckel widersprechen sich**: MasterPrompt `max_leverage: 50` (blockt `set_leverage` >50
   des Trade-Managers) vs. Trade-Manager `max_leverage: 200`, `profit_lock_max_leverage: 200`,
   `runner_secure_max_leverage: 200`, Risiko-Sizing `risk_max_leverage: 15`. Die Margen-Freisetzung
   (Runner-Secure/„Margin-Trick“) will bewusst hoch hebeln – der 50er MasterPrompt-Deckel bremst
   genau das, während der Einstieg ohnehin bei 15x gedeckelt ist.
   → Entweder MasterPrompt `max_leverage = 0` (kein Deckel; Risiko läuft über Marge/SL – so steht
   es auch in Lektions-Regel 9) **oder** Trade-Manager/Runner-Caps auf 50 senken. Beides
   konsistent, aktuell ist es keins von beidem.
2. **Fee-Wächter ist AUS** (`fee_guard_enabled: false`), obwohl die Lektion „Fees = 35 % der
   Verluste, bei 13 von 74 Verlusten ≥ 50 %“ bestätigt ist. → **AN** lassen, `fee_guard_crv_relax`
   ist bereits an (blockt nur echte Fee-Verlierer).
3. **Korrelations-Guard AUS, `max_same_direction: 8`** – die KI schließt selbst Trades mit Begründung
   „alle drei Trades wetten gegen USD“ (Klumpenrisiko). → `correlation_guard: true`,
   `max_same_direction: 3–4` (MasterPrompt Regel 8 sagt dasselbe).
4. `max_trades_per_coin: 3` → 2 (weniger Stacking desselben Fehlers, sauberere Lern-Labels).
5. `swing_max_leverage: 20` → 8 (Swing lebt von weiten Zielen; 20x + weiter SL = große Marge).
6. Tages-Reißleine fehlt komplett: MasterPrompt `max_daily_loss_usdt: 0`, Trade-Guard
   `max_daily_loss_pct: 50`, `max_consecutive_losses: 10`, Kill-Switch aus. Für Paper egal, **vor
   Live-Skalierung** z.B. `max_daily_loss_usdt` = 3–5 % der Equity setzen.
7. Gut so (nicht anfassen): Heatmap aus / Liquidationsdaten an, Risiko-Sizing 2 %/15 % Marge/15x,
   `smart_skip_move_pct 0.1`, Lernen an (60 Tage, 50 Lektionen), Validierung (15/5/12/8),
   Supervisor-Auto aus, Slippage-Guard an, Low-Vol-Block an, `min_confidence 65` (Tune 55–75).

### 2.5 MasterPrompt-Text (13 Regeln) – Urteil
Inhaltlich stimmig, nichts „dumm“ oder behindernd: Regeln 1–13 sind konsistent mit den harten
Regeln (CRV 1,2 = `crv_min`, Regel 5 „kleinere Position statt mehr Hebel“ = Risiko-Sizing).
Einzige Reibung war die harte Regel `max_leverage 50` (siehe 2.4/1) – das ist eine Einstellung,
kein Textproblem. Regel 9 der Lektions-Policy beschreibt „Auto-Leverage wählt maximalen Hebel“ –
das gilt weiterhin (Risiko-Modus: Liquidation 0,5 % hinter dem SL, gedeckelt durch 15x).

### 2.6 Lektionsbestand (14 Lektionen) – Qualität
- 1 Coin-Rangliste (fällt jetzt automatisch weg), 1 Meta-Lektion („Paper-PnL nicht
  überbewerten“ – neue Regel 11 verhindert Nachschub; bestehende bitte im UI löschen),
  6 Lektionen mit identischem Kern „mindestens zwei unabhängige Bestätigungen“ (Regime
  breakout_ruhig, drift_ruhig, Winrate < 40 %, Konfidenz ≥ 80, Bestätigungskerze, Forex) → per
  Regel 6 künftig konsolidiert; Bestand ggf. von Hand auf 2–3 zusammenführen.
- Stark und behalten: SL ≥ 1,5 ATR (conf 36), Shorts nur im bestätigten Abwärtstrend (conf 43),
  keine identischen Re-Entries, kein Hedge statt Management.

---

## 3. Tests / Regression
- Neu: `test_active_scope_and_ai_review.py` (13 Unit), `test_active_scope_api.py` (4 E2E) – grün.
- Bestehende Unit-Suite (`-m unit`): 1 108 grün; 13 Fehler + 7 Errors sind **identisch zum
  Original-Stand** (Port 8055-Tests, `test_regime_lab`-Credentials-Datei, Round-Robin-Key-Test,
  Registry-Kontamination durch Testreihenfolge) → keine Regression. `test_ai_team_regression`
  auf Preset-unabhängige Prüfung angepasst.
- Dev lief gegen lokale DB `crypto_scanner_dev` (Kopie), Bitunix/IBKR-Keys lokal entfernt,
  KI-Trader lokal aus – Prod wurde zu keinem Zeitpunkt beschrieben.
