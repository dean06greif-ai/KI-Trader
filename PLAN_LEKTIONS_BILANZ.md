# Umsetzungsplan: Lektions-Bilanz (quantitative Wirkungsmessung je Lektion)

Stand: 17.09.2026 · Branch `conflict_170926_1506` · Status: **PLAN, nicht umgesetzt**

## 0. Ziel in einem Satz

Jede Lektion des KI-Traders bekommt eine **messbare Bilanz**: Wie liefen Trades, bei denen sie
angewendet wurde? Welche Trades hat sie verhindert – und was wäre daraus geworden? Daraus entsteht
ein **Vorschlag** (behalten / verschärfen / lockern / weiter sammeln) – **nie ein Automatismus**.

## 1. Grundsätze (aus dem Auftrag)

- Additiv: kein bestehendes Verhalten ändert sich, solange kein Schalter umgelegt wird.
- Fail-open: jede neue Komponente darf ausfallen, ohne Analyse/Trading zu beeinflussen.
- Reine Kernfunktionen auf Modulebene (unit-testbar ohne Netz/DB), DB-Zugriff in dünnen Wrappern –
  wie `trade_postmortem.py`, `policy_fingerprint.py`, `setup_variant.py`.
- Bestehende Gates bleiben die einzige Instanz, die Lektionen ändert (`min_removal_results`,
  `locked`, `reevaluate_lessons`). Die Bilanz **informiert** diese Gates, ersetzt sie nicht.
- Ordnerstruktur unverändert (Render): neue Module flach unter `backend/services/`,
  Endpunkte im bestehenden `routers/ai_governance.py`, UI im bestehenden Lektionen-Bereich
  (`AITradingPanel.js`).

## 2. Ist-Zustand (Anker im Code)

| Baustein | Datei / Funktion | Relevanz für den Plan |
|---|---|---|
| Lektionen im Prompt | `services/ai_lessons.lessons_text` + `prompt_order` (vergibt `no` 1..n, KI spricht von „Lektion 6") | Nummern sind **nicht stabil** (Reihenfolge ändert sich mit Gewicht/locked) → Attribution muss über die stabile `id` (`les_xxx`) laufen |
| Entscheidungs-Schema | `services/ai_engine.py` ~Z. 340–365 (System-Prompt JSON) und ~Z. 1660–1700 (`dec = {...}`) | Hier kommt das Feld `applied_lessons` hinzu |
| Speicherung Entscheidungen | `ai_engine.py` ~Z. 1787 `db.ai_decisions.insert_many(stored)` | HOLD-Entscheidungen liegen bereits vor (Token-Sparmodus: nur symbol/action/confidence/reasoning) |
| Trade ↔ Entscheidung | `ai_engine.py` ~Z. 2331 `decision_id`, `policy_version` im Signal → `bitunix_trade.py` Z. 2333/2704 in `auto_trades` | `applied_lessons` wird auf demselben Weg vererbt |
| What-if-Simulation | `services/trade_postmortem.py`: `simulate_exit`, `excursions`, `r_multiple`, `slice_candles`, `PostmortemService._candles`, `run_pending`, `run_loop` | Wird für die HOLD-Gegenprobe **wiederverwendet**, nicht kopiert |
| Kosten-Modell | `services/paper_execution.py` (Spread/Slippage), `policy_lab.DEFAULT_SETTINGS.fee_pct` | Counterfactual wird NETTO gerechnet |
| Lektionen-Gates | `services/ai_validation.evaluate_lesson` (`min_lesson_results`, `min_removal_results`), `ai_learning._bump_lesson_candidate` | Bilanz-Urteil nutzt dieselben Mindestmengen |
| Lernlauf-Prompt | `ai_learning.run_learning` (Blöcke PERFORMANCE / LETZTE TRADES / BISHERIGE LEKTIONEN / KANDIDATEN / DORMANT) | Neuer Block „LEKTIONS-BILANZ" |
| Neubewertung | `ai_learning.reevaluate_lessons` + `_reeval_apply` (Urteile gueltig/anpassen/veraltet) | Bekommt die Bilanz als Evidenz |
| Endpunkte | `routers/ai_governance.py` (`GET/POST/PATCH/DELETE /api/ai/lessons…`) | Neuer `GET /api/ai/lessons/impact` |
| UI | `frontend/src/components/AITradingPanel.js` (Lektionen-Liste), `AIGovernancePanel.js` (Regeln) | Neue Spalte/Badge je Lektion + Detail-Ausklapper |
| Policy-Fingerprint | `services/policy_fingerprint.lessons_hash` | Bilanz je Lektion ist unabhängig vom Fingerprint (feiner); beide Sichten ergänzen sich |

**Lücke:** Es existiert keine Verbindung Lektion → Entscheidung → Ergebnis. `reasoning` ist Freitext.
HOLDs haben keinen Ergebnis-Wert. Verschärfen/Lockern passiert nur per LLM-Meinung.

## 3. Zielarchitektur

```
Analyse-Prompt  ──► KI liefert je Entscheidung: applied_lessons[ids], bei HOLD zusätzlich
                    would_be_action (LONG|SHORT|null) + would_be_sl_pct/tp1_pct (optional)
        │
        ▼
ai_decisions (+applied_lessons, +would_be)  ──►  auto_trades (+applied_lessons via Signal)
        │                                                 │
        ▼                                                 ▼
lesson_counterfactual.py                        lesson_impact.py (rein)
  HOLD nach Horizont simulieren                   Aggregation je Lektion:
  (Kerzen, SL/TP, Kosten) → ai_lesson_cf            mit/ohne, verhindert, Netto-R, Urteil
        │                                                 │
        └────────────────────►  GET /api/ai/lessons/impact ◄─┘
                                        │
                     ┌──────────────────┼───────────────────┐
                     ▼                  ▼                   ▼
             UI (Lektionen-Liste)   Lernlauf-Prompt     Neubewertung (Reeval)
             Badge + Details        „LEKTIONS-BILANZ"   Evidenz-Block
```

Feature-Flags (in `ai_trader_config`, Defaults konservativ):
- `lesson_attribution_enabled` (Default **true** – reine Datenerfassung, ändert kein Verhalten)
- `lesson_counterfactual_enabled` (Default **true** – Hintergrund-Simulation, fail-open)
- `lesson_impact_in_prompt` (Default **false** – erst nach Sichtung der Zahlen einschalten)

## 4. Datenmodell (additiv)

### 4.1 `ai_decisions` (bestehende Collection, neue optionale Felder)
```json
{
  "applied_lessons": ["les_3f9a1c2b7d", "les_0a8b…"],   // stabile Lektions-IDs, max. 6
  "would_be": {                                           // NUR bei HOLD, optional
    "action": "LONG", "sl_pct": 0.6, "tp1_pct": 0.9,
    "blocked_by": ["les_3f9a1c2b7d"]                     // Teilmenge von applied_lessons
  },
  "lessons_hash": "…"                                     // bereits in policy_version.lessons_hash
}
```

### 4.2 `auto_trades` (bestehend, neues optionales Feld)
`applied_lessons: [...]` – vererbt über das Signal (`decision_id` ist bereits da → Backfill möglich).

### 4.3 `ai_lesson_cf` (neu, Gegenproben)
```json
{
  "decision_id": "…", "symbol": "BTCUSDT", "ts": "…", "horizon": "scalp",
  "action": "LONG", "entry": 76250.0, "sl": 75790.0, "tp1": 76936.0,
  "exit_reason": "sl|tp|open|no_data", "r": -1.0, "pnl_pct_net": -0.72,
  "mfe_r": 0.4, "mae_r": -1.1, "lookahead_min": 240,
  "blocked_by": ["les_…"], "applied_lessons": ["les_…"],
  "policy_version": {...}, "status": "done|no_data"
}
```
TTL/Retention: 60 Tage (Eintrag in `services/retention.py`, analog Postmortem).

### 4.4 `settings/ai_lesson_impact_cache` (neu, optional)
Gecachte Aggregation (Stale-while-revalidate, 10 min) – die Bilanz iteriert über bis zu 3000 Trades
+ Gegenproben; Cache verhindert Atlas-Last bei UI-Polling.

## 5. Bausteine

### Baustein A – Attribution (Datenerfassung, 0 Verhaltensänderung)

**A1 Prompt (ai_engine.py, System-Prompt beide Varianten ~Z. 346 und ~Z. 450):**
- Schema-Erweiterung im `decisions`-Objekt:
  `"applied_lessons": ["les_id", …]` (IDs der Lektionen, die diese Entscheidung *maßgeblich*
  beeinflusst haben; leer erlaubt) und bei HOLD optional
  `"would_be": {"action": "LONG|SHORT", "sl_pct": …, "tp1_pct": …, "blocked_by": ["les_id"]}`
  („Welche Richtung hättest du OHNE die genannten Lektionen gehandelt?").
- Token-Sparregel bleibt: bei HOLD nur `would_be`, wenn tatsächlich eine Lektion blockiert hat.
- `ai_lessons.lessons_text`: Zeile um die ID ergänzen – `"{no}. [id:les_3f9a] …"` (kurze 4–6-Zeichen-
  Form reicht, Mapping kurz→voll in `ai_lessons.short_id_map(lessons)`).

**A2 Parsing/Validierung (ai_engine.py `dec = {...}` ~Z. 1660):**
- `applied_lessons`: nur IDs, die im aktuellen aktiven Lektionenbestand existieren (Kurz-ID auflösen),
  max. 6, sonst verwerfen. `would_be` nur bei HOLD, `action` ∈ {LONG, SHORT}, Prozentwerte durch
  `setup_asset_class.clamp_levels` wie bei echten Entscheidungen.
- Neue reine Funktion `services/lesson_attribution.py::parse_applied(dec_raw, lesson_ids)` und
  `parse_would_be(dec_raw, asset_class, swing)` – testbar.

**A3 Vererbung an Trades:**
- Signal-Dict ~Z. 2331 um `"applied_lessons": dec.get("applied_lessons")` erweitern;
  `bitunix_trade.py` an beiden Stellen (Z. 2333 Market, Z. 2704 Limit-Fill) übernehmen wie `decision_id`.
- Boot-Migration (`services/boot_migrations.py`, Marker `lesson_attribution_backfill_v1`): für
  bestehende `auto_trades` mit `decision_id` das Feld aus `ai_decisions` nachziehen (nur, wo vorhanden).

**A4 Tests (`backend/tests/test_lesson_attribution.py`):**
- Kurz-ID ↔ Voll-ID-Mapping, unbekannte IDs werden verworfen, max. 6.
- `would_be` nur bei HOLD, Clamp greift, fehlende Felder → `None`.
- Regression: Entscheidungen ohne die neuen Felder werden exakt wie heute gespeichert
  (Snapshot-Vergleich des `dec`-Dicts ohne neue Keys).
- Prompt-Snapshot: `lessons_text` enthält weiterhin alle bisherigen Bestandteile (Marker, Kontext, gültig bis).

**Aufwand:** ~0,5 Tag. **Risiko:** gering – LLM könnte Feld ignorieren → dann einfach leere Attribution.

### Baustein B – HOLD-Gegenprobe (Counterfactual)

**B1 Neues Modul `services/lesson_counterfactual.py`** (Struktur wie `trade_postmortem.py`):
- Konstanten: `HORIZON_MIN = {"scalp": 240, "swing": 1440}`, `BATCH = 12`, `LOOKBACK_DAYS = 30`,
  `MIN_AGE_MIN` = Horizont (erst simulieren, wenn der Horizont abgelaufen ist).
- Reine Funktionen:
  - `frame_from_decision(dec) -> Optional[Dict]`: Entry = `dec.price`, SL/TP aus `would_be.sl_pct/tp1_pct`
    (Fallback: Engine-Defaults `sl_pct 0.6 / tp1_pct 0.9` wie bei echten Entscheidungen), Seite aus
    `would_be.action`. Ohne `would_be` → `None` (keine Spekulation über die Richtung).
  - `review_hold(dec, candles) -> Dict`: nutzt `trade_postmortem.simulate_exit`, `excursions`,
    `r_multiple`; Netto-PnL über `paper_execution`-Kosten + `fee_pct` (2 Seiten).
  - `aggregate(reviews) -> Dict[lesson_id, {n, wins, losses, open, sum_r, avoided_loss_r, missed_gain_r}]`
    („verhindert, was Verlierer geworden wäre" = positiv für die Lektion; „verhindert, was Gewinner
    geworden wäre" = negativ).
- Service-Klasse `CounterfactualService` mit `setup(db)`, `_pending(limit)` (HOLD-Entscheidungen mit
  `would_be`, Alter ≥ Horizont, ohne Eintrag in `ai_lesson_cf`), `run_pending()`, `run_loop()`
  (alle 15 min, wie Postmortem). Kerzen über `PostmortemService._candles`-Logik
  (candle_cache/fetch_history) – gemeinsame Hilfsfunktion nach `services/candles.py` ziehen, falls noch
  nicht vorhanden, damit kein Code dupliziert wird.
- Registrierung in `server.py` neben `postmortem.run_loop()`; Guard `local_engine_disabled()` NICHT
  nötig (nur Lesen + eigene Collection), aber `AI_TRADER_LOCAL_DISABLE` respektieren, um in Previews
  Atlas-Last zu sparen.

**B2 Retention:** `services/retention.py` – `ai_lesson_cf` älter 60 Tage löschen.

**B3 Tests (`backend/tests/test_lesson_counterfactual.py`):**
- Synthetische Kerzen: SL zuerst / TP zuerst / beides in einer Kerze (konservativ SL) / kein Treffer.
- Kosten: Netto-R < Brutto-R, Vorzeichen korrekt für Short.
- Aggregation: eine Lektion, die zwei Verlierer verhinderte und einen Gewinner → `sum_r` positiv,
  `avoided_loss_r`/`missed_gain_r` getrennt.
- `frame_from_decision` ohne `would_be` → `None`; HOLD ohne `blocked_by` wird keiner Lektion zugerechnet.
- FakeDB-Test `_pending`: nur HOLD, nur mit `would_be`, nur alt genug, nicht doppelt.

**Aufwand:** ~1 Tag. **Risiko:** Kerzenlücken (Wochenende Nicht-Krypto) → `no_data`-Status wie im
Postmortem; Marktkalender via `core/market_hours.py` respektieren.

### Baustein C – Bilanz, API, Prompt-Block, UI

**C1 Neues Modul `services/lesson_impact.py`** (rein):
- `impact_rows(lessons, trades, cf_reviews, settings) -> List[Dict]` je Lektion:
  ```
  { id, title, locked, weight,
    with: {n, wr, avg_r, sum_r},          # Trades mit dieser Lektion
    without: {n, wr, avg_r},              # Vergleichsgruppe: Trades derselben Klasse/Zeitraum ohne sie
    prevented: {n, would_win, would_loss, open, avoided_loss_r, missed_gain_r, net_r},
    net_contribution_r,                   # = (with.sum_r - n_with * without.avg_r) + prevented.net_r
    sample: n_with + prevented.n,
    verdict: "edge" | "neutral" | "hinderlich" | "zu_weich" | "zu_wenig_daten",
    suggestion: "behalten" | "lockern" | "verschaerfen" | "weiter_sammeln",
    reason: "…" }
  ```
- Urteilsregeln (Konstanten oben im Modul, über `ai_validation`-Settings überschreibbar):
  - `sample < min_removal_results (12)` → `zu_wenig_daten` / `weiter_sammeln`
  - `net_contribution_r >= +0.5 R` und `prevented.missed_gain_r <= avoided_loss_r` → `edge` / `behalten`
  - `prevented.net_r < -1.0 R` und `prevented.n >= 6` → `hinderlich` / `lockern`
    (Lektion verhindert überwiegend Gewinner)
  - `with.n >= 8` und `with.wr < without.wr - 10 pp` → `zu_weich` / `verschaerfen`
    (Lektion wird angewendet, Trades verlieren trotzdem)
  - sonst `neutral` / `behalten`
- Kontrollgruppe „ohne": gleiche Assetklasse (`setup_asset_class.asset_class_of`), gleicher Zeitraum
  (`first_ts`–`last_ts` der Lektion ±), `data_collection` getrennt ausweisen (Sammel-Trades sind mit
  Absicht schlechter gefiltert → nicht mischen).
- Keine Aussage bei `n < 5` in einer Gruppe (Anzeige „—").

**C2 Endpunkt `GET /api/ai/lessons/impact?days=90`** (`routers/ai_governance.py`):
- Lädt aktive + dormant Lektionen (`lesson_store.all()`), Trades (`auto_trades`, `strategy_id=ai_trader`,
  `status=closed`, Projektion wie `policy_report`), Gegenproben (`ai_lesson_cf`), ruft `impact_rows`.
- Cache 10 min (Stale-while-revalidate, Muster `ai_playbook.status`).
- Antwort: `{days, rows, totals: {attributed_trades, unattributed_trades, cf_done, cf_pending}}` –
  `unattributed_trades` zeigt, wie gut die KI das Feld befüllt (Qualitätsmaß für Baustein A).

**C3 Prompt-Block (Flag `lesson_impact_in_prompt`):**
- `lesson_impact.prompt_block(rows, max_lines=8)` → Text
  `=== LEKTIONS-BILANZ (gemessen, netto) ===` mit je Lektion einer Zeile
  `„Titel" (id): 14 Trades mit / WR 57 % vs. 49 % ohne · 9 verhindert (6 wären Verlierer) · Beitrag +2,3 R → EDGE`
  und einer Schlusszeile: „Vorschläge nur umsetzen, wenn die Datenlage (Gate) es erlaubt; LOCKED-Lektionen
  nie ändern."
- Einbau in `ai_learning.run_learning` (nach „BISHERIGE LEKTIONEN") und in `reevaluate_lessons`
  (als Evidenz je `id`). Die LLM-Urteile laufen weiter durch `_reeval_apply` und die Gates – kein
  neuer Schreibpfad.

**C4 UI (`AITradingPanel.js`, Lektionen-Liste):**
- Badge je Lektion: `EDGE +2,3 R` (grün) / `neutral` (grau) / `hinderlich −1,4 R` (orange) /
  `zu weich` (orange) / `n=3 · sammelt` (gedimmt). Tooltip = `reason`.
- Ausklapper „Bilanz": Tabelle mit/ohne/verhindert (Zahlen aus C2), Liste der letzten 5 verhinderten
  Entscheidungen (Symbol, Zeit, wäre LONG/SHORT, Ergebnis R) mit Link auf Chart (`onShowChart`).
- Vorschlags-Knopf (Admin): „Vorschlag an Lernlauf übergeben" → ruft bestehendes
  `POST /api/ai/lessons/reevaluate` mit `reason="Lektions-Bilanz: <id> lockern"`. Kein Direkt-Edit.
- Kopf der Liste: „Attribution: 78 % der Trades tragen Lektions-IDs · 41 Gegenproben, 6 offen".
- `data-testid`: `lesson-impact-badge-<id>`, `lesson-impact-toggle-<id>`, `lesson-impact-table-<id>`,
  `lesson-impact-summary`.

**C5 Tests:**
- `backend/tests/test_lesson_impact.py`: Urteilsregeln an Grenzwerten (11 vs. 12 Samples, −0,99 vs. −1,0 R),
  Kontrollgruppe je Klasse, Sammel-Trades getrennt, `prompt_block` max. 8 Zeilen, LOCKED-Hinweis vorhanden.
- API-Test (FakeDB-Muster wie `test_iter_e2_maintenance_adaptive_api.py`): Endpoint liefert Schema,
  Cache greift (zweiter Aufruf ohne DB-Query).
- Regression Lernlauf: Prompt ohne Flag identisch zu heute (Snapshot-Test auf Blockliste).
- Frontend (Testing-Agent): Badge/Ausklapper sichtbar, Zahlen konsistent mit API.

**Aufwand:** ~1 Tag Backend + 0,5 Tag UI/Tests.

## 6. Reihenfolge, Rollout, Abnahme

| Phase | Inhalt | Abnahme |
|---|---|---|
| 1 | Baustein A deployen, 3–5 Tage laufen lassen | `unattributed_trades`-Quote < 40 %; sonst Prompt-Formulierung nachschärfen (Kurz-IDs prominenter) |
| 2 | Baustein B aktivieren | `ai_lesson_cf` füllt sich; Stichprobe 5 Gegenproben manuell am Chart geprüft |
| 3 | Baustein C: API + UI | Bilanz sichtbar; mindestens eine Lektion mit `sample ≥ 12` |
| 4 | Flag `lesson_impact_in_prompt` einschalten | Lernlauf-Log zeigt Block; kein Anstieg der `removed_lessons` ohne Gate-Freigabe |
| 5 | Nach 2–4 Wochen: Schwellen anhand realer Verteilung kalibrieren | Dokumentiert in `KI_TRADER_AUDIT.md` |

Rollback je Phase: Flag aus bzw. Modul aus `server.py` nehmen – keine Migration rückgängig nötig
(nur zusätzliche Felder/Collection).

## 7. Risiken & Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| KI attribuiert falsch/gar nicht | Quote sichtbar (C2 `unattributed_trades`); Kurz-IDs im Prompt; nur maßgebliche Lektionen |
| Counterfactual überschätzt (kein Slippage-Stress) | Netto-Kosten + konservative SL-zuerst-Regel; als „Schätzung" gekennzeichnet |
| Selektionsverzerrung „ohne"-Gruppe | Kontrollgruppe nur gleiche Klasse/Zeitraum; ab n < 5 keine Aussage |
| Overfitting an kurzen Stichproben | Urteil erst ab `min_removal_results`; kein Automatismus; LOCKED unantastbar |
| Token-Kosten | `applied_lessons` kurze IDs; `would_be` nur bei blockiertem HOLD; Prompt-Block max. 8 Zeilen |
| Atlas-Speicher (512 MB) | `ai_lesson_cf` mit 60-Tage-Retention; Cache statt Live-Aggregation |
| Doppelbetrieb Preview/Render | Loop respektiert `AI_TRADER_LOCAL_DISABLE`; nur eigene Collection |

## 8. Nicht Teil dieses Plans (bewusst)

- Automatisches Löschen/Verschärfen von Lektionen nach Bilanz.
- Änderung der Lektions-Gates oder der Dormant-Logik.
- Bewertung von Lektionen anderer Strategien (nur `ai_trader`).

## 9. Gesamtaufwand

A 0,5 Tag · B 1 Tag · C 1,5 Tage · Kalibrierung/Abnahme 0,5 Tag → **~3,5 Tage**, in drei getrennt
deploybaren Schritten.
