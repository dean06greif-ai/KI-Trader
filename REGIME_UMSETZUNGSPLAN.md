# Umsetzungsplan Regime-Erkennung (Stand 24.09.2026)

Ziel: die präziseste, für Daytrading-Strategiewechsel NUTZBARE Regime-Erkennung finden – ehrlich gemessen,
schnell gesucht, sauber in die bestehende Architektur (services/regime_*, routers/regime_lab.py, RegimeLab-UI).
Fortschritt: siehe `REGIME_FORTSCHRITT.md` (wird nach jedem Schritt aktualisiert).

## Einordnung der Messwerte (Klarstellung)
| Kennzahl | Krypto 1h | Bedeutung |
|---|---|---|
| Live=Final | 93 % | Selbst-Übereinstimmung (live vs. rückblickend eigene Phasen) – sagt nichts über Richtigkeit |
| Roh-Treffer vs. Referenz v2 | **66 %** | Anteil Kerzen mit richtiger Richtung (auf/seit/ab) |
| „immer seitwärts“ | 73 % | triviale Vergleichsbasis (Referenz ist zu ~72 % seitwärts) |
| Balanciert | 43 % | Mittel der Treffer JE Richtung (konstant = 33 %) |
| Macro-F1 / κ | 44 % / 12 | verpasste + falsche Trends bestraft; κ 0 = Zufall, 20–40 = mäßig |

Die Erkennung ist bei **großen Trends (Wochen) gut** (Lag nur ~2 Tage, Aufwärts-/Abwärtsphasen werden erkannt),
bei **kurzen Richtungswechseln (4–14 Tage) zu träge**: ~40 % dieser Phasen werden nicht erkannt.

## Phase 1 – Messung & Suche (Fundament) 
1.1 ✅ Referenz v2 (festes Fenster 7 d, Merge 2 d), Skill, balanciert, Richtungs-Phase
1.2 ✅ Macro-F1 + Cohens κ als Note/Score-Basis
1.3 ✅ Crash-Wick-Endlosschleife im Pivot-Scan behoben
1.4 **Regime-Nutzen (ökonomischer Benchmark)**: je Live-Richtung die Kursentwicklung danach
    (Forward-Return 1 d / 3 d, in % und ATR), Trennschärfe = Ø(auf) − Ø(ab), Vorzeichen-Treffer.
    Datei `services/regime_utility.py` (rein), eingebunden in `_symbol_payload` → Referenz-Dict → Qualitätskarte,
    Autopilot-Kennzahlen, Ablation-Zeilen.
1.5 **Autopilot schneller & gezielter**: (a) Duplikat-Cache (gleiche Konfiguration nicht erneut bewerten),
    (b) Plateau-Stopp (Standard 300 Runden ohne Verbesserung; einstellbar), (c) Suchraum erweitert
    (`kombi_ema_days` ab 3, `kombi_dominance_days` ab 0.5, `ema_regime_days` ab 2), (d) Neustart aus einer
    zufälligen Top-10-Variante bei langer Stagnation (gegen lokale Optima).
1.6 Tests + Fortschrittsdatei + Bericht.

## Phase 2 – Präzision der Erkennung
2.1 **Höherer-TF-Filter**: Richtung zusätzlich auf 4h/1d prüfen (kausal, aus den gleichen Kerzen aggregiert);
    Trend nur, wenn beide nicht widersprechen – optionaler Detektor-Schalter `htf_confirm` + Autopilot-Suchraum.
2.2 **Richtungs-Modus (3 Regime) als Standard für Freigabe**: weniger Regime → 90 statt 270 Shadow-Trades,
    Vola-Stufen nicht mehr als Phasenwechsel gezählt (9er-Modus bleibt wählbar).
2.3 **Crash-Wick-Robustheit**: extreme Dochte (> 8×ATR) für Extrem-/Pivot-Suche kappen + Hinweis je Coin.
2.4 **Gespeicherte Analysen neu bewerten** (Referenz v2/F1/Nutzen) per Knopf, lokal auf dem Worker.

## Phase 3 – Zusätzliche Informationen (Krypto)
3.1 Funding-Rate & Open-Interest (Bitunix/Binance öffentlich) als Bestätigung/Filter für Trendphasen.
3.2 Markt-Breite: Anteil der Coins im Aufwärtstrend (kombiniertes Modell) als Regime-Merkmal.

## Phase 4 – Automatisierung
4.1 Autopilot → Analyse → Ablation (lokal) als Kette per Schalter.
4.2 Kombi-Kalibrierung auf Referenz v2 / F1 umstellen.

## Erfolgskriterien
- Holdout Macro-F1 ≥ 50 % und κ ≥ 25 (heute 44 / 12) bei Ø Richtungs-Phase 4–14 d
- Regime-Nutzen: Ø 3-Tage-Rendite in „auf“ klar > „ab“ (Trennschärfe > 0 in ≥ 70 % der Coins)
- Autopilot-Lauf ≤ 30 min statt 2–8 h bei gleicher oder besserer Güte
