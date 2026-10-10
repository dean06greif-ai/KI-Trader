# Jump-Detektor: zu viel Seitwärts bei 3 Regime – Prüfung & Fix 10.10.2026

## Befund (Screenshot BTC 1h, 3 Regime)
89 % Seitwärts, 7 Seitwärts-Abschnitte mit Ø 60 Tagen; der Abverkauf 126k -> 60k (Okt 25 – Feb 26)
war rückblickend überwiegend „seitwärts“. Nachgestellt: genau das liefert der Detektor **jump** mit
Standardwerten (BTC 87 %, ETH/SOL 84 % Seitwärts). Die detektor-unabhängige Referenz v2 sagt ~70 %.

## Ursache
Die Rückblick-/Analyse-Sicht (Viterbi) nutzt zentrierte, stark geglättete Features, aber dasselbe
Trend-Zentrum wie die Live-Sicht (0,6 x Tagesvola je Tag). Die geglätteten Werte erreichen das kaum
(90 %-Quantil langsames Feature BTC: 0,24) -> fast alles wird seitwärts. Zusätzlich fehlte beim Jump
die Drift-Regel des reaktiven Detektors (Seitwärts mit klarer Netto-Richtung = langsamer Trend).
Die Live-Sicht ist dagegen richtig kalibriert (beste Live-Treffer gegen die Referenz bei 0,6).

## Fix (nur Rückblick-/Analyse-Sicht, Live/Handel unverändert)
- Neuer Parameter `jump_final_center_ratio` (Standard 0,65; 1 = altes Verhalten), in den Engine-Einstellungen.
- Drift-Regel als gemeinsame Funktion `regime_reactive.drift_reclassify` (reactive unverändert, jetzt auch jump).

## Messung (Final-Sicht vs. Referenz v2, Macro-F1; Live-F1 unverändert)
| Daten | alt F1 / Seitwärts | neu F1 / Seitwärts | Referenz Seitwärts |
|---|---|---|---|
| BTC 1h 540d | 58 / 87 % | 75 / 67 % | 72 % |
| ETH 1h 540d | 64 / 84 % | 72 / 70 % | 70 % |
| SOL 1h 540d | 61 / 84 % | 77 / 65 % | 69 % |
| BTC 4h 1000d | 55 / 90 % | 64 / 78 % | 70 % |
| ETH 4h 1000d | 54 / 90 % | 63 / 78 % | 67 % |
| BTC/ETH 15m 120d | 62 / 82 % | 63 / 79 % | 82/76 % |

„Live=Final“ sinkt dadurch beim Jump um ~5–10 Punkte (vorher geschönt, weil beide Sichten fast nur seitwärts waren).
Reactive/EMA/Kombi: Labels bit-genau unverändert (Hash-Vergleich 3/5/9 Regime x 3 Coins).

## Gegenprüfung gegen Schönrechnen (Nachtrag)
Der Faktor 0,65 wurde NUR auf BTC/ETH/SOL gewählt und danach ohne Anpassung geprüft:
- **Fremde Coins (XRP, DOGE, ADA, BNB, LINK, AVAX, 1h 540d):** F1 vs. Referenz im Mittel 65,0 -> 72,6;
  0,65 ist bei 5 von 6 Coins der beste Wert (LINK: 0,8 minimal besser, 75,7 vs. 75,2).
- **Synthetische Serien mit bekannter Wahrheit (24 Serien):** Final-F1 44,7 -> 55,2. Kleinere Faktoren
  (0,5) wären dort sogar noch besser (57,1) -> 0,65 ist eher vorsichtig gewählt, nicht geschönt.
- Reproduzierbar: `python backend/scripts/regime_jump_final_validation.py synth|real <pkl>`.

## Ehrliche Grenzen
- Es gibt keine „eine wahre“ Regime-Einteilung; F1 ~70-75 vs. Referenz bzw. ~55 vs. bekannte Wahrheit
  ist gut, aber nicht perfekt. Reactive/EMA/Kombi liegen bei bekannter Wahrheit auf ähnlichem Niveau (55-57).
- Die LIVE-Sicht des Jump erkennt Trends spät (Trend-Treffer ~32 % gegen den Rückblick). Gegen die
  Referenz ist sie trotzdem die beste Live-Sicht; gegen synthetische Wahrheit liegt sie unter den anderen
  (45 vs. ~50). Das ist ein eigener, nächster Schritt.
- Die eingebaute Plausibilitätsprüfung (`validate_labels`) meldet sowohl alt als auch neu 0 % Verstöße –
  sie prüft nur Anfang/Ende eines Abschnitts und erkennt einen Crash INNERHALB eines Seitwärts-Abschnitts nicht.

## Hinweis andere Detektoren (nicht geändert)
EMA/Kombi/Reactive zeigen eher zu WENIG Seitwärts (38–48 % vs. Referenz ~70 %) und schwächere Live-Treffer
(Live-F1 31–44 vs. Jump ~60). Jump bleibt für 3 Regime die beste Wahl.

## Tests
`backend/tests/test_regime_jump_final_center_1010.py` (8) + bestehende Jump-/Autopilot-Tests grün.
Bestehende Analysen ändern sich erst nach „Neu auswerten“ bzw. einer neuen Analyse.
