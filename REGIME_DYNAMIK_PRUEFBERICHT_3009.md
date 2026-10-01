# Prüfbericht 30.09.2026 – Regime-Lab ↔ dynamische Strategien (Optimizer, Backtester, Live) + lokaler Worker-Log

Basis: Branch `conflict_300926_1710`. Code-Änderungen nur in der lokalen Preview mit separater Test-DB.
Produktiv-DB wurde **nur lesend** abgefragt (dynamische Strategien, Analyse `ra_e866c510`), Börse nicht berührt.
Vorwissen: `REGIME_LAB_PRUEFBERICHT_2309/2409/2509.md`, `memory/PRD.md`, `analysis_paket/`.

---

## 0. Kurzfassung

Die Kette **Regime-Lab → Regime-Suche/Werkbank → Walk-Forward → Bau → Backtester → Live** war in sich
**nicht deckungsgleich**. Jeder Teil war einzeln sauber gebaut, aber an vier Stellen haben die Teile
**unterschiedliche Regime** bzw. **unterschiedliche Strategien** gesehen. Deshalb passten
In-Sample-Zahlen, Walk-Forward und Live nicht zusammen.

| # | Befund | Schwere | Status |
|---|---|---|---|
| D1 | Live-Regime wurde auf **30 Tagen** Historie bestimmt – das Modell braucht **142 Tage** Warmup | **kritisch** | ✅ behoben |
| D2 | Regime-Suche trainierte auf **rückblickenden** Phasen (Start am Hoch/Tief), live gibt es nur die kausale Sicht | **hoch** | ✅ behoben (Standard = Live-Sicht, umschaltbar) |
| D3 | Walk-Forward handelte **auch Regime ohne Strategie** (live: keine Trades) | mittel | ✅ behoben |
| D4 | Gemischte Zuordnungen (z.B. NNFX + eigene Regeln): Regime liefen live/im Backtest mit der **Basis-Strategie** statt der zugeordneten | mittel | ✅ behoben (neue Builds) |
| D5 | Dynamischer Backtest mit kurzem Zeitraum: Regime ohne Detektor-Warmup bestimmt | mittel | ✅ behoben |
| W1–W3 | Worker-Log: Verbindungs-Flattern, HYPEUSDT-Wiederholungen, Dukascopy-Spam | Log-Rauschen | ✅ reduziert, Verhalten gleich |

---

## 1. Befunde im Detail (mit Messwerten)

### D1 – Live-Regime auf zu kurzer Historie (kritisch)
- `dynamic_runtime.refresh` / `dynamic_live.watch_loop` luden `check_days` (Standard **30**, max. 90) Tage.
- Die produktiv gehandelten dynamischen Strategien (`Trendfolge 9 Regime`, `… optimiert`, `NNFX: 9 Regime Krypto 1h`)
  nutzen ein **kombi-Modell mit 9 Regimen** → `regime_engine.required_history_days` = **142 Tage** (3343 Kerzen Warmup).
- Der KI-Trader (`structural_regime`) hatte diesen „Historien-Vertrag“ bereits – die dynamischen Strategien nicht.
- **Messung** (echte Binance-1h-Kerzen, Produktiv-Modell, 90 Tage, alle 6 h verglichen mit dem Label auf voller Historie
  = das, was Backtest/Walk-Forward annehmen):

| Coin | 30 Tage (bisher) | 142 Tage (neu) | 284 Tage |
|---|---|---|---|
| BTCUSDT | **51 %** abweichend | 0 % | 0 % |
| ETHUSDT | **65 %** abweichend | 8 % | 0 % |

→ Live wurde in über der Hälfte der Zeit **ein anderes Regime** (und damit eine andere Sub-Strategie) gehandelt als getestet.
- **Fix:** `dynamic_live.history_days(model, check_days)` – v2-Modelle laden mindestens den Detektor-Warmup
  (gleiche Funktion wie der KI-Trader, dieselben Kerzen im Cache → kein zusätzlicher RAM). Wirkt für Laufzeit,
  Hintergrund-Prüfung und manuelles „Regime aktualisieren“ (`refresh_state`). `check_days` bleibt Untergrenze.
- Rest-Abweichung ETH 8 % bei exakt 142 Tagen: Pfadabhängigkeit der Hysterese (gleiches Verhalten wie im KI-Trader) – bewusst
  nicht weiter erhöht, um den Render-RAM nicht zu belasten.

### D2 – Regime-Suche auf rückblickenden Phasen (hoch)
- Für reactive/ema/kombi/jump speichert die Analyse zwei Sichten: `segments` (Final/Rückblick: jede Phase beginnt am
  bestätigten Hoch/Tief) und `live_segments` (kausal, so wie live erkannt).
- `regime_opt._build_regime_segments` nutzte **immer** `segments` (`label_basis: retrospective_reference`).
  Live, Backtest und Walk-Forward nutzen dagegen die kausale Sicht.
- **Messung** `ra_e866c510` (13 Coins, kombi 9 Regime): von der Final-Zeit der Trend-Regime sind live nur **79–83 %**
  ebenfalls dieses Regime (Seitwärts: 97 %). Es fehlt genau der **Anfang jedes Trends** – der profitabelste Teil, der live nie
  handelbar ist.
- Folge in Produktion: Zuordnungen In-Sample **+15.700 USDT** (z.B. Abwärtstrend niedrige Vola +4.824) → Walk-Forward
  **−883 USDT** (dieselben Regime −100 / −442 / +10). Ein Teil ist klassisches Overfitting, ein systematischer Teil ist diese
  Trainings-/Handels-Diskrepanz.
- **Fix:** neuer Parameter `label_basis` (`live` = Standard, `final` = bisheriges Verhalten) in Regime-Suche und Werkbank.
  Auswahl „Regime-Abschnitte: Live-Sicht (wie im Handel, empfohlen) / Rückblick (ideale Phasen)“ im Regime-Lab-Suchpanel und
  in der Dynamik-Werkbank; das Ergebnis zeigt die verwendete Basis. Alt-Analysen ohne Live-Sicht fallen automatisch auf die
  gespeicherten Abschnitte zurück (Regressions-Detektor: dort sind sie ohnehin live).

### D3 – Walk-Forward handelte unbelegte Regime
- `run_walkforward` simulierte alle Holdout-Abschnitte; Regime ohne Zuordnung liefen mit der Fallback-Strategie.
  Live (Multi-Modus) und dynamischer Backtest handeln dort **nicht** (Nutzer-Entscheidung „Regime ohne Strategie: keine neuen Trades“).
- **Fix:** Walk-Forward handelt nur bestätigte Regime; Regimewechsel werden weiter vollständig gezählt, neues Feld `untraded_bars`.

### D4 – Gemischte Zuordnungen verloren den Multi-Modus
- `/api/regime-lab/{aid}/build` schrieb `regime_strategies` nur, wenn **alle** Regime eine Registry-Strategie hatten.
  Bei gemischten Zuordnungen (typisch für die Werkbank: Discovery-Regeln + NNFX-Parameter) galt der Einzel-Modus:
  - Regime mit Registry-Strategie liefen live **und** im dynamischen Backtest mit der **Basis-Strategie** (Walk-Forward prüfte die zugeordnete),
  - Regime ohne Zuordnung handelten mit der Basis-Strategie.
- **Fix:** `strategy_plan.regime_strategy_map` – Regime→Strategie immer explizit (Registry direkt, eigene Regeln/Basis-Parameter über
  die Basis-Strategie; die Regel-Definition schaltet deren Regeln um). Reine NNFX-/Registry-Builds sind bitgleich zu vorher.
  Der Legacy-Apply-Pfad (`apply_regime_strategies`) übernimmt dazu jetzt auch die Regel-Umschaltung (wie `apply_configs`).
- Bestehende dynamische Strategien werden **nicht** migriert (produktiv betroffen: keine – die aktiven Dokumente sind reine
  Registry-Zuordnungen). Neu bauen übernimmt den Fix.

### D5 – Dynamischer Backtest ohne Warmup
- `dynamic_backtest.simulate_dynamic` lud genau den Backtest-Zeitraum und bestimmte darauf das Regime → bei 30-Tage-Backtests
  dieselbe Abweichung wie D1. **Fix:** Detektor-Warmup wird vor dem Fenster mitgeladen, gehandelt/gezählt wird nur im Fenster.

### Geprüft und in Ordnung
- Ein Resolver für Live, Backtest und Legacy-Apply (`strategy_plan.resolve_symbol_plan`) – konsistent.
- Walk-Forward nutzt kausale Labels (`classify_series`), Holdout unberührt, Versuchszähler.
- Regimewechsel „schließen“ (Standard) entspricht der Segment-Logik im Backtest/Walk-Forward.
- Geld-/Hebel-Keys bleiben beim Nutzer (`USER_MONEY_KEYS`), Freigabe-Sperre (`strategy_release`) greift live.

### Einordnung der aktuellen Produktiv-Strategien (lesend geprüft)
- `Trendfolge 9 Regime` / `… optimiert` / `NNFX: 9 Regime Krypto 1h`: nur **Paper** auf BTCUSDT, Release „draft“ →
  keine Signale (Freigabe-Sperre). 85 Laufzeit-Regimewechsel protokolliert, 0 Trades – das passt.
- Walk-Forward `ra_e866c510`: −883 USDT, 241 Wechsel, Gebühren 781 → „statische Strategie bevorzugen“ ist korrekt.
- Empfehlung: nach dem Deploy die Regime-Suche/Werkbank mit **Live-Sicht** neu laufen lassen, dann Walk-Forward; Regime mit
  negativem Walk-Forward auf „nicht handeln“ stellen (Empfehlung „skip“ im Ergebnis-Backtest). Erst bei positivem Walk-Forward freigeben.

---

## 2. Lokaler Worker – warum so viele Meldungen?

| Meldung | Ursache | Echtes Problem? | Änderung |
|---|---|---|---|
| „Verbindung fehlgeschlagen … NameResolutionError / getaddrinfo failed“ + „Job …: Server nicht erreichbar“ + „wiederhergestellt“ (3–5 Zeilen je Aussetzer) | DNS-/Internet-Wackler auf deinem PC bzw. Render-Neustart; Poll-Schleife **und** jeder Fortschritts-Thread meldeten getrennt | Nein – Jobs rechnen weiter, Ergebnisse werden nachgeliefert | **Worker 1.16.2**: ein gemeinsamer Verbindungszustand, Meldung erst nach 20 s, kurzer Grund, je Ausfall 1 Zeile + 1 Zeile Wiederverbindung |
| `regime_lab: HYPEUSDT ausgeschlossen – Kerzenanzahl 8644 ≠ Manifest 8759` bei jedem Job | Autopilot startet viele Jobs; die Analyse wurde auf dem Server mit 8759 Kerzen gebaut, im Worker-Cache fehlen 115 Stunden HYPEUSDT, die die Quelle nicht nachliefert. Jeder Job versuchte erneut zu reparieren (Downloads!) und meldete | Teilweise: HYPEUSDT fällt aus den **lokalen** Autopilot-Läufen heraus (Ergebnis ist trotzdem gültig, Symbol wird im Ergebnis als ausgeschlossen ausgewiesen) | Ergebnis gleich (weiter ausgeschlossen), aber Reparatur/Meldung nur noch 1× je 6 h. Tipp: HYPEUSDT im Worker unter Daten löschen und neu laden oder Analyse in der Cloud ausführen |
| `dukascopy XAGUSD 2025-10-01: HTTP 503 (Ratelimit)` + `Download endet bei …` | FX/Metall/Index-Backup-Historie; Dukascopy drosselt (503/Timeout). Download bricht sauber ab, bereits geladene Tage bleiben, Rest beim nächsten Lauf | Nein | Eine zusammengefasste Zeile je Instrument und Tag, höchstens alle 6 h |
| „Auto-Update der Kerzendaten…“ stündlich | deine Einstellung (Auto-Update 60 min) | Nein | unverändert |

Alle Worker-Änderungen betreffen nur das Logging. Poll-Intervall, Backoff, Wiederholungen, Rechenweg und Upload sind unverändert.
`services/*` kommt automatisch mit; für die ruhigere Verbindungs-Meldung das Worker-Paket (1.16.2) neu herunterladen.

---

## 3. Tests
- Neu: `backend/tests/test_regime_dynamic_consistency_3009.py` (18 Unit-Tests: Historien-Vertrag, Segment-Basis,
  Walk-Forward ohne unbelegte Regime, dynamischer Backtest mit Warmup, Multi-Modus-Bau, Log-Drosselung, Worker-Verbindungszustand).
- Angepasst: `analysis_regression/test_ap07_research_validation.py::test_result_payloads_carry_label_basis` (neuer Vertrag).
- Komplette Unit-Suite vorher/nachher: gleiche (alte, fremde) Fehlschläge, keine neuen.
- `CI=true yarn build` grün.

## 4. Was du tun musst
1. Branch pushen → Render deployt Backend + Frontend.
2. Worker-Paket neu herunterladen (1.16.2) – optional, nur für das ruhigere Verbindungs-Log.
3. Dynamische Strategien: Regime-Suche/Werkbank mit „Live-Sicht“ neu laufen lassen → Walk-Forward → neu bauen.
4. Wie immer erst freigeben, wenn der Walk-Forward positiv ist.
