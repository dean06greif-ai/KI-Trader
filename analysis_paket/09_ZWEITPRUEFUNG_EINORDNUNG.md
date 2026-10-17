# 09 – Unabhängige funktionale Zweitprüfung und Einordnung

Eine separate funktionale Codeprüfung wurde auf derselben Quellkopie angefordert. Sie war **read-only**, kein Sicherheitsaudit, ohne Produktionsdaten oder Brokerzugriff. Schwerpunkt waren KI-Entscheidung, Orderausführung und Backtest-/Live-Abweichung. Dies ist eine redaktionelle Zusammenfassung und Nachprüfung, kein unverändertes Volltranskript des Agentenoutputs.

## Wesentliche zusätzliche Hinweise

| Hinweis der Zweitprüfung | Eigene Verifikation | Finaler Umgang |
|---|---|---|
| Watchdog kann fremde gleichseitige Vollposition nach kürzlichem Bot-Close schließen | Quellpfad geprüft; AST-extrahierte Originalmethode mit anderer Positions-ID/Qty ausgeführt | T01, P0, Originalfunktionsnachweis |
| Timeout-Recovery übernimmt geplante Qty bei nur teilweiser Börsenmenge | Marketbranch gelesen, gegenüber Maker differenziert; schließlich Originalbranch per AST geprüft | T02, P0, begrenzter Branchnachweis |
| Stop-None wird nicht als missing markiert | Original-If-Branch mit None/True/False geprüft | T03, P0; dreistufiger Status statt blindem Booleanfix |
| Backtest-Reifeboost und Ausführungsmodell sind nicht gleichbedeutend mit realer Live-Reife | `setup_backtest/weights.py`, `policy_promotion.py`, Setup-Gate gelesen | W02/T04: Vorhandene Caps/Mindest-Paperbasis ausdrücklich anerkannt; statistisches Risiko statt pauschal fehlender Guards |

## Warum die Zweitprüfung nicht unverändert als Wahrheit übernommen wurde

- Ein Codepfad kann gute Vorbedingungen haben, die das Fehlerfenster begrenzen. Beispielsweise prüft der Watchdog zunächst eine Entry-Registry; dies wurde im Finalbefund berücksichtigt.
- Ein möglicher Fehler ist kein Beleg real eingetretener Verluste. Ohne tatsächliche Konfiguration/Fills bleibt die Produktivhäufigkeit unbekannt.
- Der im Code beschriebene Segment-End-Close existiert nicht in der tatsächlich aufgerufenen Simulation. Eigene Prüfung und numerische Tests hatten Vorrang vor Kommentaren.
- Teilweise oberflächen-/sandboxbezogene Aussagen sind nicht belastbar ohne die tatsächliche externe Anwendung. Die unbenutzte Startervorschau wurde ausdrücklich aus dem Produktbefund ausgeschlossen.
- Testerempfehlungen, jetzt Codefixes vorzunehmen, wurden **nicht ausgeführt**, weil der Nutzer nur Analyse und Plan freigegeben hat.

## Lieferumfang nach Nachprüfung

Der finale Katalog enthält getrennte Evidenzgrade. Die Tests liegen außerhalb des Quellrepositories. Die Quellkopie entspricht weiterhin dem gepinnten Commit. Weitere Analysedateien können für die nächste KI direkt als Arbeitsgrundlage genutzt werden, ohne Zugangsdaten oder produktive Konfigurationen zu übertragen.