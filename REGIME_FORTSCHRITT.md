# Fortschritt Regime-Erkennung

> Wenn der Chat abbricht: hier steht der genaue Stand. Nächster Schritt = erster offener Punkt unten.
> Plan: `REGIME_UMSETZUNGSPLAN.md` · Befunde: `REGIME_LAB_PRUEFBERICHT_2409.md`

## Erledigt
- [x] 24.09. Referenz v2, Skill, balanciert, Richtungs-Phase (regime_reference, regime_truth, regime_quality)
- [x] 24.09. Macro-F1 + Cohens κ (Note, Autopilot-Score, Ablation, Freigabe-Delta)
- [x] 24.09. Crash-Wick-Endlosschleife Pivot-Scan (regime_kombi, regime_reactive) – DOT 70 d → 0,6 d
- [x] 24.09. Hinweis „alte Referenz v1“ auf der Qualitätskarte

## In Arbeit / offen (Reihenfolge)
- [x] 1.4 Regime-Nutzen (services/regime_utility.py → Referenz-Dict, Qualitätskarte „Regime-Nutzen / Richtung bestätigt / Seitwärts-Ruhe“, Autopilot-Kennzahlen)
- [x] 1.5 Autopilot: Duplikat-Cache, Plateau-Stopp (UI-Feld, Standard 300), erweiterter Suchraum (kombi_ema ab 3, dominance ab 0.5, ema ab 2), Top-10-Neustart alle 120 stagnierende Runden
- [ ] 1.6 Tests + Testing-Agent
- [ ] 2.1 Höherer-TF-Filter
- [ ] 2.2 Richtungs-Modus als Freigabe-Standard
- [ ] 2.3 Crash-Wick-Robustheit
- [ ] 2.4 Analysen neu bewerten (Knopf)
- [ ] 3.x Funding/OI, Markt-Breite
- [ ] 4.x Kette Autopilot→Analyse→Ablation, Kombi-Kalibrierung v2

## Was DU danach tun musst
1. Deploy auf Render (Branch pushen) + Worker-Paket ≥ 1.14.0 neu herunterladen
2. Autopilot neu starten (1h, Krypto-Kern, Ziel 4–14 d) – alte Scores nicht mit neuen vergleichen
3. Analyse ausführen → Qualitätskarte: Macro-F1, κ, Regime-Nutzen prüfen

## Log
- 24.09. 1.4 + 1.5 umgesetzt, Unit-Tests grün (tests/test_pruefung_2409.py 28), Build ok – nächster Schritt 1.6 Testing-Agent
- 24.09. Plan erstellt, Start Phase 1.4
