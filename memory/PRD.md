# PRD – externe KI-Trader-Planprüfung

## Originalauftrag und Nutzerentscheidungen
Bestehende produktive externe Daytrading-Website prüfen: https://github.com/dean06greif-ai/KI-Trader/tree/conflict_160926_0902. Verbesserungsplan für Regime Lab und KI-Trader sei fertig implementiert; prüfen, ob korrekt umgesetzt und sinnvoller, kurze Erklärung von Regime Lab/dynamischen Strategien, priorisierte verbleibende Probleme. Stabilität, Rückwärtskompatibilität, Originalstruktur und externer Betrieb bleiben erhalten.
Nutzer bestätigt: Plan im Repo suchen; gründliches Review, nur kleine eindeutig bestätigte Fehler beheben, nichts Großes umbauen; produktive Dienste nicht anfassen, 110-Credits-Budget beachten.
Vom Nutzer mitgesendete Secrets absichtlich nicht gespeichert oder verwendet; Rotation empfohlen.

## Architekturentscheidungen
- Kein App-Neubau/Redesign/Deployment. Isolierter Clone unter `/app/review/KI-Trader`, Commit `50dc7b2657820898a63fc00d85c48d66f9975bc1`.
- Original FastAPI/services/routers/core, React-Frontend, Mongo und externer Worker bleiben strukturell unverändert.
- Offline-Tests mit FakeDB/Fake-Broker, ohne Server-Lifespan. Separate Test-venv `/app/review/test-venv`, echte Telegram-Bibliothek 22.8; keine Änderung produktiver Dependencies/.env.
- Read-only funktionales Code-Review plus zwei Testing-Agent-Läufe. Kein Security-Audit angefragt/ausgeführt.

## Persona / statische Anforderungen
Einzelner Betreiber einer extern stabil laufenden Trading-Website. Benötigt ehrliche Validierung, Schutz bestehender Workflows und nachvollziehbare Entscheidungen statt Vollständigkeits-/Renditeversprechen.
Kein produktiver Zugriff, keine Trades, keine Migrationen, keine Großrefactorings. Lokale kleine Änderungen müssen mit Regressionen belegt sein.

## Umgesetzt – 16.09.2026
- Plan AP00–AP13, PROGRESS und tatsächliche Aufrufpfade abgeglichen. Ergebnis: echte Teilverbesserungen, wesentliche Abnahmelücken trotz Vollständigkeitsbehauptung.
- Kleiner Fix in `position_watchdog.py`: ohne bekannte identische Positions-IDs kein heuristischer Restclose mehr.
- Kleine reine Evidence-Fixes in `strategy_release.py`: kein beliebiger Coin-WF-Fallback, scopebezogener Validierungsstatus, fehlende Health blockiert Empfehlung.
- Bestandstests angepasst, elf neue Review-Fälle (teils Charakterisierung offener Fehler). Abschluss 191 passed, 1 strict-xfail Confirm-Race. Report `/app/test_reports/iteration_2.json`, JUnit `/app/test_reports/pytest/iteration2_analysis_regression.xml`.
- Frühere breite Unit-Auswahl nicht vollständig grün: 10 lokale Server-Fehler, 4 Collection-Fehler, 12 skips. Keine produktiven Fehler daraus behaupten. Mongo-Iter46-Fixtures nur statisch geprüft.
- Detaillierter deutscher Bericht `REVIEW_REGIME_KI_TRADER.md`, PROGRESS um Nachprüfung ergänzt; kleines Übernahmepaket mit relativer Originalstruktur vorgesehen.

## Priorisierter Backlog / nächste Aufgaben
### P0 Betreiberaktion
Offengelegte Secrets erneuern; keine Werte aus dem Chat in Artefakte übernehmen.
### P1 (ausdrücklich NICHT im kleinen Fixauftrag behoben)
- Evidenzgebundene Freigabe statt approve-Bypass; vollständiger Modell-/Dataset-/Definitionshash.
- Persistenter atomarer Confirm-Claim mit Retry-/Crash-Vertrag (strict-xfail bleibt offen).
- Eindeutige Trade-/Dynamic-/Mode-Ownership; vollständiges Order-/Fill-/Schutz-/Reservierungsmodell.
- Kombi-Ranking nutzt weiterhin Holdout-Segmentdauer; Fallback direction_pct ist Gesamtmetrik. Innere Auswahl strikt isolieren und Regressionen ergänzen.
- Discovery-Regelsätze tatsächlich im Scanner konsumieren; Simulation/Runtime dieselbe Definition.
- Persönliche Ursprungswerte nach dynamischer Überschreibung restaurieren.
- Freigegebenes gemeinsames Regime-Artifact statt separatem Gate-Modell.
### P2
- Policy-Artifact tatsächlich übergeben, neuer Worker-Vertrag strikt erzwingen; Test-Hygiene.
- Paper-/Shadow-Pilot mit eingefrorenen Parametern und statischer Benchmark, dann erst weitere Forschung/Modulbereinigung.

## Grenzen
Kein Browser-/Broker-/Produktions-/Worker-Lauf geprüft, keine Profitabilitätsaussage. Zwei Fachdateien lokal geändert, externe Website und GitHub unverändert. Bericht unterscheidet Laufzeitbelege, statische Datenpfade und offene Vertragsrisiken.
