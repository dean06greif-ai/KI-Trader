# Regime-Lab: unabhängige Prüfung (28.09.2026): Sinnhaftigkeit, Benchmarks, 5 vs. 9 Regime

Basis: Branch `conflict_250926_0712`. Die Produktiv-DB wurde **nur lesend** genutzt, Kerzen kamen von der öffentlichen Bitunix-API.
**Am Code wurde nichts geändert.** Geprüft habe ich die Prüfberichte 23.09., 24.09. und 25.09., `REGIME_LAB_KLARHEIT_UND_KALIBRIERUNG.md`,
`analysis_paket/`, die Module `regime_jump / regime_reference / regime_truth / regime_utility / regime_autopilot /
regime_release` sowie alle 6 gespeicherten Analysen.
Aktuellste Analyse: **`ra_e866c510` „Regime Krypto 1h“ (25.09., 11:40)**, 13 Coins, 720 Tage, 1h, **Detektor `kombi`,
9er-Modus**, Konfiguration aus dem Autopilot-Lauf `8d76b4cfa6f2`.

Eigene Nachmessung (Skripte in `/app/research/`: `regime_study.py`, `effn.py`, Daten `c1h_1080.pkl`):
- Die Detektoren werden genau so aufgerufen, wie das Lab es tut (`rg.detect_regimes` → `eng.reactive_payload` → Live-Labels).
- Verglichen wird gegen Referenz v2 (`centered_labels`, 7 Tage, Mindestlänge 2 Tage).
- Dazu kommen Zusatzprüfungen, die das Lab heute nicht macht: eine triviale Vergleichsregel, rollierende 90-Tage-Fenster, die Kursentwicklung
  nach jeder erkannten Richtung (Block-Bootstrap mit 90 %-Intervall) und die Frage, ob eine Strategie je nach Regime unterschiedlich gut funktioniert.

---

## 1. Kurzfazit

1. **Die Technik ist sauber.** Kein Lookahead, das Holdout-Leck ist behoben, Referenz v2 ist fair, und Live- und Lab-Labels sind gleich.
   Die Prüfberichte vom 23. bis 25.09. waren fachlich richtig.
2. **Die Benchmark-Zahlen sind reproduzierbar, bedeuten aber weniger als gedacht.** Das Jump-Modell erreicht bei mir einen Holdout-Macro-F1 von
   ≈ 60–61 (Bericht: ≈ 61). Ein „gut“ heißt aber nur, dass der Detektor einem Rückblick-Etikett ähnelt. Es heißt nicht,
   dass man darauf gewinnbringende Strategien bauen kann.
3. **Das wichtigste Problem:** Die erkannte Richtung hat über die Zeit **keinen stabilen Vorhersagewert**. Auch ob eine Strategie
   in einem Regime funktioniert, **kippt zwischen Trainings- und Testzeitraum** (Belege in Abschnitt 3). Auf dieser Basis „einfach Strategien je Regime“ zu bauen,
   würde mit hoher Wahrscheinlichkeit Zufallsmuster übernehmen.
4. **Die aktuellste Analyse nutzt nicht den besten Detektor.** Sie läuft mit `kombi`, nicht mit `jump`, und hat alle 9 Regime „behalten“, obwohl
   2 davon im Training praktisch nur **0–1 echte Marktphasen** haben.
5. **5 oder 9 Regime?** Das macht einen **großen Unterschied**. Für Strategien sind **3 Richtungen (oder 5 mit Stärke) plus Volatilität als
   Risiko-Stellschraube** klar besser als 9 Regime (Abschnitt 5).

---

## 2. Die aktuellste Analyse (25.09.) in Zahlen

| Kennzahl (Holdout, Mittel über 13 Coins, DB) | Wert | Einordnung |
|---|---|---|
| Live=Final | 92,6 % | nur Selbst-Übereinstimmung, keine Aussagekraft |
| Referenz v2 Macro-F1 | 60,9 (53,0 – 69,2) | „gut“ nach Lab-Schwelle ≥ 55 |
| Cohens κ | 38,6 | „mäßig“ |
| Skill über „immer seitwärts“ | +7,5 % (−7,7 bis +26) | **schwach**: die rohe Trefferquote liegt kaum über der trivialen Baseline |
| Ø Richtungs-Phase | 5,2 Tage (Referenz ≈ 10 Tage) | im Ziel 4–14 Tage, aber doppelt so viele Wechsel wie die Referenz |
| Ø Lag / verpasste Phasen | 1,7 Tage / 12,3 % | gut |
| **Regime-Nutzen Training (3 Tage), Vorzeichen-Treffer** | **38,6 – 51,9 %, Ø 47 %** | **schlechter als ein Münzwurf** |
| Regime-Nutzen Holdout (3 Tage) | Ø 48,8 %, Streuung der Trennung −3,6 bis +4,1 % | uneinheitlich |

Auffällig: Der **Holdout ist bei fast allen Coins besser als das Training** (BTC: Training-F1 58, Holdout 68). Das spricht für einen
„leichten“ Testzeitraum (klare Abwärtsbewegung März–Sept. 2026) und nicht für ein besseres Modell.

---

## 3. Befunde

### B1 (hoch): Referenz-F1 misst Ähnlichkeit zu einem Etikett, nicht Nützlichkeit
- Referenz v2 ist die vola-normierte Drift über ein zentriertes 7-Tage-Fenster. Das Jump-Modell benutzt vola-normierte EWMA-Drift.
  Beide messen fast dieselbe Größe, einmal mit Blick in die Zukunft und einmal ohne. Ein hoher F1 heißt also:
  „kommt einem Rückblick-Etikett nahe“.
- Das Fenster (7 Tage, Mindestlänge 2 Tage) wurde so gewählt, dass Phasen von ≈ 10 Tagen herauskommen, also passend zum Ziel 4–14 Tage.
  Das ist legitim, aber **eine Definition und keine Wahrheit**.
- Das ist kein Fehler im Code, sondern eine Grenze der Kennzahl. Die Oberfläche zeigt den F1 aber als Hauptnote („gut ≥ 55“).

### B2 (hoch): Eine 3-Zeilen-Regel erreicht fast dieselbe Benchmark-Note
Die Regel „Vorzeichen der vola-normierten 7-Tage-Rendite, Schwelle 0,9“ ist kausal und hat keine Parameter-Suche:

| Detektor | Holdout-F1 720 d | Holdout-F1 1080 d | **Median-F1 aller 90-Tage-Fenster** | schlechtestes 90-Tage-Fenster |
|---|---|---|---|---|
| jump (Standard) | 60,3 – 61,0 | 60,6 | **53,1 – 55,4** | 39,5 – 44,0 |
| kombi (Autopilot 25.09.) | 56,7 | 57,3 | **52,6 – 53,1** | 38,6 – 40,3 |
| ema thr 0,34 | 43,0 | 46,5 | 38,5 | 28,0 |
| **naive 7-Tage-Regel** | 53,3 | 56,0 | **50,6 – 51,5** | 39,0 – 40,2 |

→ Über rollierende Fenster sind jump, kombi und die naive Regel **fast gleich gut** (Median ≈ 51–55). Der Abstand zeigt sich vor allem
im aktuellen Holdout. Die naive Regel flackert zwar stark (Ø Phase 0,9 Tage), aber das zeigt: Das meiste vom F1 ist
„Rendite der letzten Woche“. Der Benchmark hat **keine Vergleichsspalte gegen eine triviale Regel**.

### B3 (kritisch für Strategien): Die Richtung hat keinen stabilen Vorhersagewert
13 Coins zusammen, Kursänderung 3 Tage nach der Kerze, 90 %-Intervall per Block-Bootstrap (10-Tage-Blöcke):

| | nach „ab“ (Training) | nach „ab“ (Holdout) | nach „auf“ (Training) | nach „auf“ (Holdout) | Long/Short-Regime-Strategie Sharpe Training → Holdout |
|---|---|---|---|---|---|
| jump, 1080 d | **+1,19 %** [+0,74; +1,60] | −2,16 % [−2,93; −1,44] | +1,43 % | +0,83 % | −0,26 → +1,14 |
| kombi, 1080 d | **+1,00 %** [+0,61; +1,40] | −1,51 % | +1,30 % | −0,04 % | +0,10 → +0,93 |
| kombi, 720 d | +0,46 % | −0,29 % | +0,67 % | +0,57 % | −0,10 → +0,60 |

- Im Training stieg der Kurs **nach einem erkannten Abwärtstrend signifikant**. Die Richtung kehrte sich also um, statt weiterzulaufen.
  Im Holdout lief die Richtung weiter. **Das Vorzeichen kippt.**
- Der Vorzeichen-Treffer nach 3 Tagen liegt im Training bei allen Detektoren bei 47–49 %.
- Die gute Holdout-Performance kommt damit aus dem Marktumfeld des Testzeitraums, nicht aus der Erkennung.

### B4 (kritisch für Strategien): Wie gut eine Strategie in einem Regime läuft, kippt zwischen den Zeiträumen
Probe mit einer einfachen Trendfolge-Regel (Vorzeichen der letzten 24 h → nächste 24 h), Ergebnis je Regime:

| Detektor | „ab“ Training | „ab“ Holdout | „seitwärts“ Training | „seitwärts“ Holdout |
|---|---|---|---|---|
| jump 1080 d | **−0,69 %** [−0,85; −0,51] | **+0,68 %** [+0,45; +0,96] | +0,10 % [+0,04; +0,16] | −0,08 % [−0,15; −0,01] |
| kombi 1080 d | −0,53 % [−0,71; −0,37] | +0,60 % [+0,38; +0,86] | +0,06 % | −0,09 % |

→ Eine im Training gelernte Zuordnung wie „im Abwärts-Regime keine Trendfolge“ wäre im Holdout **genau falsch** gewesen, und das bei
statistisch deutlichen Effekten in **beide** Richtungen. Das ist das größte Risiko für „Regime → Strategie“.

### B5 (hoch): Der 9er-Modus hat zu wenige unabhängige Marktphasen, und die Sperre dagegen greift nicht
Die Coins bewegen sich gemeinsam: Die Richtung stimmt zu **76–81 %** mit BTC überein. Unabhängige Marktphasen im Training (540 Tage),
gezählt als „mindestens die Hälfte der Coins im selben Regime“:

| Regime (9er) | 0 ab·ruhig | 1 ab·mittel | 2 ab·volatil | 3 seit·ruhig | 4 seit·mittel | 5 seit·volatil | 6 auf·ruhig | **7 auf·mittel** | **8 auf·volatil** |
|---|---|---|---|---|---|---|---|---|---|
| kombi 25.09. | 7 | 6 | 10 | 23 | 21 | 11 | 8 | **1** | **0** |
| jump | 6 | 3 | 5 | 15 | 11 | 7 | 6 | **1** | **1** |

- `regime_release.MIN_SEGMENTS_PER_REGIME = 5` zählt die **Abschnitte aller Coins zusammen**. Regime 8 kommt so auf 32–95 „Abschnitte“, obwohl es
  im Kern nur 0–1 Marktereignisse gibt. Deshalb hat die Analyse vom 25.09. **alle 9 Regime behalten**.
- Im 5er-Modus hat jedes Regime 13–37 Marktphasen. Das ist deutlich belastbarer.

### B6 (mittel): Die zweite Achse (Vola bzw. Stärke) wird von keinem Benchmark geprüft
- F1, κ, Lag und Nutzen werten **nur die Richtung** aus. Ob die Unterteilung in ruhig/mittel/volatil oder leicht/stark irgendetwas taugt, misst das Lab nicht.
- Meine Messung: Die **Vola-Stufe sagt die künftige Volatilität tatsächlich voraus** (3 Tage danach: ruhig 3,8 %, mittel 4,4 %, volatil 4,7 %
  Tagesvola; im Holdout 2,8 / 3,2 / 3,7 %). Sie eignet sich also für **Positionsgröße und SL-Abstand**, aber nicht für die Richtung.
- Die Stärke-Stufe im 5er-Modus (nur ≈ 8–9 % „stark“) hat keinen stabilen Richtungsvorteil. Sie steht vor allem für höhere Vola.

### B7 (mittel): Nur ein Holdout, der mehrfach angeschaut wird
- 75/25-Split: ein einziger Testzeitraum von 180 Tagen mit ≈ 18–30 Richtungsphasen. Die Unsicherheit des F1 liegt realistisch bei ±5–8 Punkten.
- Mehrere Autopilot-Läufe mit Blick auf denselben Holdout führen dazu, dass **du selbst** den Holdout mitoptimierst (Leck durch den Menschen).

### B8 (niedrig): Konfidenz ist gegen Live=Final kalibriert
Konfidenz-Bins unter 50 % treffen in 80 % der Fälle (`score_is_calibrated_probability: false`). Die Konfidenz ist damit als Wahrscheinlichkeit
für Strategie-Gates nicht verwendbar.

### B9 (niedrig): Datenlage
Auf Bitunix gibt es für BNB (≈ 510 Tage), HYPE (≈ 550 Tage) und POL (≈ 735 Tage) weniger Historie. In den 1080-Tage-Läufen fallen sie weg.

---

## 4. Sind die Benchmarks akkurat? Ist „sehr gut“ wirklich sehr gut?

- **Rechnerisch ja.** Die Zahlen im Bericht vom 25.09. und in der DB lassen sich nachrechnen. Kleine Abweichungen kommen daher, dass ich direkt 1h-Kerzen
  geladen habe statt aggregierter 1m-Kerzen.
- **Inhaltlich nein.** „gut“ (F1 ≥ 55) heißt: kommt einem 7-Tage-Rückblick-Etikett mäßig nahe (κ 30–40). Das ist etwa so gut
  wie eine triviale Momentum-Regel, und die Note hängt stark vom Zeitraum ab (90-Tage-Fenster zwischen 39 und 67).
- **Für „Erkennung perfekt, ich kann easy Strategien bauen“ reicht das nicht.** Das Regime-Lab ist heute ein gutes
  **beschreibendes** Werkzeug („wo stehen wir gerade“). Ein belegter **Vorhersage- oder Umschalt-Vorteil** fehlt noch (B3, B4).

---

## 5. Lieber 5 oder 9 Regime?

**Das macht einen großen Unterschied. Meine Empfehlung: kein 9er-Modus für Strategien.**

| | 3 (Richtung) | **5 (Richtung + Stärke)** | 9 (Richtung × Vola) |
|---|---|---|---|
| Unabhängige Marktphasen je Regime (540 Tage Training) | viele | 13–37 | **0–23, zwei Regime ≤ 1** |
| Benötigte Historie live (Bericht 23.09.) | ≈ 120 Tage | ≈ 120 Tage | ≈ 310–400 Tage |
| Richtungs-Benchmark | identisch | identisch | identisch |
| Nutzen der 2. Achse | – | Stärke: kein stabiler Vorteil | Vola: sagt künftige Vola voraus → Risiko |
| Gefahr, Strategien auf Zufall anzupassen | gering | mittel | **hoch** |

**Empfehlung (Architektur):** Die beiden Achsen **trennen**.
- **Strategie-Auswahl** nur über die **Richtung**: 3 Zustände, oder 5, wenn die Stärke später einen Nutzen nachweist.
- **Vola-Stufe** nicht als eigenes Regime verwenden, sondern als **Risiko-Parameter** (Positionsgröße, SL/TP-Abstand, Hebel).
- So nutzt du den echten Vorteil der Vola-Achse, ohne die Stichprobe durch 9 Töpfe zu teilen. Aus 3×3 werden
  3 Strategie-Töpfe plus eine stufenlose Risiko-Skalierung.

---

## 6. Verbesserungsvorschläge (priorisiert, noch nicht umgesetzt)

| # | Vorschlag | Aufwand | Wirkung |
|---|---|---|---|
| **P0-1** | **Walk-Forward-Benchmark:** 6–8 rollierende 90-Tage-Fenster zeigen Median, Minimum und Streuung von F1 **und** Nutzen, dazu eine **Vergleichsspalte „naive 7-Tage-Regel“** (Skill gegenüber der trivialen Regel). Die Note „gut“ gibt es nur bei Skill > 0 über **alle** Fenster. | mittel (neues reines Modul `regime_walkforward_bench.py`, additiv) | ehrliche Note, kein Glück mit dem Holdout |
| **P0-2** | **Abschnitts-Sperre korrigieren:** unabhängige **Marktphasen** zählen (Kalender-Vereinigung bzw. Mehrheit der Coins) statt der Summe je Coin, und ≥ 8 Phasen im Training verlangen. Alte Analysen bleiben lesbar, der Vorschlag ändert sich nur für neue. | klein (`regime_release.segments_per_regime`) | verhindert Strategien auf 1-Ereignis-Regimen |
| **P0-3** | **Stabilitäts-Prüfung für Strategie ↔ Regime:** Eine Zuordnung wird nur akzeptiert, wenn der Effekt in **≥ 2 getrennten Zeiträumen dasselbe Vorzeichen** hat und nach Kosten besser ist als dieselbe Strategie ohne Umschaltung (Ablation). | mittel (Erweiterung `regime_release.validate_release`) | fängt genau den Kipp-Effekt aus B4 ab |
| P1-4 | Für **neue** Analysen empfehlen: Detektor **jump** plus Modus **5 oder 3**. Der Engine-Standard `reactive` bleibt aus Kompatibilitätsgründen. | klein (UI-Vorauswahl / Autopilot-Startwert) | +3–4 F1, ruhigere Phasen |
| P1-5 | **2. Achse bewerten:** Vola-Stufe über das Verhältnis künftige Vola hoch/niedrig, Stärke über Nutzen je Stärkestufe, beides auf der Qualitätskarte. | klein | 9er/5er-Entscheid mit Beleg |
| P1-6 | **Versiegelter Test:** die letzten 60–90 Tage einmalig mit Zeitstempel auswerten, danach gesperrt. Autopilot und manuelle Auswahl sehen diese Tage nicht. | mittel | schließt das Leck durch den Menschen (B7) |
| P2-7 | Konfidenz gegen Referenz/Nutzen kalibrieren statt gegen Live=Final. | klein | Konfidenz nutzbar für Gates |
| P2-8 | Nicht-Preis-Merkmale für Krypto (Funding, Open Interest, BTC-Dominanz) als zusätzliche Achse testen. Mit reiner Preis-Erkennung ist die Obergrenze erreicht. | groß | einziger Weg zu mehr als κ ≈ 40 |

**Vorgehen bis zu echten Strategien:** P0-2 → P0-1 → neu analysieren (jump, 5er, 1080 Tage, Krypto-Kern) → P0-3 → Ablation → Shadow
→ 4 Wochen Reward-Split → erst dann dynamische Strategien. Ein legitimes Ergebnis kann auch sein: **„Regime nur für Risiko/Größe, Strategie statisch“**.

Alle Vorschläge sind additiv und rückwärtskompatibel (neue Felder, alte Analysen unverändert). Sie brauchen keine Ordner-Änderungen und keine neuen Abhängigkeiten,
sind also Render- und Worker-kompatibel.
