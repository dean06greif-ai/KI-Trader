# 07 – Prüfgrenzen, offene Entscheidungen und Fachquellen

## 1. Umfang der tatsächlichen Prüfung

Analysiert wurde eine separate Quellkopie des öffentlichen Branches am Commit `792ff0ac44861df447d3e619042ecef1704c37c5`. Die produktive externe Website wurde nicht gestartet, verändert oder mit Zugangsdaten aufgerufen. Die Analyseumgebung enthält weiterhin ihren unabhängigen Starter; sie ist kein Ersatz für deine Anwendung.

### Tief geprüft

- Regime Lab: Datenabruf, Train-/Holdoutverwendung, Final-/Live-Segmente, Suche, Assign/Keep/Build/Walkforward.
- Dynamische Strategien: Parametereffekt, Definitionstransport, Fallback, Transition-Scope, Last-State/Retry und Confirm/Delete.
- Backtest: Entry-/Exit-/Teil-TP-/Endzustand, segmentweise Verwendung, Kennzahlen.
- KI Trader: relevante Context-/Regime-/Decision-/Setup-Gatepfade, Policyfingerprint, Governancegrenzen und Lernoutcomeprozess.
- Order-/Risiko-Hotspots: Watchdog-Adoption, Timeoutmengen, Stopverifikation, Registry, Portfolio-/Clusterbudget.
- Worker-Paketvertrag und betroffene UI-/API-Statusdarstellung im Quelltext.

### Nur teilweise geprüft

Vollständige `ai_trade_manager`-Aktionsmenge, alle Fast-Sim-Spezialzweige, komplette Key-Level-Limit-Lebenszyklen, alle Governance-/Housekeeping-Automationen, ML-Modelltraining/Validierung, kompletter IBKR-Sync, alle Brokergebühren-/Währungsfälle, Worker-Reconnect/Claim-Persistenz und sämtliche frontendseitigen Interaktionen.

### Nicht geprüft / nicht behauptet

- Tatsächliche aktive Settings oder gespeicherte Daten deiner Produktionsinstanz.
- Ob Regime-Discovery, `close_open`, Setup-Bypass oder betroffene Legacy-K-Means-Dokumente bei dir aktuell genutzt werden.
- Brokervertragsdetails, Netzwerk-/Gatewaylatenz, tatsächliche Gebühren/Slippage, Live-Fills, Reconnects und Stoporderabdeckung.
- Langfristige Profitabilität, aktuelle Model-Providerqualität oder behauptete Renditewerte in älteren Dokumenten.
- Vollständiger Sicherheits-/Penetrationstest. Die Credentialwarnung folgt der Offenlegung im Chat; sie ist kein separates Sicherheitsaudit.
- Vollständiger Test aller280 bestehenden Backend-Testdateien oder jeder der532 Python-Dateien.

## 2. Wichtige zurückgenommene/verfeinerte Annahmen

1. **Segmentende:** Ein Kommentar behauptet erzwungenes Schließen. Tatsächlicher Backtester verliert offene Positionen. Der finale Katalog verwendet den Codebefund, nicht den Kommentar.
2. **R05:** Kausaler Labeler wurde nicht als generell lookaheadbehaftet bestätigt. Verkabelung mit Final-Segmenten ist belegt; die konkrete getestete Future-Append-Probe blieb ohne Finaländerung.
3. **TP1/TPFull:** Erste Fixture hatte identische Ziele und zeigte nur das fehlende TP1-Flag. Die finale Probe verwendet verschiedene Ziele und belegt +6 statt+4 tatsächlich numerisch.
4. **T02/T03:** Erste Branchmodelle waren schwächer als Originalcodeausführung. Die finale Testdatei führt AST-extrahierte Originalbranches mit kontrollierten Abhängigkeiten aus. Weiterhin kein vollständiger Brokerflow.
5. **Frontend:** Ein früher Testagent sah die allgemeine Startervorschau. Das ist kein Befund über die Nutzerwebsite; nicht in den Produktfehlerkatalog übernommen.
6. **Testgrün:** Bestehende Tests bestätigen teilweise ausdrücklich das heutige fail-open-Verhalten. Ein grüner Test ist nicht automatisch ein gutes fachliches Design.

## 3. Vor späterer Umsetzung zu entscheidende Produktregeln

Diese Fragen blockieren die Analyse nicht. Für einen Implementierungsauftrag sind begründete Defaults vorgeschlagen, aber nicht als aktive Nutzerkonfiguration auszugeben.

| Entscheidung | Empfohlener Default für neue Releases | Warum |
|---|---|---|
| Bestehende Position bei Regimewechsel | Unter ursprünglichem Exitplan weiterführen | Verhindert implizite Risikoänderung und unrealistischen Backtest |
| Fehlendes/unklares Regime | Keine neuen Entries | Fehlender Kontext soll nicht heimlich irgendeine Basis handeln |
| Unknown-Stop/Equity/Brokerzustand | Neue Risikoerhöhung sperren, Betreuung fortsetzen | Kein Erfolg aus fehlender Information ableiten |
| Manuelle Fremdpositionen | Keine automatische Änderung ohne Ownership/Freigabe | Schutzmodule dürfen nicht fremde Entscheidungen überschreiben |
| Erlaubte Dynamik | Zuerst kleiner Parameter-/Regimefilter, dann bewiesene Strategiewahl | Weniger Freiheitsgrade, bessere statistische Beurteilbarkeit |
| Rolle des LLM | Hypothesen/Erklärungen, strukturierte Vorschläge | Deterministische Grenzen und Nachweisbarkeit bleiben erhalten |
| Erstfreigabe Live | Explizit menschlich, eng begrenzter Scope | Forschungserfolg allein genügt nicht |
| Historische Altreports | Weiter lesbar, ungebunden/veraltet markieren | Rückwärtskompatibel und wahrheitsgemäß |
| Neue Datenquellen/Modelle | Erst bei nachgewiesenem Nutzen | Keine angrenzende Plattform neu bauen |

Später nötige Informationen: bevorzugte Assetuniversen/Handelshorizonte, tatsächlich aktive Modi, Umgang mit manuellen Positionen, Kapital-/Risikotoleranz, verfügbare Historie und Sandboxmöglichkeiten. Keine Zugangsdaten erneut in den Plan oder Chat kopieren.

## 4. Wissenschaftliche Einordnung

### Was ein Regime ist — und was nicht

Regime sind Modellkategorien zur Beschreibung bedingter Marktverteilungen. Eine nachträglich schön segmentierte Kurve ist nicht automatisch eine vorher handelbare Zustandsklassifikation. Ein Indikator erklärt Historie nicht deshalb kausal, weil seine Linie glatt aussieht.

Gute Forschung trennt:

- **Beschreibung:** Phasen, Charakteristika und rückblickende Referenzen.
- **Prognose:** Welche Information stand bei der Entscheidung zur Verfügung?
- **Entscheidung:** Verbessert die bedingte Strategie nach Kosten und Risiko die Ergebnisse?
- **Ausführung:** War der modellierte Trade mit realer Liquidität/Orderlogik überhaupt möglich?

Ein Detektor kann seine Labels korrekt/kausal berechnen und trotzdem keinen Tradingnutzen haben. Umgekehrt kann ein einfacher grober Kontextfilter sinnvoll sein, ohne besonders schöne Final-Übereinstimmung zu liefern.

### Suchvielfalt und Unabhängigkeit

Wer viele Indikatoren, Schwellen, Zeitfenster, Assets und Exitregeln ausprobiert, findet zwangsläufig überzeugende Zufallsergebnisse. Deshalb alle Suchversuche/Abstammungen dokumentieren, Auswahlvalidierung von finalem Test trennen und zeitliche/assetübergreifende Abhängigkeit berücksichtigen.

Ein stationäres/i.i.d.-Bootstrap über einzelne Trades kann bei Regimeclustern falsche Sicherheit geben. Block-/Session-/Episodeblöcke und paarweise gleiche Chancen sind besser begründete Ausgangspunkte. Keine einzelne statistische Kennzahl garantiert Generalisierung; reale Strukturbrüche bleiben möglich.

## 5. Fachquellen zur Vertiefung

Diese Quellen dienen der Methodik, nicht als Beleg für konkrete Codefehler. Codebefunde sind direkt am gepinnten Repository und den beigefügten Tests nachvollziehbar. Externe Referenzen wurden per Webrecherche identifiziert, die Paper nicht in dieser Analyse vollständig repliziert.

1. **Bailey, Borwein, López de Prado, Zhu – The Probability of Backtest Overfitting.** Begründung, weshalb die Auswahl vieler Backtests außerhalb der Stichprobe scheitern kann; PBO/CSCV.  
   https://www.davidhbailey.com/dhbpapers/backtest-prob.pdf
2. **Bailey, López de Prado – The Deflated Sharpe Ratio.** Korrektur für Selektionsbias und nichtnormal verteilte Renditen; keine Aufforderung, einen Sharpegrenzwert ungeprüft zur Livefreigabe zu machen.  
   https://davidhbailey.com/dhbpapers/deflated-sharpe.pdf  
   https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2460551
3. **scikit-learn: TimeSeriesSplit.** Zeitgeordnete Splits und `gap`; wichtig: der Splitter kennt nicht automatisch Label-/Positionsüberlappung, Assetasynchronität oder reale Informationsverfügbarkeit.  
   https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.TimeSeriesSplit.html
4. **Gepinnte Quellbasis:**  
   https://github.com/dean06greif-ai/KI-Trader/tree/792ff0ac44861df447d3e619042ecef1704c37c5

Nicht empfohlen: allein aufgrund dieser Links neue Bibliotheken installieren oder die vorhandene Engine ersetzen. Erst den fachlichen Vertrag und kleine Goldenszenarien stabilisieren; dann ein passendes geprüftes Werkzeug auswählen.

## 6. Schlussbewertung

Die erfolgversprechende Weiterentwicklung ist nicht „noch mehr KI“. Sie ist eine **einheitliche, überprüfbare Verbindung zwischen Daten, Experiment, Strategieversion, Freigabe, tatsächlichem Fill und daraus gelerntem Ergebnis**. Die vorhandene Architektur bietet genug Material dafür. Die richtigen nächsten Schritte sind kleine fachlich abgesicherte Korrekturen, danach ein begrenzter Laborpilot mit der ehrlichen Möglichkeit, die statische Basis als bessere Lösung beizubehalten.