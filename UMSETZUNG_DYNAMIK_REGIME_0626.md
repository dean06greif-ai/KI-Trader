# Umsetzung 06/2026 – Dynamische Strategien & Regime (zu ANALYSE_DYNAMIK_REGIME_0626.md)

Alle Änderungen sind modular und rückwärtskompatibel: Gespeicherte Analysen und Strategien bleiben gültig, und neue Schutzfilter sind standardmäßig AUS.
Die neue **Bewertung** gilt nur für neue Läufe. Umschaltbar im Regime-Lab (Abschnitt 0) oder per `POST /api/research-mode`.

| Etappe | Was | Code |
|---|---|---|
| E1 | Statistik-Kern: Glaubwürdigkeit `n/(n+20)`, Shrinkage Richtung Gesamt-Ø, Untergrenze Ø − 1 SE, Bootstrap P(dyn > stat), Größen-Faktor | `services/evidence_stats.py` |
| E1 | Robuste Validierung je Regime (≥ 5 Trades **und** Untergrenze > 0 statt „3 Trades im Plus“) | `dynamic_strategy.validation_passed(mode)` |
| E1 | Robustes Urteil dynamisch vs. statisch (≥ 15 Test-Trades, P ≥ 80 % für „besser“ und „> 0“) | `dynamic_strategy.build_verdict(mode=…)` |
| E1 | Regime-Evidenz abgestuft: ab 10 Trades, gewichteter Ø > 0, `size_factor` je Regime | `regime_evidence.evaluate(mode="robust")` |
| E1 | Freigabe nutzt den Modus des jeweiligen Walk-Forward-Laufs (alte Läufe = klassisch) | `strategy_release.py` |
| E2 | Zeitliche Validierung: ein gemeinsamer Zeitschnitt über alle Phasen + 24 Kerzen Embargo | `dynamic_strategy.split_segments(mode="time")` |
| E2 | Ziel „Robust“ (PnL ohne die 2 besten Trades) in Optimizer und Dynamik | `_score`, `optimizerOptions.js` |
| E3 | Makro × Haupt-Regime je Anlageklasse (Krypto 1D × 8h, Indizes/Rohstoffe 1W × 1D, Forex 1D × 4h), Matrix, BTC-Leitmarkt | `services/regime_hierarchy.py` |
| E4 | Nutzen-Note statt Rückblick-Treffer: Trennschärfe der Rendite danach, Vorzeichen-Treffer, Phasendauer, Ping-Pong | `services/regime_usefulness.py` |
| E5 | Regime-Assistent: ein Knopf je Anlageklasse, Ampel, optional Struktur-Regime für den KI-Trader ohne Lab-Freigabe | `services/regime_assistant.py`, `routers/regime_assistant.py`, `RegimeAssistant.js` |
| E6 | Je dynamischer Strategie: Makro-Filter und Übergangsschutz (Regime-Sicherheit), dazu eine empfohlene Größe | `strategies/dynamic_regime_strategy._apply_guards`, `DynamicGuardPolicy.js` |

Tests:
- Neu: `tests/test_evidence_robust_0626.py` und `tests/test_regime_hierarchy_assistant_0626.py` (33 Tests).
- Regression: 515 Tests rund um Dynamik, Regime, Optimizer und Freigabe laufen grün.
- Einziger Fehler: `test_autopilot_best_score_never_drifts_down`. Er schlug schon **vor** den Änderungen fehl.

Bekannte Grenzen:
- Der Makro-Filter wirkt nur live, Backtests berücksichtigen ihn nicht. Deshalb erst im Paper prüfen.
- Der Größen-Faktor wird nur angezeigt. Die Positionsgröße bleibt in der Hand des Nutzers (Blitz-Einstellungen).
