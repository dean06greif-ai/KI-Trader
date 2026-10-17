# 03 – Zielbild: Forschungslabor mit kontrollierter Verbindung zum Trader

## 1. Entscheidung: gezielter Ausbau statt Neubau

**Deine Laboridee ist fachlich sinnvoll.** Ein gutes Regime Lab beantwortet nicht nur „Welche Farbe hat dieser Chartabschnitt?“, sondern:

> Welche handelbare Regel bringt für welche Assets und Marktbedingungen einen robusten Zusatznutzen gegenüber einer einfachen Alternative — nach Kosten und außerhalb der Daten, auf denen sie ausgewählt wurde?

Regime sind dabei **Kontext**, kein Selbstzweck. Nicht jede Strategie muss neun verschiedene Regeln besitzen. Ein guter statischer Ansatz kann einer aufwendig umgeschalteten Strategie überlegen sein. „Kein neuer Trade“ ist ein zulässiges und oft notwendiges Ergebnis. Mehr Automatik ist nur dann ein Fortschritt, wenn sie die Qualität nachweislich erhöht.

### Gegenüberstellung der Optionen

| Option | Vorteil | Nachteil | Urteil |
|---|---|---|---|
| Unverändert lassen | Keine kurzfristige Umstellungsarbeit | Belegte Ausführungs-/Forschungsbrüche bleiben | Nicht als langfristiges Ziel geeignet |
| Komplett neu bauen | Fachgrenzen frei gestaltbar | Hoher Regressions-/Migrationsaufwand, parallele Engines | Nicht empfohlen |
| Nur weitere Indikatoren/KI-Prompts | Schnell sichtbare Funktionen | Vergrößert Suchraum, Overfitting und Wartung | Erst nach valider Messbasis |
| Vorhandenes Lab zu versionierter Forschungsstrecke ausbauen | Bestehende Oberfläche/Module nutzbar, kontrollierbarer Fortschritt | Disziplin bei Daten/Freigaben nötig | **Empfohlen** |

## 2. Zielarchitektur innerhalb der vorhandenen Struktur

```text
                       RESEARCH / KEINE ORDERRECHTE
Feste Marktdaten → Featureversion → Regime-/Strategieexperiment
                                      ↓
                           innere Auswahlvalidierung
                                      ↓
                           unabhängige äußere Tests
                                      ↓
                             Research Report
                                      ↓
                       StrategyRelease (unveränderlich)
                                      ↓ explizites Approval
──────────────────────────────────── Grenze ────────────────────────────────────
                             RUNTIME / HANDEL
Neue verfügbare Daten → versionierter MarketContext → PlanResolver
                                                        ↓
                        deterministisches Signal / begrenzter KI-Vorschlag
                                                        ↓
                          zentrale RiskPolicy / PortfolioReservation
                                                        ↓
                            OrderIntent → Broker → FillEvents
                                                        ↓
                          Schutzstatus + Ledger + Reconciliation
                                                        ↓
                    Monitoring / Shadowvergleich / neue Forschungshypothese
```

Die Rückkopplung aus realen Ergebnissen erzeugt **neue Kandidaten**, keine stillen Änderungen eines bereits freigegebenen Releases. Rechenworker erhalten Daten-/Experimentaufträge, keine Möglichkeit, aus einem Forschungsergebnis selbst eine Livefreigabe abzuleiten.

### Logische Bausteine und existierende Anker

Neue Namen sind Vorschläge, keine bereits implementierten Dateien. Die erste Stufe kann flache Module unter `backend/services/` verwenden; ein späteres Unterpaket setzt aktualisierte Worker-Paketierung voraus.

| Logischer Baustein | Bestehender Anker | Additive Erweiterung |
|---|---|---|
| Dataset Snapshot | `history_sources`, `candle_cache`, `candles` | `research_dataset.py`: unveränderliche Fenster, Qualität, Manifest |
| Feature-/Regimevertrag | `regime_core`, `regime_engine`, `ai_market_observer` | `market_context.py`: benannte Ebenen, Herkunft, Zeitpunkte |
| Ausführbarer Strategieplan | `dynamic_strategy`, `dynamic_live`, Strategy Registry | `strategy_plan.py`: reiner Resolver, Binding-/Fallbackvertrag |
| Referenzsimulation | `backtester` | Reiner Position-/Fill-Kern; bestehendes `simulate_pair` als Adapter |
| Experimente/Splits | `regime_lab`, `regime_opt` | `research_experiment.py`, `research_validation.py` |
| Freigabe | `routers/regime_lab`, `routers/dynamic`, `policy_promotion` | `strategy_release.py`: Version, Evidence-Hash, Approval |
| Risiko | `entry_guard`, `risk_budget`, `position_sizing` | Freshness, konservative Unknown-Policy, Reservation |
| Orderzustand | `entry_order_registry`, `entry_inflight`, Brokeradapter | Erweiterte Intent-/Fill-/Schutzstatusdaten |
| Ergebnisse/Lernen | `ai_learning`, `pnl_reconcile`, `policy_fingerprint` | versionierte OutcomeEvents, disjunkte Evidenz |

Keine neue Datenbank oder Infrastrukturkomponente ist für den ersten sinnvollen Schritt notwendig. Metadaten können in der vorhandenen MongoDB bleiben, größere Datenmengen in bestehenden passenden lokalen/Worker-Datenformaten mit Hashreferenz. Nicht komplette jahrelange Candlearrays unkontrolliert in einzelne Mongo-Dokumente packen.

## 3. Datenvertrag: identische Inputs, nachvollziehbare Bedeutung

### `DatasetManifest` (vorgeschlagen)

Pflichtfelder:

- `dataset_id`, `schema_version`, `created_at`, `code_commit`.
- `instrument_id` als stabile ID; Symbol, Assetklasse, Datenvenue, Handelsvenue, spot/perpetual/FX, Quote- und Kontowährung separat.
- `timeframe`, `bar_timestamp_semantics`, UTC-Fenster `[start_ts,end_exclusive_ts)`.
- `source_revision`, `raw_checksum`, `normalized_checksum`, genaue Candleanzahl.
- `available_until_ts`, Umgang mit noch offenen Bars, Marktkalender-/Sessionversion.
- Qualityreport: Lücken, Dubletten, unsortierte Bars, nichtendliche Zahlen, OHLC-Invarianten, Ausreißer, ungültige Volumenfelder, Listing-/Delistingfenster.
- Bei Futures: Kontrakt-/Funding-/Tick-/Qty-Information und deren zeitliche Gültigkeit, soweit verfügbar. Bei FX: Kalender und Währungsumrechnung. Nicht verfügbare Informationen ausdrücklich markieren.
- Datenberichtigungen als neue Version. Bestehende Experimentdaten nicht still ersetzen.

**Datenqualität ist ein Gate.** Fehlende Tage sollen nicht als ruhiger Markt erscheinen. Keine perfekte Liquiditäts-/Slippageannahme, wenn lediglich Candlepreise verfügbar sind. Daten einer anderen Venue können für Forschung nützlich sein, müssen aber als Proxy gekennzeichnet und gegen die Zielvenue validiert werden.

### Zeitbegriffe nicht vermischen

`bar_open_ts` ≠ `bar_close_ts` ≠ `available_at` ≠ `decision_at` ≠ `order_submitted_at` ≠ `filled_at`.

Eine Information darf einen Trade nur beeinflussen, wenn `available_at <= decision_at`. Multi-Timeframe-Kontext wird als rückwärtsgerichteter As-of-Join verbunden. Ein 1h-Wert darf nicht ab 10:00 in den 10:05-Trade gelangen, wenn die Stundenkerze erst11:00 geschlossen ist.

### Gemeinsame Zeitgrenzen über Assets

Gleichzeitig trainierte Assetmodelle verwenden eine gemeinsame Kalendergrenze, nicht bloß jeweils75% der vorhandenen Candleanzahl. Ein später gelistetes Asset oder längere Datenlücken verändern sonst die relativen Zeiträume; bei gepoolter Modellwahl können Trainingsdaten eines Assets zeitlich in den Test eines anderen reichen.

## 4. Regimevertrag: Kontext, Setup und Risiko trennen

### Drei sinnvolle Ebenen

1. **Struktureller Kontext:** längerfristiger Trend/Seitwärts, Volatilität, Liquidität/Session. Eher stabil; Tages-/Mehrstundenhorizont nach Datenbasis.
2. **Handelbares Setup:** kurzfristiger Pullback, Breakout, Range-Entry usw. Deutlich schneller als das strukturelle Regime.
3. **Risk-Overlay:** Datenqualität, Spread/Funding, Stress/News/Session, Korrelation/Exposure, unbekannter Brokerzustand. Darf Entries einschränken, muss nicht automatisch ein neues „Regime“ erzeugen.

Dadurch muss nicht ein einziger langsamer Regimedetektor zugleich Tagesschwankungen, Entry-Timing und Liquiditätsrisiko lösen.

### `MarketContext` (vorgeschlagen)

```json
{
  "schema_version": 1,
  "instrument_id": "example-btc-perpetual",
  "as_of_bar_close_ts": "2026-09-01T12:00:00Z",
  "available_at": "2026-09-01T12:00:02Z",
  "feature_version": "features-example-v1",
  "regime_model_id": "regime-example-v1",
  "taxonomy_version": "context-v1",
  "structural_regime": "trend_up",
  "volatility_bucket": "high",
  "short_term_setup_context": "pullback",
  "confidence_score": 0.72,
  "score_is_calibrated_probability": false,
  "data_quality": "valid",
  "status": "known"
}
```

Beispieldaten, keine produktive Konfiguration oder Modellleistung.

**Wichtig:** Bestehende Confidence-/Probability-Felder der Detektoren sind heuristische Scores. Sie sind ohne separate Kalibrierung keine72%-Gewinn- oder Regimewahrheitswahrscheinlichkeit. Anzeige und Freigabe müssen das respektieren.

### Historische Referenz ist ein anderes Objekt

`RetrospectiveReference` trägt ausdrücklich `uses_future=true`, Berechnungs-/Vorwärtshorizont, Version und diagnostischen Zweck. Es kann Verzögerung, Richtungswechsel und Regimeverständlichkeit bewerten. Es darf nicht die zum Entscheidungszeitpunkt verfügbare Information ersetzen.

### Welche Modellfamilie zuerst?

- Vorhandene deterministische EMA/Kombi/Reactive-Familien behalten und gegeneinander vergleichen.
- Kleine, interpretierbare Kontextmenge als Baseline; Aufspaltung in mehr Regime nur bei zusätzlichen robusten Informationen und ausreichenden unabhängigen Fällen.
- K-Means für Bestandskompatibilität und explizite Forschungsversuche behalten; numerischen R16 zuerst beheben.
- HMM, Change-Point-Modelle, ML-Classifier oder Soft-Routing sind **spätere Kandidaten**, keine notwendige Erstmaßnahme.
- Assetspezifische Kalibrierung nur bei Datenstärke; sonst Pooling über sachlich sinnvolle Assetklassen mit vorsichtiger assetspezifischer Abweichung.

## 5. Strategieplan: einmal definiert, überall gleich aufgelöst

### `StrategyRelease` / `StrategyBinding`

- Stabile Release-ID und Schema-/Versionsnummer.
- Referenz auf genaue Registry- oder Custom-Definition plus Definitionhash.
- Pro Kontextregime: `strategy_definition_id`, vollständige Strategieparameter, Trade-/Exitparameter, zulässige Seiten, Instrumente, Timeframe.
- Eindeutiger Fallback für `unknown`, `unmapped`, `stale`, `insufficient_history`.
- Transitionpolicy: was gilt für neue Entries und für schon offene Trades?
- Data-/Feature-/Regime-/Cost-/Risk-/Executionmodell-Versionen.
- Validierungsbericht, Fingerprint, Status, Freigabeakteur und Zeitpunkt.

**Resolverfunktion:** Gleiche Eingabe aus Release + MarketContext + gültigen persönlichen Einstellungen → gleicher EffectivePlan, unabhängig vom zuvor aktiven Regime. Keine kumulativen State-Merges.

### Bestehende Positionen bei Regimewechsel

Empfohlener Standard für neue Releases: **kein neuer Entry nach altem Plan, bestehende Position weiter unter ihrem ursprünglichen Exit-/Schutzvertrag verwalten.** Eine neue Policy darf nicht rückwirkend Risiko erhöhen oder Stops beliebig entfernen.

`close_open` ist nur eine ausdrücklich getestete Strategieentscheidung. Backtest muss ihre Kosten und Exitpreise tatsächlich simulieren. Keine symbolweite Fremdpositionen-Schließung als Nebenwirkung.

### Konfigurationspräzedenz

In einem Test explizit festschreiben:

```text
versionierter Basisplan
  + freigegebener Regime-Binding-Snapshot
  + erlaubte persönliche Scope-Overrides
  → effektiver Plan
  → globale harte Risikogrenzen begrenzen ihn abschließend
```

Persönliche Overrides dürfen harte Grenzen nicht überschreiben. Welche bestehenden Keys Override-relevant sind, wird einmal inventarisiert; keine zweite versteckte Präzedenz im Brokeradapter.

## 6. Echter Laborablauf

### Schritt A – Fragestellung vor Suche definieren

Beispiel: „Verbessert ein ATR-normalisierter Trendfilter die Nettoerwartung einer vorhandenen Pullbackstrategie in hochvolatilen Kontexten gegenüber derselben Strategie ohne Filter?“

Vorab festhalten: Assetuniversum, Datenzeitraum, Trainings-/Testfenster, Kosten-/Fillmodell, erlaubter Suchraum, Hauptmetrik und Verlust-/Stabilitätsgrenzen. Erst dann suchen. Nicht nach jedem Resultat das Ziel ändern.

### Schritt B – Indikatoren als versionierte Features testen

- Univariate Wirkung/bedingte Verteilungen, Missingness und Stabilität über Zeit/Assets.
- Redundanz zwischen Indikatoren; keine fünf stark korrelierten Trendindikatoren als fünf unabhängige Stimmen zählen.
- Ablation: derselbe Plan ohne den Indikator, mit einfacher Alternative und mit dem vorgeschlagenen Merkmal.
- Alle Transformations-/Skalierungs-/Auswahlschritte nur im Trainingsfold fitten.
- Diagnosekennzahlen wie Trefferquote, Lag und Übergangsstabilität getrennt von Trade-Nettoergebnissen.

### Schritt C – Verschachtelte zeitliche Validierung

1. Ein äußeres Zeitfenster dient nur zur Bewertung der vollständig ausgewählten Pipeline.
2. Innerhalb des davor liegenden Trainingsfensters finden Feature-/Regime-/Strategie-/Parametersuche und Auswahlvalidierung statt.
3. Ausgewählte Pipeline einfrieren; im äußeren Fenster barweise kausal durchlaufen lassen.
4. Danach vorrollen, nur bereits verfügbare Daten verwenden.
5. Ein letzter reservierter Zeitabschnitt wird einmalig nach Festlegung der Methode geprüft. Wird er erneut zur Anpassung verwendet, ist er ab dann Validierung; neues unabhängiges Material nötig.

Purging folgt den tatsächlich überlappenden Outcome-/Labelintervallen. Embargo/Gaps werden begründet an Horizont, Positionstragedauer und Datengenerierung festgelegt — nicht willkürlich „fünf Tage“, nicht fälschlich jeder Blick in zulässige historische Warmupdaten verboten.

### Schritt D – Gleiche Vergleichsbasis

Vergleichen gegen:

- keinen Trade als Cash-/Risiko-Baseline;
- freigegebene statische Strategie;
- einfachen festen Regimefilter;
- Regime-Parameteranpassung;
- erst dann echte Strategiewahl oder komplexere Modelle.

Alle Varianten sehen dieselben verfügbaren Entscheidungssituationen und verwenden dieselben Kosten-/Fillregeln. Unterschiedliche Zahl gefüllter Trades und geblockte Chancen offenlegen; nicht nur Gewinner oder nur im jeweiligen Modell gewählte Zeitfenster vergleichen.

### Schritt E – Ergebnisbericht

Pflicht: netto PnL und R, realisierte UND offene Equity-/Drawdownpfade, Gebühren/Funding/Slippage, Turnover, Exposure, Auslastung, Trade-/Episodeanzahl, Regime-Coverage, Profitfaktor mit korrektem Sonderfall „keine Verluste“, Ausfall-/Nichtfillquote, Signal-zu-Fill-Verzögerung.

Unsicherheit: block-/sessionbasierte Konfidenzintervalle, Sensitivität gegenüber Parametern/Kosten/Fenstern, Ergebnisverteilung statt nur bester Wert. Suchversuche, Abbrüche und nicht ausgewählte Kandidaten zählen. Deflated Sharpe/PBO sind bei größerer Suche mögliche Ergänzungen, keine magischen Freigabezertifikate.

**Minimum-Trades sind keine Naturkonstante.** Wenige hochkorrelierte Trades aus einer Trendepisode sind keine unabhängige Stichprobe. Mindestanzahl, Zeit-/Regimeabdeckung und Konfidenz müssen gemeinsam beurteilt werden; sonst `insufficient_evidence`, nicht `passed`.

## 7. Rolle des KI Traders

### Sinnvolle LLM-Aufgaben

- Testbare Hypothesen und kleine erlaubte Parameterbereiche vorschlagen.
- Forschungsberichte zusammenfassen, Unterschiede erklären, belegte Schwachstellen identifizieren.
- Nachrichten/qualitativen Kontext strukturiert erfassen, mit Quelle/Zeitpunkt versehen und außerhalb harter Sicherheitsentscheidungen halten.
- Kandidaten für Paper/Shadow vorschlagen; Ergebnisse anhand Evidence-IDs kommentieren.

### Nicht allein an das LLM delegieren

- Live-Freigabe bei fehlender Evidenz.
- Positionsidentität und Fillmengen nach Timeout.
- Absolute Risiko-/Kapitalgrenzen, Kill-Switch und Stopbestätigung.
- Ungültige oder veraltete Daten als aktuelle Wahrheit interpretieren.
- Eigenständig den Validator oder unveränderlichen freigegebenen Release umschreiben.

Das bestehende KI-Frontend muss nicht verschwinden. Der erste Umbau ist intern: strukturierte Vorschläge statt versteckter Zustandsänderungen, gemeinsame freigegebene Artefakte und klare Gründe bei Nichtausführung.

## 8. Freigabe und Betrieb

### Zustände

`draft → research_validated → shadow → paper_validated → approval_pending → approved_live → retired`

Daneben `rejected`, `stale`, `insufficient_evidence`, `suspended`. „Shadow“ trifft Entscheidungen ohne Orders; „Paper“ führt simulierte Orders nach dem definierten Fillmodell aus. Das darf nicht zusammengezählt werden.

### Harte Freigabekriterien

- Dataset, Code, Definition und Validierungsfingerprint passen exakt.
- Keine offenen P0-Ausführungs-/Paritätsprobleme im genutzten Pfad.
- Risiko- und Kostenstress erfüllt vorab festgelegte Grenzen.
- Ausreichende unabhängige OOS-/Paper-Evidenz für die erlaubte Domain.
- Reconciliation und Schutzstatus vollständig; bekannte Broker-/Kontokonfiguration.
- Explizite menschliche Erstfreigabe und klarer Scope; kein automatisches Hochskalieren wegen weniger Gewinner.

### Überwachung

Drift der Features/Regimebelegung, Fill-/Slippageabweichung, Datenlücken, Stop-Unknown, offene Intentalter, Regime-Flattern, tatsächliche vs. gewünschte Konfiguration, netto Ergebnisse pro Release. Vorab definierte Reaktion: neue Entries stoppen oder auf letzten geeigneten freigegebenen Plan zurückkehren; bestehende Positionsbetreuung erhalten.

## 9. Erste sinnvolle Laborversion

Noch keine neuen Indikatoren nötig. Ein Pilot mit wenigen ausreichend liquiden Assets, **einer vorhandenen Strategie**, **einem bestehenden Regimedetektor**, festem Datensatz und **einem** kleinen Suchraum reicht.

Akzeptanz des Piloten: gleiche Signale/Positionsübergänge im Referenzreplay und Runtime-Resolver, nachvollziehbar unveränderte Daten, ehrlicher unabhängiger Bericht, explizite Draft→Approval-Grenze. Wenn der Pilot keinen Zusatznutzen zeigt, die statische Basis beibehalten. Genau diese Möglichkeit macht ein Labor wissenschaftlich nützlich.