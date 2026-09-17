# Umsetzungsplan: Regime-Brücke Regime-Lab ↔ KI-Trader

Stand: 17.09.2026 · Branch `conflict_170926_1506` · Status: **PLAN, nicht umgesetzt**

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
Regime-Lab (Forschung)                         Runtime (KI-Trader / Auto-Trade)
──────────────────────                         ────────────────────────────────
regime_analyses[aid]                           structural_regime.py (NEU, Cache)
  kept / assignments                              ├─ resolve(symbol) -> MarketContext(structural)
  + release: {approved_at, by,      ─approve─►    │    Quelle A: freigegebene Lab-Analyse (Modell + Kerzen 1h)
             scope, symbols,                      │    Quelle B: Fallback = heutige regime_gate._detect
             model_fingerprint,                   │    Zustand: known | unknown | stale
             ttl_hours}                           │
                                                  ├─ prompt_line(symbol) -> "Struktur (Lab v3): Bär seit 12 T · 0,71"
                                                  ├─ artifact() -> "lab:<aid>:<model_fp>" | "gate:auto" | None
                                                  └─ phase(symbol) -> bär|bulle|seitwärts (für regime_gate)
                                                          │
            ┌─────────────────────────────────────────────┼──────────────────────────┐
            ▼                                             ▼                          ▼
   ai_engine Prompt (Struktur-Zeile,        policy_fingerprint.build(          regime_gate.check_signal_allowed
   klar getrennt vom Kurzfrist-Regime)        regime_artifact=…)               (Quelle wählbar: eigen | lab)
            │                                             │
            ▼                                             ▼
   entry_market_snapshot.structural           Policy-Bericht: "Lab-Modell A vs. B",
   auto_trades.structural_regime              Rewards by_structural_regime
```

Feature-Flags (`ai_trader_config` bzw. Auto-Trade-Config; Defaults = heutiges Verhalten):
- `structural_regime_in_prompt` (Default **false**)
- `structural_regime_source`: `"lab"` | `"auto"` (Default **"auto"** = heutige Gate-Erkennung, ohne Prompt-Wirkung)
- `regime_gate_source` je Strategie: `"own"` (heute) | `"lab"` (Default **"own"**)
- `regime_artifact_in_fingerprint` (Default **false**, damit der Fingerprint nicht unbeabsichtigt rotiert)

## 4. Datenmodell (additiv)

### 4.1 `regime_analyses[aid].release` (neu, optional)
```json
{
  "approved": true, "approved_at": "…", "approved_by": "Admin",
  "scope": "combined" | "per_coin", "symbols": ["BTCUSDT", "ETHUSDT"],
  "asset_classes": ["crypto"],
  "model_fingerprint": "a1b2c3…",          // market_context.model_fingerprint(model)
  "ttl_hours": 6,                           // wie alt darf der letzte Kerzen-Refresh sein (sonst stale)
  "notes": "Kalibriert 09/2026, Ablation +1,4 % vs. statisch"
}
```
Nur **eine** freigegebene Analyse je Assetklasse (Eindeutigkeit wird beim Approve erzwungen; die
vorherige wird auf `approved:false` gesetzt und im Feld `release_history` protokolliert).

### 4.2 `ai_decisions` / `auto_trades` (neue optionale Felder)
- `entry_market_snapshot.structural`: der `MarketContext`-Eintrag (`structural_context(...)`) zum
  Entscheidungszeitpunkt (direction, label, confidence, state, model_fingerprint, source).
- `policy_version.regime_artifact`: `"lab:<aid>:<fp8>"` bzw. `"gate:auto"` (nur bei Flag).

### 4.3 `settings/structural_regime_cache` (optional)
Persistierter Cache je Symbol (label, direction, since, confidence, refreshed_at) – überlebt Render-
Neustarts, spart Kerzen-Downloads beim Boot.

## 5. Bausteine

### Baustein 1 – Freigabe im Regime-Lab (Research → Release)

**1.1 Backend (`routers/regime_lab.py`, `services/regime_lab.py`):**
- `POST /api/regime-lab/{aid}/approve` (Admin): Body `{scope, symbol?, asset_classes, ttl_hours, notes}`.
  Validierung: Analyse muss v2 sein (`rg.is_v2(model)`), mindestens ein `kept`-Regime, `symbols`
  ⊆ Analyse-Symbole. Schreibt `release`, deaktiviert vorherige Freigabe derselben Klasse.
- `POST /api/regime-lab/{aid}/revoke`, `GET /api/regime-lab/releases` (aktive Freigaben je Klasse).
- Reine Funktion `regime_lab.validate_release(doc, body) -> (ok, reason, release)` (testbar).
- Audit-Log über bestehendes `core/audit.log_action`.

**1.2 Frontend (`RegimeLab.js`):**
- Knopf „Für KI-Trader freigeben" an gespeicherten Analysen (nur v2, nur mit kept-Regimen), Dialog mit
  Klassen-Checkboxen, TTL, Notiz. Badge „FREIGEGEBEN · Krypto · seit 14.09." in der Liste.
- `data-testid`: `regime-lab-approve-<aid>`, `regime-lab-release-badge-<aid>`, `regime-lab-revoke-<aid>`.

**1.3 Tests:** `backend/tests/test_regime_release.py` – Validierung (kein v2 → 400, kein kept → 400),
Eindeutigkeit je Klasse, Revoke, `releases`-Liste; API-Wiring-Test wie `test_policy_report.test_endpoint_wiring`.

### Baustein 2 – Struktur-Resolver (eine Wahrheit für Runtime)

**2.1 Neues Modul `services/structural_regime.py`:**
- `resolve(symbol, source="auto"|"lab") -> Dict` (MarketContext-Eintrag der Ebene `structural`):
  - `lab`: freigegebene Analyse für die Klasse des Symbols laden (`setup_asset_class.asset_class_of`),
    Modell via `regime_lab.model_for(doc, scope, symbol)`, 1h-Kerzen (letzte 30 Tage, `candle_cache`
    /`fetch_history`, `aggregate_candles(drop_partial=True)`), `rg.current_regime(model, candles, "1h")`,
    Richtung über `mc.direction_from_regime_id(rid, mode)`, Ergebnis `mc.structural_context(
    "regime_lab", direction, label, confidence, model_fp, age_sec, ttl_sec)`.
    Zusätzlich `since_days` (Beginn des aktuellen Regimes) und `kept` (ist das aktuelle Regime ein
    behaltenes?).
  - `auto`: delegiert an `regime_gate.current_phase(symbol)` (heutiges Verhalten, unverändert).
  - Keine Freigabe / Fehler → `state: "unknown"` bzw. `"stale"` (nie Exception nach außen).
- Cache 15 min je Symbol (wie `regime_gate._cache`), Refresh im Hintergrund (`run_loop`, alle 10 min,
  nur Symbole mit offener Analyse/Trades; respektiert `AI_TRADER_LOCAL_DISABLE`).
- `prompt_line(symbol) -> str`:
  `"Struktur (Lab-Modell v3, 1h): BÄR stark seit 12 Tagen · Sicherheit 0,71 (heuristisch)"`
  bzw. `"Struktur: unbekannt (keine freigegebene Analyse)"`. Wortwahl bewusst anders als Kurzfrist-Regime.
- `artifact(symbol) -> Optional[str]`: `"lab:<aid>:<fp8>"` | `"gate:auto"` | `None`.
- `phase(symbol) -> Optional[str]` (`bär|bulle|seitwärts`) für das Gate.

**2.2 `regime_gate.check_signal_allowed(cfg, symbol)`:**
- Neuer Config-Key `regime_gate_source` (`own` Default). Bei `lab` → `structural_regime.phase(symbol)`;
  bei `unknown/stale` → **fail-open wie heute** + Log „Lab-Regime unbekannt – Trade erlaubt".
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

**3.1 Prompt (`ai_engine.py`, Gruppen-Prompt ~Z. 1618; Flag `structural_regime_in_prompt`):**
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
  `entry_market_snapshot["structural"] = structural_regime.resolve(sym)` ergänzt (nur wenn Flag an).
- `ai_rewards`: neue Aggregation `by_structural_regime(db, days)` (rein wie `by_regime`), Endpoint
  `GET /api/ai/rewards` liefert zusätzlich `by_structural_regime`; UI im Reward-Panel als zweite Tabelle
  „nach Struktur-Regime". Kurzfrist-Tabelle bleibt.

**3.3 Tests:** Prompt-Snapshot ohne Flag identisch zu heute; mit Flag enthält Block + Trennhinweis;
`entry_market_snapshot.structural` vorhanden/abwesend je Flag; `by_structural_regime` mit FakeDB.

### Baustein 4 – Fingerprint & Policy-Bericht

**4.1 `ai_engine.py` ~Z. 1693:** `policy_fingerprint.build(..., regime_artifact=structural_regime.artifact(sym)
if flag else None)`. **Wichtig:** Artefakt ist je Assetklasse stabil (Freigabe-ID + Modell-FP), nicht je
Regime-Zustand – sonst rotiert der Fingerprint bei jedem Regime-Wechsel. Auch `policy_lab` (Shadow,
~Z. 1770) bekommt dasselbe Artefakt, damit Champion/Kandidat vergleichbar bleiben.

**4.2 Policy-Bericht (`setup_variant.policy_report_rows` – bereits v2-fähig):** `changed` zeigt
„Regime-Modell", wenn sich das Artefakt ändert (Label existiert seit 17.09.). Frontend
`PolicyReportCard.policySubtitle`: Artefakt-Kurzform anzeigen („Lab-Modell a1b2c3").

**4.3 Tests:** `test_policy_fingerprint.py` erweitern: gleiche Teile + anderes Artefakt → anderer
`combined`, `changed == ["Regime-Modell"]`; Flag aus → Schema 1/2 wie heute (Regression auf bestehende
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
| 1 | Baustein 1 (Approve/Revoke + UI) | Eine Krypto-Analyse freigegeben; `GET /releases` zeigt sie; kein Runtime-Effekt |
| 2 | Baustein 2 (Resolver + Gate-Quelle, Flags aus) | `/api/ai/regime-structural/{symbol}` (Debug-Endpoint) liefert `known` für freigegebene Klasse, `unknown` sonst; Gate `own` unverändert (Regressionstests grün) |
| 3 | Baustein 3 Prompt-Flag AN für Paper/Sammel | Entscheidungen tragen `structural`; Analyse-Feed zeigt Struktur-Zeile; Token-Zuwachs < 3 % je Prompt (Log `token_estimate`) |
| 4 | Baustein 4 Fingerprint-Flag AN | Policy-Bericht zeigt neue Version mit `geändert: Regime-Modell`; danach stabil (kein Rotieren bei Regime-Wechsel) |
| 5 | Nach ≥ 30 Trades je Struktur-Regime: `by_structural_regime` bewerten; ggf. Gate `lab` für KI-Trader aktivieren | Entscheidung dokumentiert in `KI_TRADER_AUDIT.md` |
| 6 | Baustein 5 (optional) | erst wenn Lektions-Bilanz produktiv |

Rollback: Flags aus → Prompt/Fingerprint/Gate exakt wie heute; Freigaben bleiben nur Metadaten.

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
| Ungetestetes Lab-Modell steuert Live | nur Approval-Pfad; Gate `lab` für Live-Strategien erst nach Phase 5 |

## 9. Nicht Teil dieses Plans (bewusst)

- Ersetzen des Kurzfrist-Regimes durch das Lab-Regime.
- Automatische Freigabe/Rotation von Lab-Modellen.
- Änderung der dynamischen Strategien (`dynamic_live`) – sie nutzen weiterhin ihre Assignments.
- HMM/Change-Point-Modelle (spätere Kandidaten laut `analysis_paket/03`).

## 10. Gesamtaufwand

B1 0,5 Tag · B2 1 Tag · B3 1 Tag · B4 0,5 Tag · Tests/Abnahme 0,5 Tag → **~3,5 Tage**; B5 separat
(~1 Tag) nach der Lektions-Bilanz. Empfohlene Reihenfolge insgesamt: **Lektions-Bilanz Baustein A
zuerst** (Datensammlung startet früh), dann Regime-Brücke B1–B4, dann Lektions-Bilanz B/C.
