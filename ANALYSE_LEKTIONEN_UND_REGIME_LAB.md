# Analyse: Lektionen-Validierung & Regime-Lab ↔ KI-Trader (Stand 17.09.2026)

Antworten auf die drei offenen Fragen aus dem Verbesserungs-Auftrag. Reine Analyse –
Code wurde hierfür NICHT geändert (nur Policy-Versionen-UI und Event-Setup-Tabelle, siehe Git-Diff).

---

## 1. „Policy-Versionen“ – was zeigt das überhaupt?

**Kurz:** Ein *Regelwerk-Stand* des KI-Traders. Der Trader entscheidet mit Prompt + Lektionen +
Playbook + Modell + ML-Gate + Sizing + Einstellungen. Ändert sich eines davon, wird automatisch ein
neuer Fingerprint gebaut (`services/policy_fingerprint.py`) und an jede Entscheidung und jeden Trade
gehängt. Die Karte im Analyse-Panel gruppiert geschlossene Trades nach diesem Fingerprint und zeigt
je Stand den **Netto-PnL** (Fees/Funding bereits abgezogen) getrennt nach LIVE / PAPER / SAMMEL.

**Neu (umgesetzt):** Versionen heißen jetzt „Version 12 · 17.09.–17.09. · Modell · ML-Gate v77 ·
geändert: ML-Gate“ statt Hash. Ein dezentes ⓘ-Icon erklärt die Karte, Standard ist eingeklappt.
Backend liefert dafür `version_no`, `is_current`, `changed` (`services/setup_variant.py`,
Tests `backend/tests/test_policy_report_versions.py`).

**Befund aus deinen echten Daten (48 Trades mit Fingerprint):** 14 Versionen in 4 Tagen, die meisten
mit 1–2 Trades. Ursache: fast jede Version entsteht durch „Playbook“ oder „ML-Gate“ (ML-Gate v71 → v78
in 4 Tagen, Playbook-Revisionen fast täglich). Damit ist der Bericht *technisch* korrekt, aber
statistisch fast wertlos – kein Stand lebt lange genug für eine Aussage.

**Empfehlung (klein, kein Umbau):**
- ML-Gate-Retrains und Playbook-Statistik-Updates sollten NICHT den Fingerprint drehen, solange sich
  die *Entscheidungsregeln* nicht ändern (Gate-Version nur bei echtem Modellwechsel/Schwellen-Änderung
  zählen, Playbook nur bei Revision der Setup-Regeln, nicht bei Reife-/Statistik-Updates).
- Alternativ im Bericht eine Ansicht „nach Prompt+Lektionen gruppiert“ (grobe Policy) anbieten.
  Das ist ein Backend-Einzeiler (zweiter `group_key`) und würde die 14 Versionen auf ~4 verdichten.

---

## 2. Lektionen – reicht die heutige Validierung?

### Was heute schon eingebaut ist (mehr als du befürchtest)

| Schutz | Wo | Wirkung |
|---|---|---|
| Datenbasis-Gate für neue Lektion | `ai_validation.evaluate_lesson` (`min_lesson_results` 5) | Keine Lektion aus 2 Trades |
| Wiedererkennung | `ai_learning._bump_lesson_candidate` (`min_lesson_confirmations` 2) | KI muss dieselbe Lektion in einem *späteren* Lernlauf erneut sehen |
| Disjunkte Evidenz (AP09/T08) | ebd. (`lesson_evidence_min_trades` 2) | Wiederholtes Sichten derselben Trades zählt nicht doppelt |
| Verwerfen braucht mehr Daten | `min_removal_results` 12 | Gute Lektion fliegt nicht nach einer Pechserie |
| Kontext-Bindung + Verfall | `ai_lessons.valid_until`, `context` | Regime-Lektionen laufen ab → *dormant*, nicht gelöscht |
| Reaktivierung | `renewal_valid_until` (14–90 Tage, wächst mit Bestätigungen) | Bewährtes muss sich seltener neu beweisen |
| Neubewertung per Knopf | `reevaluate_lessons` | LLM prüft jede Lektion gegen aktuelle Strategie |
| MasterPrompt-Audit | `lesson_store.audit_against_master` | Verstoßende Lektionen werden entfernt |
| Trader-Sperre | `locked` / `origin=trader` | KI darf deine Lektionen nie ändern |
| Konflikt-Konsolidierung / Dedupe | `consolidate_conflicts`, `dedupe_lessons` | Keine widersprüchlichen Doppel-Regeln |
| Policy-Fingerprint | `lessons_hash` | Jeder Trade weiß, unter welchem Lektionen-Stand er lief |

### Was fehlt – deine Sorge ist berechtigt, aber präzise eingrenzbar

Alle heutigen Prüfungen sind **Eingangs-Kontrollen** (darf die Lektion rein / raus?) oder
**LLM-Urteile** (die KI *meint*, die Lektion sei noch gültig). Es gibt **keine quantitative
Erfolgsmessung pro Lektion**:

1. **Kein Edge-Nachweis je Lektion.** Es wird nirgends gemessen, ob Trades, bei denen Lektion X
   griff, besser liefen als Trades ohne. Die Entscheidung speichert `reasoning` als Freitext, aber
   keine `applied_lessons`-Liste → Attribution ist heute nicht möglich.
2. **Keine Gegenprobe „was wäre ohne die Lektion passiert“.** HOLD-Entscheidungen werden gespeichert
   (`ai_decisions`, action=HOLD) und der Bewegungs-Scanner (`ai_move_scanner`) findet verpasste
   Moves – aber ohne Zuordnung, *welche* Lektion den Trade verhindert hat. Das Postmortem
   (`trade_postmortem`) rechnet What-if nur für *eingegangene* Trades und verfallene Limit-Orders.
3. **Kein Verschärfen/Lockern-Signal aus Zahlen.** „anpassen“ passiert nur, wenn das LLM es im
   Reeval vorschlägt – nicht datengetrieben.

### Vorschlag: „Lektions-Bilanz“ (modular, 3 kleine Bausteine, additiv)

**Baustein A – Attribution (Datenerfassung, 0 Verhaltensänderung):**
Im Analyse-Prompt bekommen Lektionen bereits eine ID (`les_xxx`). Der KI wird pro Entscheidung ein
optionales Feld `applied_lessons: ["les_a1", …]` erlaubt (LONG/SHORT *und* HOLD). Speicherung in
`ai_decisions` und Vererbung an `auto_trades` (wie heute `policy_version`). Kein Gate, kein Filter –
nur Daten. ~40 Zeilen in `ai_engine.py` + Prompt-Zeile.

**Baustein B – HOLD-Gegenprobe (Counterfactual):**
Neues Modul `services/lesson_counterfactual.py`, das wie das Postmortem arbeitet: für jede
HOLD-Entscheidung mit `applied_lessons` wird nach Ablauf des Horizonts (Scalp ~4h / Swing ~24h)
auf den Kerzen simuliert, was ein hypothetischer Trade in die von der KI *ohne* Lektion vermutlich
gewählte Richtung gebracht hätte (die KI liefert im Prompt bereits `reasoning`; ergänzend ein Feld
`would_be_action` bei HOLD). Ergebnis je Lektion: „verhinderte Trades: 14 · davon 9 wären Verlierer
(SL zuerst), 5 Gewinner → Lektion hat +3,2 R gespart“ oder eben negativ.
Wiederverwendet: `trade_postmortem.simulate_exit`, `candle_cache`, `paper_execution`-Kosten.

**Baustein C – Bilanz + Vorschlag (kein Automatismus):**
`GET /api/ai/lessons/impact` liefert je Lektion: Trades mit Lektion (n, WR, Ø R) vs. ohne,
verhinderte Trades (n, vermiedener/verpasster PnL), **Netto-Beitrag in R** und ein Urteil:
- `edge` (Beitrag > 0 bei n ≥ `min_removal_results`)
- `neutral` (|Beitrag| klein oder n zu klein → weiter sammeln)
- `hinderlich` (Beitrag < 0, n ausreichend) → Vorschlag „lockern“ mit Begründung
- `zu weich` (Lektion wurde angewandt, Trades trotzdem oft Verlierer) → Vorschlag „verschärfen“

Anzeige als Spalte in der Lektionen-Liste (Governance-Panel) + Block im Lernlauf-Prompt
(„LEKTIONS-BILANZ – Lektion X kostet netto −2,1 R über 18 Fälle; prüfe Verschärfung/Lockerung“).
Die KI entscheidet weiterhin im Rahmen der bestehenden Gates (`min_removal_results`, `locked`) –
es wird nichts automatisch gelöscht. Damit bleibt das heutige Verhalten 1:1 erhalten, die
Lektionen werden aber erstmals *messbar*.

**Aufwand:** A ≈ 0,5 Tag, B ≈ 1 Tag, C ≈ 1 Tag inkl. Tests. Risiko gering (additiv, fail-open).
**Nutzen:** genau die Antwort auf „bringt die Regel über mehrere Trades einen nennenswerten Vorteil
oder behindert sie gute Trades?“ – heute nur gefühlt, dann gemessen.

**Nicht empfohlen:** Lektionen automatisch nach Bilanz löschen/verschärfen. Bei n < 20 je Lektion
ist die Streuung zu groß; ein Fehl-Automatismus würde bewährte Regeln in Schwächephasen killen
(genau das verhindert heute die Dormant-Logik zu Recht).

---

## 3. Regime-Lab ↔ Regime des KI-Traders

### Zwei getrennte Regime-Welten (bewusst, AP08/R15 `services/market_context.py`)

| | KI-Trader-Regime (`setup_context`) | Regime-Lab (`structural`) |
|---|---|---|
| Quelle | `ai_market_observer.classify_regime_v2` | `regime_engine` v2 (reactive/regression), KMeans-Legacy |
| Datenbasis | 1m-Kerzen, letzte Stunden | 1h/4h/1d, 30–2000 Tage |
| Labels | `trend_up`, `trend_down`, `range_ruhig`, `volatil`, … | 5er/9er-Taxonomie mit Richtungs-IDs (bulle/bär/seitwärts) |
| Wer nutzt es | Analyse-Prompt, `entry_market_snapshot`, Regime-Sperrfilter (`regime_block_list`), Rewards `by_regime`, Lektionen-Kontext | Dynamische Strategien (`dynamic_live`), Regime-Gate für Auto-Trade (`regime_gate`), Forschungs-Analyst |

**Direkte Antwort:** Das Regime-Lab **verändert die Regime des KI-Traders nicht.** Eine neue
Regime-Analyse, andere Profile (fein/standard/grob) oder Kalibrierung im Lab haben **null Einfluss**
auf `range_ruhig`/`trend_up` des Traders. Deshalb bleiben **Lektionen, die auf Trader-Regimen
basieren, vollständig gültig und unberührt**, egal was du im Lab einstellst. Die Lektionen-Verfall-
Logik (`valid_until`) hängt an Zeit, nicht am Lab.

### Wie das Lab den KI-Trader *doch* beeinflusst (drei indirekte Wege)

1. **Forschungs-Analyst** (`ai_research.digest_regime_runs / digest_regime_analyses`): die letzten
   5 Lab-Läufe + 4 Analysen werden als Text („Regime 3 ‚Bulle stark‘: Strategie X +2,1 %…“) in den
   Analyse- und Lernlauf-Prompt eingespeist (`research_analyst.context_text`). Das ist *Wissen*,
   keine harte Regel – die KI kann es nutzen oder ignorieren.
2. **Regime-Gate** (`services/regime_gate.py`, Auto-Trade-Config `regime_filter_enabled` +
   `regime_block_phases`): blockiert neue Trades einer Strategie in blockierten Phasen. Es rechnet
   aber **eigenständig** (1h, 30 Tage, 3 Regime, `rg.detect_regimes`) – es liest *keine* gespeicherte
   Lab-Analyse. Lab-Einstellungen wirken also auch hier nicht. Für den KI-Trader ist das Gate
   standardmäßig aus.
3. **Policy-Fingerprint Schema 2** hat ein Feld `regime_artifact` – wird aktuell **nicht befüllt**
   (`ai_engine.py` übergibt es nicht). Vorbereitet, nicht aktiv.

### Was du konkret einstellen solltest

**Für den KI-Trader (Trader-Regime):**
- `regime_block_enabled` erst aktivieren, wenn `Rewards → by_regime` über ≥ 30 Trades zeigt, dass ein
  Regime klar negativ ist. Bis dahin Shadow-Anzeige nutzen (`/api/ai/…` liefert `regime_shadow`:
  wie viele Trades das Gate blockiert *hätte*). Default-Liste `["range_ruhig"]` ist plausibel.
- Regime-Lektionen mit `context` + `valid_until` (14–30 Tage) anlegen lassen – das machst du schon
  richtig; die Dormant-Logik übernimmt den Rest.

**Für das Regime-Lab (Forschung):**
- **Engine v2, Detector `reactive`, `adapt_profile: auto`** – das ist der Default und in
  `scripts/ema_testbed.py` empirisch geprüft (Holdout +2 pp, Lag −8 Tage). Nicht manuell an
  `rev_atr_mult`/`persist_candles` drehen, solange keine Kalibrierung (`/regime-lab/calibrate`,
  `truth_source: centered`) das begründet.
- **Timeframe 1h, Zeitraum 360–720 Tage, `train_pct` 70–75**, damit der Holdout für den
  Walk-Forward unangetastet bleibt. Unter 180 Tagen liefert `grob` zu wenige Abschnitte, `fein`
  zu viel Rauschen.
- **`regime_mode` 5** (nicht 9): die 9er-Taxonomie braucht deutlich mehr unabhängige Abschnitte
  je Regime; bei 1–2 Jahren Krypto sind das oft < 10 Fälle → Overfitting.
- Ablation (`/regime-lab/ablation`) einmal je Assetklasse laufen lassen: zeigt, ob eine
  Regime-Umschaltung überhaupt besser ist als dieselbe Strategie *ohne* Umschaltung. Wenn nein:
  statische Strategie behalten („kein neuer Trade“ ist ein legitimes Ergebnis).
- Ergebnisse nur über **StrategyRelease/Approval** in dynamische Strategien übernehmen (siehe
  `analysis_paket/03_ZIELBILD…`), nie direkt.

**Wenn du willst, dass das Lab den KI-Trader wirklich steuert (spätere Ausbaustufe):**
- Schritt 1: `regime_gate` optional auf eine *gespeicherte, freigegebene* Lab-Analyse zeigen lassen
  (statt eigener 30-Tage-Erkennung) und `regime_artifact` im Fingerprint befüllen → dann ist
  jeder Trade einem Lab-Modell zuordenbar und der Policy-Bericht zeigt „Lab-Modell A vs. B“.
- Schritt 2: strukturelles Regime als **eigene Prompt-Zeile** („Struktur: Bulle stark seit 12 Tagen,
  Modell v3“) neben dem Kurzfrist-Regime – getrennt benannt, damit Lektionen eindeutig bleiben
  („im strukturellen Bär …“ vs. „bei `range_ruhig` …“).
- Erst danach lohnen Regime-spezifische Lektionen auf Lab-Basis.

**Fazit:** Du machst dir bei Lektionen *teilweise* zu Recht Sorgen (fehlende Wirkungsmessung –
Vorschlag oben), beim Regime-Lab *nicht*: es ist sauber vom Trader entkoppelt, Lektionen sind
sicher. Der größte reale Hebel ist aktuell die Fingerprint-Granularität (Punkt 1), sonst bleibt
jede Erfolgsmessung – auch die Lektions-Bilanz – ohne Stichprobe.
