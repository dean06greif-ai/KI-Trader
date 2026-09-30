# Umsetzungsplan: Regime-Brücke Regime-Lab ↔ KI-Trader

Stand: 17.09.2026 (Rev. 2: zweistufige, nachweisgebundene Freigabe + KI-Vorschlag/Auto mit Karenz) · Branch `conflict_170926_1506` · Status: **PLAN, nicht umgesetzt**

## 0. Ziel in einem Satz

Regime-Lab und KI-Trader bleiben **zwei getrennte Regime-Ebenen** (strukturell vs. Kurzfrist) – aber
der KI-Trader **sieht** künftig das freigegebene strukturelle Lab-Regime, jeder Trade **trägt** es im
Fingerprint, und das Regime-Gate kann **optional** dasselbe freigegebene Modell nutzen statt eigener
Erkennung. Kontrollierte Brücke, kein Verschmelzen.

## 1. Grundsätze

- Die Taxonomie aus `services/market_context.py` (Ebenen `structural` / `setup_context` /
  `risk_overlay`, Richtungs-IDs statt Label-Substrings, `unknown`/`stale` als echte Zustände) ist
  der Vertrag. Nichts wird stillschweigend gleichgesetzt.
- **Nur freigegebene** Lab-Analysen wirken auf Runtime (explizites Approval durch den Trader). Forschung
  bleibt Forschung.
- Additiv + fail-open: fehlt die Freigabe oder sind Daten `stale`, verhält sich alles wie heute.
- Bestehende Lektionen bleiben eindeutig: Kurzfrist-Regime heißen weiter `trend_up`/`range_ruhig`…,
  strukturelle Regime bekommen im Prompt einen **eigenen, unverwechselbaren Namen** („Struktur: …").
- Ordnerstruktur unverändert; neue Logik flach in `backend/services/`, Endpunkte in
  `routers/regime_lab.py` bzw. `routers/ai.py`.

## 2. Ist-Zustand (Anker im Code)

| Baustein | Datei / Funktion | Befund |
|---|---|---|
| Kurzfrist-Regime des Traders | `ai_market_observer.classify_regime_v2`, `entry_snapshot(sym).features.regime` | 1m-Basis; im Prompt je Symbol-Zeile; in `entry_market_snapshot` jedes Trades |
| Regime-Sperrfilter Trader | `ai_engine.config.regime_block_enabled/regime_block_list` (~Z. 194, 2156) | arbeitet NUR auf Kurzfrist-Regime; Shadow-Zähler in `routers/ai.py /api/ai/guard-stats` |
| Strukturelles Regime (Lab) | `services/regime_lab.py` (`run_analysis`, Collection `regime_analyses`, `kept`, `assignments`, `model_for`, `scope_key`), `regime_engine.py` v2, `regime.py` (`detect_regimes`, `current_regime`, `is_v2`) | Analysen werden gespeichert, Regime „behalten/verworfen" (`/keep`), Strategien zugewiesen (`/assign`, `/build`) → dynamische Strategien |
| Regime-Gate Auto-Trade | `services/regime_gate.py` (`_detect`: eigenes 1h/30d/3-Regime-Modell, Cache 15 min, fail-open), `entry_guard.py` Z. 64, `bitunix_trade.py` Z. 995 | Liest **keine** Lab-Analyse; eigene „zweite Wahrheit" |
| MarketContext-Vertrag | `market_context.structural_context(source, direction, label, confidence, model_fp, age_sec, ttl_sec)`, `observer_context`, `direction_from_regime_id`, `model_fingerprint` | Vertrag existiert bereits, wird vom Gate genutzt – ideale Basis |
| Fingerprint | `policy_fingerprint.build(..., regime_artifact=None)` Schema 2; Aufruf `ai_engine.py` ~Z. 1693 übergibt **kein** `regime_artifact` | Feld vorbereitet, ungenutzt |
| Forschungs-Digest | `ai_research.digest_regime_runs/digest_regime_analyses` → `research_analyst.context_text()` → Prompt | Lab wirkt heute nur als Freitext-Wissen |
| Rewards je Regime | `ai_rewards._regime_for`, `by_regime` | nur Kurzfrist-Regime |
| Prompt-Aufbau | `ai_engine.py` Gruppen-Prompt ~Z. 1618 (`MARKTDATEN … FOKUS-GRUPPE`), `resolve_group_blocks`; Makro-Block `ai_engine_context.py` ~Z. 383 (`MARKT-REGIME: BTC-Dominanz…`) | Einbau-Punkt für eine Struktur-Zeile je Symbol/Klasse |

## 3. Zielarchitektur

```
Regime-Lab (Forschung)                            Runtime (KI-Trader / Auto-Trade)
──────────────────────                            ────────────────────────────────
regime_analyses[aid]                              structural_regime.py (NEU, Cache + Frische-Regel)
  kept / assignments                                 ├─ resolve(symbol) -> MarketContext(structural)
  calibrate / ablation  ──► validate_release ──►     │    Quelle: Analyse der Klasse mit Stufe shadow|active
  release: {stage: none|shadow|active,               │    Zustand: known | unknown | stale (Marktkalender)
            evidence{hash}, history[]}               ├─ prompt_line(symbol)  (nur Stufe active)
        ▲                     ▲                      ├─ artifact(symbol)     (ab Stufe shadow)
        │ Trader (Knopf)      │ KI-Trader            └─ phase(symbol)        (Gate-Quelle lab, nur active)
        │                     │ (Proposal-Scope             │
        │                     │  regime_release:            │
        │                     │  suggest -> du klickst,     │
        │                     │  auto -> 24 h Karenz)       │
            ┌───────────────────────────────────────────────┼──────────────────────────┐
            ▼                                               ▼                          ▼
   ai_engine Prompt (Struktur-Block,           policy_fingerprint.build(          regime_gate.check_signal_allowed
   klar getrennt vom Kurzfrist-Regime)           regime_artifact=…)               (Quelle: own | lab)
            │                                               │
            ▼                                               ▼
   entry_market_snapshot.structural             Policy-Bericht: "Lab-Modell A vs. B",
   auto_trades.structural_regime                Rewards by_structural_regime
                                                            │
                                                            ▼
                                       Forschungs-Analyst: regime_release_recommendation
                                       (activate | keep_shadow | revoke) ──► Proposal
```

### Freigabe-Stufen (statt eines einzelnen Schalters)

Eine Lab-Analyse durchläuft je Assetklasse **drei Stufen** – jede Stufe ist im `release`-Dokument
festgehalten, auditiert und jederzeit rückholbar:

| Stufe | Bedeutung | Wirkung auf den Trader | Wer schaltet |
|---|---|---|---|
| `none` | normale Lab-Analyse | keine | – |
| `shadow` („Beobachten") | Struktur-Regime wird je Symbol berechnet, in `entry_market_snapshot.structural` gespeichert, im Fingerprint mitgeschrieben, in Rewards ausgewertet | **keine** Wirkung auf Prompt oder Gate | Trader (Knopf, nur wenn Nachweis-Gate grün) |
| `active` („Wirksam") | zusätzlich Prompt-Block + optional Gate-Quelle `lab` | Prompt sieht Struktur; Gate blockt ggf. | Trader **oder** KI-Trader-Vorschlag (siehe 5.1.4) |

Nachweis-Gate (`validate_release`, rein/testbar) muss für `shadow` grün sein; für `active`
zusätzlich das Stichproben-Gate (≥ 30 Trades im Shadow, Rewards je Struktur-Regime unterscheiden sich
signifikant – sonst trägt die Ebene keine Information und bleibt Shadow).

Feature-Flags (`ai_trader_config` bzw. Auto-Trade-Config; Defaults = heutiges Verhalten):
- `structural_regime_stage` je Assetklasse (abgeleitet aus dem `release`; Default **none**)
- `regime_gate_source` je Strategie: `"own"` (heute) | `"lab"` (Default **"own"**; `lab` nur bei Stufe `active`)
- `structural_regime_autonomy`: `"off"` | `"suggest"` | `"auto"` (Default **"suggest"**) – darf der KI-Trader
  den Wechsel `shadow → active` selbst vorschlagen bzw. vollziehen? (eigener Schalter, bewusst getrennt
  von der allgemeinen `autonomy`, weil es eine Struktur-Entscheidung ist)
- **Kein** manuelles TTL-Feld: Frische-Regel liegt im Resolver (Timeframe + Marktkalender, s. 5.2.1)

## 4. Datenmodell (additiv)

### 4.1 `regime_analyses[aid].release` (neu, optional)
```json
{
  "stage": "none" | "shadow" | "active",
  "scope": "combined" | "per_coin", "symbols": ["BTCUSDT", "ETHUSDT"],
  "asset_classes": ["crypto"],
  "model_fingerprint": "a1b2c3…",          // market_context.model_fingerprint(model)
  "evidence": {                             // Nachweis-Gate zum Zeitpunkt der Freigabe (unveränderlich)
    "calibration_job_id": "…", "ablation_job_id": "…",
    "ablation_delta_pct": 1.4,              // Regime-Umschaltung vs. statisch im Holdout
    "kept_regimes": 3, "min_segments_per_regime": 7,
    "evidence_hash": "sha256[:12] über alle Nachweis-Felder"
  },
  "history": [                              // jede Stufenänderung, nie überschrieben
    {"stage": "shadow", "at": "…", "by": "Admin", "reason": "manuell"},
    {"stage": "active", "at": "…", "by": "ki_trader", "proposal_id": "…",
     "reason": "Rewards: Bär Ø −0,42 R (n=38) vs. Bulle Ø +0,31 R (n=41) – Ebene trägt Information"}
  ]
}
```
Nur **eine** Analyse je Assetklasse darf Stufe `shadow` oder `active` haben (beim Hochstufen wird die
vorherige auf `none` gesetzt und in deren `history` protokolliert). Widerruf = Stufe `none`, sofort wirksam.

### 4.2 `ai_decisions` / `auto_trades` (neue optionale Felder)
- `entry_market_snapshot.structural`: der `MarketContext`-Eintrag (`structural_context(...)`) zum
  Entscheidungszeitpunkt (direction, label, confidence, state, model_fingerprint, source).
- `policy_version.regime_artifact`: `"lab:<aid>:<fp8>"` bzw. `"gate:auto"` (nur bei Flag).

### 4.3 `settings/structural_regime_cache` (optional)
Persistierter Cache je Symbol (label, direction, since, confidence, refreshed_at) – überlebt Render-
Neustarts, spart Kerzen-Downloads beim Boot.

## 5. Bausteine

### Baustein 1 – Freigabe im Regime-Lab (Research → Release, zweistufig, nachweisgebunden)

**1.1 Nachweis-Gate (rein, `services/regime_lab.py::validate_release(doc, stage, body, jobs) -> (ok, reasons, evidence)`):**
- Modell ist v2 (`rg.is_v2`), Scope-Modell vorhanden (`model_for`), ≥ 1 `kept`-Regime.
- Kalibrierung gelaufen (`regime_lab_runs`-Eintrag `kind=calibrate` zu dieser Analyse) **und**
  Ablation gelaufen mit `ablation_delta_pct >= 0` (Regime-Umschaltung verliert im Holdout nicht gegen
  die statische Variante).
- Mindestens `MIN_SEGMENTS_PER_REGIME` (Konstante, Start 5) unabhängige Abschnitte je behaltenem Regime.
- `symbols` ⊆ Analyse-Symbole; Assetklasse konsistent (`setup_asset_class`).
- Ergebnis: Liste **fehlender** Nachweise in Klartext (für den Tooltip) + `evidence`-Dokument mit Hash.
- Für Stufe `active` zusätzlich `validate_activation(rewards_by_structural, min_trades=30)`: Stichprobe
  je Struktur-Regime ≥ `min_trades` und Unterschied der Ø-Rewards zwischen bestem/schlechtestem Regime
  ≥ 0,25 R (sonst „Ebene trägt keine Information – bleibt Shadow").

**1.2 Endpunkte (`routers/regime_lab.py`, Admin):**
- `POST /api/regime-lab/{aid}/release` Body `{stage: "shadow"|"active"|"none", scope, symbol?, asset_classes, reason}`
  → ruft `validate_release`/`validate_activation`; bei Fehlschlag **400 mit allen Gründen** (kein
  Teil-Erfolg). Schreibt `release.stage`, `evidence`, `history[]`; setzt Konkurrenz-Analyse der Klasse auf `none`.
- `GET /api/regime-lab/releases` – aktive Stufen je Klasse inkl. Nachweis + Stichprobenstand
  (`shadow_trades`, `rewards_by_structural`), damit die UI zeigt, ob `active` schon möglich ist.
- `GET /api/regime-lab/{aid}/release-check?stage=` – Vorab-Prüfung für den Knopf (grau/aktiv + Tooltip).
- Audit über `core/audit.log_action` (Aktion `regime_release`).

**1.3 Frontend (`RegimeLab.js`):**
- An jeder gespeicherten Analyse zwei Knöpfe: **„Beobachten (Shadow)"** und **„Wirksam schalten"**.
  Grau, solange `release-check` Gründe liefert; Tooltip = fehlende Nachweise („Ablation fehlt",
  „Regime 2 nur 3 Abschnitte", „Shadow erst 12/30 Trades"). Kein Freitext-TTL, kein Notizfeld –
  nur `reason` (Pflicht, kurz) für die History.
- Badge in der Analysen-Liste: `SHADOW · Krypto · seit 14.09. · 18/30 Trades` bzw. `WIRKSAM · Krypto`.
  Klick öffnet die `history` (wer/wann/warum) + Widerruf-Knopf.
- Zusätzlich im KI-Trader-Panel (`AITradingPanel.js`, Bereich Regime-Sperrfilter): Anzeige der aktiven
  Stufe je Klasse und der KI-Empfehlung (1.4), damit du nicht ins Lab wechseln musst.
- `data-testid`: `regime-release-shadow-<aid>`, `regime-release-active-<aid>`, `regime-release-badge-<aid>`,
  `regime-release-history-<aid>`, `regime-release-revoke-<aid>`, `ai-structural-stage-<cls>`.

**1.4 KI-Trader übernimmt selbst (Vorschlag oder Auto), Trader kann immer freischalten/zurücknehmen:**
- Wiederverwendung des **bestehenden Proposal-Mechanismus** (`ai_engine_governance._insert_proposal`,
  `decide_proposal`, `ai_proposals`, UI „Vorschläge"): neuer Scope `"regime_release"`, `changes =
  {"structural_regime_stage": "active", "asset_class": "crypto", "aid": "…"}`.
- Auslöser: der **Forschungs-Analyst** (`ai_research`, bekommt `rewards.by_structural_regime` + Shadow-
  Stichprobe als Kontext) darf im Bericht `regime_release_recommendation: {asset_class, aid, verdict:
  "activate"|"keep_shadow"|"revoke", confidence, reason}` zurückgeben. Nur `verdict=activate` mit
  `confidence ≥ 70` erzeugt einen Vorschlag – und **nur, wenn `validate_activation` grün ist**
  (die KI kann das Gate nicht umgehen; ein Vorschlag ohne Nachweis wird als `needs_data` abgelegt,
  sichtbar, aber nicht anklickbar).
- Verhalten nach `structural_regime_autonomy`:
  - `off`: keine Vorschläge; nur manuell.
  - `suggest` (Default): Vorschlag `pending` → du klickst „Übernehmen"/„Ablehnen" im Vorschläge-Panel
    oder im Lab; Telegram-Hinweis über bestehenden `telegram_notify_config`-Kanal für Vorschläge.
  - `auto`: Vorschlag wird `auto_applied` (Stufe `active`, `history.by = "ki_trader"`), aber
    **mit 24 h Karenz**: Eintrag `pending_auto_until`; Klick „Stopp" in der Karenz verhindert die
    Umschaltung; danach vollzogen. Telegram-Meldung bei Start und Vollzug. Widerruf bleibt jederzeit möglich.
- Umgekehrt (`revoke`): Empfiehlt die KI mit hoher Konfidenz den Widerruf (z. B. Struktur-Regime
  verschlechtert Ergebnisse nach ≥ 30 Trades), gilt derselbe Weg – in `auto` **sofort** zurück auf
  `shadow` (Sicherheitsrichtung braucht keine Karenz), Meldung an dich.
- Gedächtnis: jede Entscheidung (auch Ablehnung) landet über `ai_memory` im KI-Wissen, damit die KI nicht
  in jedem Bericht dasselbe erneut vorschlägt (Cooldown 7 Tage je Klasse, Muster `_insert_proposal`-Aufräumer).

**1.5 Tests (`backend/tests/test_regime_release.py`):**
- `validate_release`: jeder fehlende Nachweis erzeugt genau einen Klartext-Grund; alle grün → Evidence-Hash stabil.
- `validate_activation`: 29 vs. 30 Trades, Δ 0,24 vs. 0,25 R; Klasse ohne Shadow → nicht aktivierbar.
- Eindeutigkeit je Klasse; `history` wächst, wird nie gekürzt; Widerruf setzt `none` sofort.
- Proposal-Pfad: `suggest` erzeugt `pending`; `auto` erzeugt `auto_applied` mit Karenz; „Stopp" in der
  Karenz → `rejected`; KI-Vorschlag ohne grünes Gate → `needs_data`; Cooldown verhindert Duplikate.
- API-Wiring (`/release`, `/releases`, `/release-check`), `require_admin` an allen Schreib-Endpunkten.

### Baustein 2 – Struktur-Resolver (eine Wahrheit für Runtime)

**2.1 Neues Modul `services/structural_regime.py`:**
- `resolve(symbol) -> Dict` (MarketContext-Eintrag der Ebene `structural`; Quelle ergibt sich aus der
  Freigabe-Stufe der Assetklasse – kein separater Quellen-Schalter):
  - Stufe `shadow`/`active`: freigegebene Analyse für die Klasse des Symbols laden
    (`setup_asset_class.asset_class_of`),
    Modell via `regime_lab.model_for(doc, scope, symbol)`, 1h-Kerzen (letzte 30 Tage, `candle_cache`
    /`fetch_history`, `aggregate_candles(drop_partial=True)`), `rg.current_regime(model, candles, "1h")`,
    Richtung über `mc.direction_from_regime_id(rid, mode)`, Ergebnis `mc.structural_context(
    "regime_lab", direction, label, confidence, model_fp, age_sec, ttl_sec)`.
    Zusätzlich `since_days` (Beginn des aktuellen Regimes), `kept` (ist das aktuelle Regime ein
    behaltenes?) und `stage`.
  - Stufe `none`: `state: "unknown"` (kein Fallback auf die Gate-Erkennung – die bleibt ausschließlich
    Sache von `regime_gate` mit `source=own`, damit es keine zweite implizite Wahrheit gibt).
  - Fehler → `state: "stale"` mit letztem bekannten Stand (nie Exception nach außen).
- **Frische-Regel statt TTL-Feld** (`freshness_ttl_sec(symbol, timeframe)` rein, testbar): 1h-Kerzen →
  2 Kerzen-Längen (7200 s); Nicht-Krypto außerhalb der Handelszeiten (`core/market_hours.py`) → Stand
  der letzten Sitzung gilt bis zur nächsten Eröffnung (`state: ok`, Flag `market_closed: true`);
  Kerzenlücke > 3 Kerzen → `stale`. Damit ist Wochenende bei Gold/Forex kein Fehler und Krypto nie älter als 2 h.
- Cache 15 min je Symbol (wie `regime_gate._cache`), Refresh im Hintergrund (`run_loop`, alle 10 min,
  nur Symbole mit offener Analyse/Trades; respektiert `AI_TRADER_LOCAL_DISABLE`).
- `prompt_line(symbol) -> str`:
  `"Struktur (Lab-Modell v3, 1h): BÄR stark seit 12 Tagen · Sicherheit 0,71 (heuristisch)"`
  bzw. `"Struktur: unbekannt (keine freigegebene Analyse)"`. Wortwahl bewusst anders als Kurzfrist-Regime.
- `artifact(symbol) -> Optional[str]`: `"lab:<aid>:<fp8>"` ab Stufe `shadow`, sonst `None`.
- `phase(symbol) -> Optional[str]` (`bär|bulle|seitwärts`) für das Gate.

**2.2 `regime_gate.check_signal_allowed(cfg, symbol)`:**
- Neuer Config-Key `regime_gate_source` (`own` Default). `lab` ist nur wählbar, wenn die Klasse Stufe
  `active` hat; bei `unknown/stale` oder Rückstufung auf `shadow` → **fail-open wie heute** + Log
  „Lab-Regime nicht wirksam – Trade erlaubt".
  `own` bleibt exakt der heutige Codepfad (`_detect`). Rückgabe-Text nennt die Quelle.
- UI (Auto-Trade-Modal, `StrategyAutoTradeModal.js`): Select „Regime-Quelle: eigene Erkennung |
  freigegebene Lab-Analyse" neben `regime_filter_enabled`; Hinweis, wenn keine Freigabe vorliegt.

**2.3 Tests (`backend/tests/test_structural_regime.py`):**
- Resolver mit Fake-Modell + synthetischen Kerzen: Richtung aus Regime-ID, `kept`-Flag, `since_days`.
- Ohne Freigabe → `unknown`; abgelaufene TTL → `stale`; Fehler in Kerzen-Ladung → `stale`, keine Exception.
- `prompt_line`-Snapshots (bekannt/unbekannt/stale) enthalten nie Kurzfrist-Begriffe (`trend_up`, `range_ruhig`).
- Gate: `source=own` byte-identisch zum heutigen Ergebnis (Regressionstest gegen bestehende
  `test_guard_separation.py`/Gate-Tests), `source=lab` blockiert nur bei `known`.

### Baustein 3 – KI-Trader sieht die Struktur (Prompt) + Snapshot

**3.1 Prompt (`ai_engine.py`, Gruppen-Prompt ~Z. 1618; nur für Klassen mit Stufe `active`):**
- Vor `=== MARKTDATEN … ===` ein Block
  `=== STRUKTURELLES MARKTREGIME (freigegebenes Lab-Modell, 1h – NICHT das Kurzfrist-Regime der Symbolzeilen) ===`
  mit einer Zeile je Symbol der Gruppe (dedupliziert je Klasse bei `scope=combined`).
- Ergänzung im System-Prompt (beide Varianten): „Struktur = Großwetterlage über Wochen (Lab), Kurzfrist-
  Regime = Zustand der letzten Stunden (Symbolzeile). Beziehe Lektionen, die sich auf die Struktur
  beziehen, ausdrücklich mit ‚strukturell' im Text ein."
- Lern-System-Prompt (`ai_learning.LEARNING_SYSTEM`): Hinweis, Regime-Lektionen mit `context`
  eindeutig zu benennen („strukturell Bär" vs. „Kurzfrist range_ruhig"), damit `is_expired/context`
  sauber greifen. Keine Änderung der Lektions-Gates.
- Smart-Skip (`_should_skip_group`): Struktur-Wechsel eines Symbols (Richtung ändert sich) zählt als
  „Markt verändert" → kein Skip in diesem Zyklus.

**3.2 Snapshot & Rewards:**
- `ai_market_observer.entry_snapshot(sym)` bleibt unverändert; in `ai_engine` wird beim Bau von `dec`
  `entry_market_snapshot["structural"] = structural_regime.resolve(sym)` ergänzt (ab Stufe `shadow` –
  genau das ist der Zweck der Shadow-Stufe: Daten sammeln ohne Wirkung).
- `ai_rewards`: neue Aggregation `by_structural_regime(db, days)` (rein wie `by_regime`), Endpoint
  `GET /api/ai/rewards` liefert zusätzlich `by_structural_regime`; UI im Reward-Panel als zweite Tabelle
  „nach Struktur-Regime". Kurzfrist-Tabelle bleibt.

**3.3 Tests:** Prompt-Snapshot bei Stufe `none`/`shadow` identisch zu heute; bei `active` enthält Block +
Trennhinweis; `entry_market_snapshot.structural` abwesend bei `none`, vorhanden ab `shadow`;
`by_structural_regime` mit FakeDB.

### Baustein 4 – Fingerprint & Policy-Bericht

**4.1 `ai_engine.py` ~Z. 1693:** `policy_fingerprint.build(..., regime_artifact=structural_regime.artifact(sym))`
(liefert ab Stufe `shadow` einen Wert, sonst `None` → Schema wie heute). **Wichtig:** Artefakt ist je Assetklasse stabil (Freigabe-ID + Modell-FP), nicht je
Regime-Zustand – sonst rotiert der Fingerprint bei jedem Regime-Wechsel. Auch `policy_lab` (Shadow,
~Z. 1770) bekommt dasselbe Artefakt, damit Champion/Kandidat vergleichbar bleiben.

**4.2 Policy-Bericht (`setup_variant.policy_report_rows` – bereits v2-fähig):** `changed` zeigt
„Regime-Modell", wenn sich das Artefakt ändert (Label existiert seit 17.09.). Frontend
`PolicyReportCard.policySubtitle`: Artefakt-Kurzform anzeigen („Lab-Modell a1b2c3").

**4.3 Tests:** `test_policy_fingerprint.py` erweitern: gleiche Teile + anderes Artefakt → anderer
`combined`, `changed == ["Regime-Modell"]`; Stufe `none` → Schema wie heute (Regression auf bestehende
Fingerprints, damit Alt-Trades gruppierbar bleiben).

### Baustein 5 – Strukturelle Lektionen (optional, nach 4 Wochen Daten)

- `ai_lessons.normalize`: optionales Feld `layer: "structural" | "setup_context" | null` (aus `context`
  heuristisch oder von der KI gesetzt). Prompt-Marker `[STRUKTUR]`/`[KURZFRIST]`.
- `Lektions-Bilanz` (siehe `PLAN_LEKTIONS_BILANZ.md`) gruppiert zusätzlich nach `structural.direction`
  → beantwortet „wirkt Lektion X nur im strukturellen Bär?".
- Keine automatische Verfallslogik-Änderung; `valid_until` bleibt.

## 6. Reihenfolge, Rollout, Abnahme

| Phase | Inhalt | Abnahme |
|---|---|---|
| 1 | Baustein 1 (Nachweis-Gate, `/release`, UI-Knöpfe Shadow/Wirksam, Proposal-Scope `regime_release`) | Knopf „Beobachten" wird erst grün, nachdem Kalibrierung + Ablation gelaufen sind; Tooltip nennt fehlende Nachweise; kein Runtime-Effekt |
| 2 | Baustein 2 (Resolver + Frische-Regel + Gate-Quelle) und Baustein 4 (Fingerprint) – eine Krypto-Analyse auf **Shadow** | `entry_market_snapshot.structural` füllt sich; Policy-Bericht zeigt einmalig `geändert: Regime-Modell`, danach stabil; Prompt und Gate byte-identisch zu heute (Regressionstests) |
| 3 | Shadow laufen lassen, Rewards `by_structural_regime` beobachten | ≥ 30 Trades je Klasse; Forschungs-Analyst gibt `regime_release_recommendation` ab (sichtbar im Bericht) |
| 4 | Stufe **Wirksam**: entweder KI-Vorschlag übernehmen (`suggest`) / Karenz verstreichen lassen (`auto`) oder manuell – Baustein 3 Prompt-Block wird damit aktiv | Analyse-Feed zeigt Struktur-Block; Token-Zuwachs < 3 % je Prompt; `history` dokumentiert, wer geschaltet hat |
| 5 | Nach weiteren ≥ 30 Trades: Vergleich Rewards Shadow-Phase vs. Wirksam-Phase; ggf. Gate `lab` für KI-Trader | Entscheidung in `KI_TRADER_AUDIT.md`; bei Verschlechterung: KI-`revoke` oder manueller Widerruf → Shadow |
| 6 | Baustein 5 (optional) | erst wenn Lektions-Bilanz produktiv |

Rollback: Stufe `none`/`shadow` setzen → Prompt/Gate exakt wie heute; Fingerprint bleibt bei `shadow`
absichtlich mit Artefakt (sonst würde der Rückweg selbst eine neue Policy-Version erzeugen).

## 7. Empfohlene Lab-Einstellungen für die freizugebende Analyse

- Engine **v2**, Detector **reactive**, `adapt_profile: auto`, `regime_mode: 5`.
- Timeframe **1h**, Zeitraum **360–720 Tage**, `train_pct: 70–75` (Holdout unangetastet).
- Scope **combined** je Assetklasse (Krypto: BTC/ETH/SOL/BNB; Indizes QQQ/SPY; Rohstoffe XAU/XAG;
  Forex EURUSD/USDJPY) – per_coin nur, wenn die Kalibrierung deutliche Unterschiede zeigt.
- Vor Freigabe: `calibrate` (truth_source `centered`) und `ablation` laufen lassen; nur freigeben, wenn
  die Regime-Umschaltung im Holdout gegen die statische Variante nicht verliert.
- TTL 6 h (Krypto) / 24 h (Nicht-Krypto, Wochenende → `stale` ist erwünscht, nicht Fehler).

## 8. Risiken & Gegenmaßnahmen

| Risiko | Gegenmaßnahme |
|---|---|
| Begriffs-Vermischung in Lektionen („Bär" = welches Regime?) | eigener Prompt-Block mit Trennhinweis; Marker `[STRUKTUR]`; Lern-Prompt-Regel |
| Fingerprint rotiert zu oft | Artefakt = Freigabe-ID + Modell-FP (stabil), nicht Regime-Zustand; Flag getrennt |
| Zwei Wahrheiten (Gate vs. Lab) bleiben | `regime_gate_source=lab` optional; Debug-Endpoint zeigt beide nebeneinander |
| Stale-Daten am Wochenende (Nicht-Krypto) | `state: stale` → fail-open, im Prompt als „unbekannt" |
| Atlas-/RAM-Last (Kerzen 1h × Symbole) | 30 Tage 1h = 720 Kerzen je Symbol; Cache 15 min; Hintergrund-Refresh; `ram_guard`-Profil beachten |
| Render-Neustart verliert Cache | `settings/structural_regime_cache` persistiert, Boot lädt daraus (kein Download-Burst) |
| Ungetestetes Lab-Modell steuert Live | Nachweis-Gate (Kalibrierung + Ablation + Mindestabschnitte) vor Shadow; Stichproben-Gate vor Wirksam; Gate `lab` erst nach Phase 5 |
| KI schaltet „wirksam" zu früh/zu oft | KI kann Gates nicht umgehen (`needs_data`); `auto` nur mit 24 h Karenz + Stopp-Knopf + Telegram; Cooldown 7 Tage; Widerruf jederzeit; Sicherheitsrichtung (`revoke`) ohne Karenz |
| Freigabe per Bauchgefühl | Knopf grau bis Nachweise vorliegen; Tooltip nennt, was fehlt; Evidence-Hash + `history` unveränderlich |
| Manuell gesetzte TTL veraltet still | kein TTL-Feld; Frische-Regel aus Timeframe + Marktkalender im Resolver |

## 9. Nicht Teil dieses Plans (bewusst)

- Ersetzen des Kurzfrist-Regimes durch das Lab-Regime.
- Automatische Rotation/Neuberechnung von Lab-Modellen (die KI darf Stufen vorschlagen/umschalten, aber
  kein neues Modell ohne Lab-Lauf und Nachweis freigeben).
- Änderung der dynamischen Strategien (`dynamic_live`) – sie nutzen weiterhin ihre Assignments.
- HMM/Change-Point-Modelle (spätere Kandidaten laut `analysis_paket/03`).

## 10. Gesamtaufwand

B1 1 Tag (Nachweis-Gate + Stufen + Proposal-Pfad) · B2 1 Tag · B3 1 Tag · B4 0,5 Tag · Tests/Abnahme 0,5 Tag → **~4 Tage**; B5 separat
(~1 Tag) nach der Lektions-Bilanz. Empfohlene Reihenfolge insgesamt: **Lektions-Bilanz Baustein A
zuerst** (Datensammlung startet früh), dann Regime-Brücke B1–B4, dann Lektions-Bilanz B/C.
