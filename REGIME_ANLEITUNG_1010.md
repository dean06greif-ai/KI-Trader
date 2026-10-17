# Regime-Erkennung – Anleitung & Bewertung 10.10.2026

> Geprüft gegen den Code am 10.10.2026: Kennzahlen/Schwellen unten stimmen mit `regime_selection`,
> `regime_advice`, `regime_reference`, `regime_utility` und `regime_release` überein. Korrigiert wurden die
> Referenz-Schwelle (Note „gut“ ab 65 %, nicht 60 %) und die Freigabe-Regel (Shadow-Trades je nach Note).
> **Neu:** Partial Pooling ist eingebaut (Abschnitt 5, Karte „Partial Pooling“ im Regime-Lab).

## 1. Warum „mindestens 3 Coins“? (Hinweis in `services/regime_advice.py`)

Der Hinweis gilt der **Suche** nach den Detektor-Einstellungen (Autopilot/Optimizer), nicht der späteren
Nutzung. Ein Detektor hat 10–15 freie Stellschrauben (Umkehr-Schwelle, EMA-Länge, Sprungkosten,
Mindest-Phase …). Sucht man sie auf **einem** Kursverlauf, findet die Suche die Einstellung, die genau
dessen Wellen auswendig lernt: Trainings-Score hoch, Holdout bricht ein (Überanpassung, „Auswahl-Effekt“).
Mehr Coins = mehr unabhängige Phasen für dieselben Parameter = robustere Einstellung.

**Deine Intuition stimmt trotzdem:** Coins sind verschieden. Der Widerspruch löst sich so:
- Die Merkmale der Detektoren sind bereits **volatilitäts-normiert** (ATR %, Steigung / Tagesvola).
  Ein gemeinsamer Parametersatz misst „Trend relativ zur eigenen Schwankung“ – das überträgt sich gut.
- Unterschiedlich sind vor allem die **Skalen** (Schwellen, Mindest-Phase), nicht die Struktur.
- Darum: **Struktur gemeinsam suchen, Skalen je Coin vorsichtig nachjustieren** (Partial Pooling – jetzt
  im Regime-Lab, siehe Abschnitt 5).

**Ein Coin allein ist ok, wenn** genug Phasen da sind – Faustregel (keine harte Code-Grenze): ≥ 40
abgeschlossene Phasen im Training **und** ≥ 10 im Holdout (bei 1h und Ø 5–15 Tagen Phase ≈ ≥ 540–720 Tage).
Dieselbe Zahl 40 ist der Standard-Prior des Partial Pooling (bei 40 Phasen zählt der Coin halb). Dann nicht
blind übernehmen, sondern gegen das Gruppen-Modell **im Holdout** vergleichen – genau das macht
`regime_selection` (Champion je Asset: Strafe 1,5 Punkte für Coin-Modelle, für Pooling-Modelle nur
1,5 × Coin-Gewicht; Mindest-Score 45; Wechsel nur mit Vorsprung in jedem Fenster + Mehrfachtest-Marge).

## 2. Der Weg zur besten Regime-Erkennung für dynamische Strategien

| Stufe | Was | Werkzeug im System |
|---|---|---|
| 0 Daten | 1h (oder 4h), ≥ 540 Tage (Bull, Bär, Seitwärts enthalten), Lücken reparieren | Lokaler Worker „Lücken reparieren“ |
| 1 Gruppen | Ähnlich tickende Assets finden (r + gleiche Richtung, Holdout) | **Asset-Korrelation** (jetzt auch lokal) → „Gruppe übernehmen“ |
| 2 Struktur suchen | Autopilot je Gruppe (3–8 Assets), **mit Referenz** (zentrierte OLS / HMM), Phase 5–15 Tage | Regime-Autopilot (+ TF-Kette), Warmstart |
| 3 Feinsuche | ±1 Schritt um das Beste, kurz – kein stundenlanges Breitsuchen | `regime_finetune` |
| 4a Partial Pooling | Je Coin ein Skalen-Faktor (nur Training), geschrumpft Richtung Gruppe | Karte „Partial Pooling“ (`regime_pooling`) |
| 4b Je Asset prüfen | Gruppe vs. Pooling vs. Coin-Modell nur im Holdout vergleichen (fairer Zeitraum-Vergleich) | Regime-Champion je Asset (`regime_selection`) |
| 5 Nutzen messen | Taugt die Live-Richtung wirtschaftlich? (Rendite 1 und 3 Tage danach je Richtung, Trefferquote ≥ 55 %) | `regime_utility`, fairer Vergleich `regime_fair_compare` |
| 6 Walk-Forward | Mehrere Fenster, nicht nur ein Holdout | Walk-Forward im Lab |
| 7 Freigabe | Shadow (beobachten) → Active: ≥ 0,25 R Unterschied und 10/15/25/30 Shadow-Trades je Regime (Note sehr gut/gut/mittel/schwach) | `regime_release` |
| 8 Strategien je Regime | Je Regime eigene Sub-Strategie, nur mit genug Trades je Regime | Dynamik-Werkbank (`dynamic_strategy`) |
| 9 Überwachen | Vorwärts-Trefferquote, Drift → monatlich neu kalibrieren | Regime-Cockpit |

**Bewertungsmaßstab:** nicht „Live=Final“ (sättigt bei ~98 % auf kleinen TFs und belohnt träge
Detektoren), sondern **Referenz-Treffer + Regime-Nutzen + Strategie-PnL im Holdout**.

## 3. Reicht das bestehende System? – Bewertung

Das System ist methodisch weit (Jump-Modell nach Nystrup, Referenz-Wahrheit, Holdout, Walk-Forward,
Champion-Auswahl mit Strafe, Freigabe-Stufen). **Was fehlt bzw. unbedingt zu verbessern ist:**

1. **Champions sind kaum besser als Zufall** (Prod: Scores 46–53 %, Referenz-Trefferquote).
   Bei 3 Richtungen ist Zufall ~33 %, eine handelbare Erkennung sollte ≥ 60 % erreichen.
   → Längere Zeiträume (≥ 720 Tage), 1h/4h statt 30m/2h, Gruppen statt 13 gemischte Assets.
2. ~~Echtes Partial Pooling fehlt~~ – **umgesetzt 10.10.2026** (Abschnitt 5): Gruppen-Modell + je Coin nur
   Skalen-Faktor mit Shrinkage Richtung Gruppe.
3. **Harte Labels statt Wahrscheinlichkeiten:** Für dynamische Strategien hilft „wie sicher ist das
   Regime“ (Größe reduzieren / flat in unklaren Übergängen). Das Jump-Modell liefert Abstände, die man
   in Wahrscheinlichkeiten umrechnen kann.
4. **BTC als Kontext für Alts:** Alt-Regime hängen stark am BTC-Regime – ein BTC-Regime-Merkmal in
   der Erkennung der Alts fehlt.
5. **Im KI-Trader ungenutzt:** `regime_block_enabled` und `regime_label_reliability_enabled` sind AUS,
   aktiv freigegeben ist nur `ra_e866c510` (9 Regime Krypto 1h). Erst wenn Stufe 5 (Nutzen) passt,
   das Regime-Gate einschalten.
6. **Retention-Schutz:** Champion-Analysen waren nicht vor der Aufräum-Regel geschützt (3 Champions
   zeigten bereits auf gelöschte Analysen) – **behoben** (`retention.champion_analysis_ids`).

## 4. Empfohlene Praxis (konkret)
1. Korrelation auf 1h/720 Tage für die Watchlist (Ausführung „Lokal“).
2. Je Gruppe ≥ 3 Assets: Autopilot 1h, 720 Tage, Phase 5–15 Tage, Referenz an.
3. Feinsuche, dann „Regime suchen & speichern“ (Umfang „beide“, Training 75 %).
4. Partial Pooling auf diese Gruppen-Analyse (Prior 40), danach fairer Zeitraum-Vergleich + Champion je Asset.
5. Erst bei Referenz-Note „gut“ (≥ 65 %, „mittel“ ab 55 %) und Nutzen-Trefferquote ≥ 55 %: Shadow-Freigabe →
   beobachten, bis die nötigen Shadow-Trades je Regime erreicht sind (10–30 je nach Note) → Active.
6. Dynamik-Werkbank nur auf freigegebenen Analysen, Mindest-Trades je Regime einhalten.

## 5. Partial Pooling – so funktioniert es (`services/regime_pooling.py`)

| Baustein | Umsetzung |
|---|---|
| Gruppen-Modell | Kombinierte v2-Analyse mit ≥ 3 Coins, Training < 100 % (Holdout nötig) |
| Struktur gemeinsam | Detektor, EMA-Längen und das gewählte Glättungs-Profil werden 1:1 übernommen |
| Skala je Coin | EIN Faktor k ∈ {0,7 · 0,85 · 1,0 · 1,2 · 1,4} auf die Schwellen: Umkehrpunkte `rev_atr_mult` + `side_leg_atr_mult`, EMA `ema_regime_thr`, Kombi `kombi_thr`, Jump `jump_center` + `jump_penalty_days` |
| Suche | Nur auf dem Training des Coins (balancierte Trefferquote + innere Validierung); < 1 Pkt. Vorsprung ggü. k = 1 → k = 1 |
| Shrinkage | Gewicht w = Phasen / (Phasen + Prior), k_pooled = k_best^w; Prior 80 vorsichtig / 40 Standard / 20 mutig |
| Ergebnis | Neue Analyse „… · Pooling“ (Umfang je Coin, `settings.pooling` = Bericht) |
| Bewertung | Champion je Asset: Pooling-Abschlag 1,5 × w statt 1,5; Wechsel nur mit Vorsprung in jedem Fenster |

**Lesen des Ergebnisses:** Pooling-Faktor nahe 1,0 = Gruppe passt; deutliche Abweichung nur bei Coins mit
vielen Phasen. Pooling ist kein Selbstzweck – übernimm es nur, wenn der faire Vergleich es vorne sieht.
Der Pooling-Lauf rechnet in der Cloud (nicht auf dem lokalen Worker).
