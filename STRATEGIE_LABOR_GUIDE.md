# Strategie-Labor – Kompletter Leitfaden

Dieser Leitfaden erklärt **alle Funktionen des Strategie-Labors** (Strategie-Optimizer),
das Zusammenspiel mit Backtester & Regime-Lab, das Seitwärtsmarkt-Problem und die
neuen Funktionen (Marktphasen-Filter, verbesserter Copilot).

---

## 1. Die 5 Such-Modi des Strategie-Optimizers

| Modus | Was er macht | Wann nutzen |
|---|---|---|
| **Parameter-Optimierung** | Testet viele Parameter-Kombinationen einer BESTEHENDEN Strategie (Indikator-Schwellen + optional TP/SL, Hebel, Break-Even, …). Algorithmen: Random Search oder Bayes (TPE, konvergiert schneller). | Du hast eine Strategie, die grundsätzlich funktioniert, und willst sie feinjustieren. |
| **Strategie-Discovery** | Baut eine NEUE Strategie „greedy": fügt Regel für Regel den Indikator hinzu, der das Ergebnis am stärksten verbessert. Schnell, findet aber keine Synergien zwischen Indikatoren (jeder wird einzeln bewertet). | Schneller erster Wurf für neue Ideen. Ergebnis danach immer per Walk-Forward prüfen! |
| **Discovery + Optimierung (Combo)** | Erst Discovery, dann werden die gefundenen Schwellenwerte per Feintuning weiter optimiert. | Wie Discovery, aber mit besserer Endqualität. |
| **Dynamische Strategie** | Erkennt Marktregime automatisch (Bulle/Bär/Seitwärts, ohne Lookahead) und sucht **pro Marktphase** eigene Trade-Parameter oder komplett eigene Regeln. Jede Phase wird per Walk-Forward geprüft, Vergleich gegen statische Benchmark ist immer aktiv. | **Die Antwort auf das Seitwärtsmarkt-Problem** (siehe Abschnitt 3). |
| **Endlos-Suche (Explore)** | Sucht im Hintergrund so lange neue Indikator-Kombinationen (frischer Zufalls-Seed je Lauf), bis genug „Champions" Training UND Walk-Forward bestehen. | Am robustesten gegen Overfitting – ideal über Nacht laufen lassen. |

### Wichtige Einstellungen (gelten je nach Modus)
- **Zeitraum (Tage)**: Mehr Daten = robustere Ergebnisse. Faustregel: mind. 90 Tage für 5m+, für 1m reichen 7–30 Tage.
- **Ziel (Objective)**: Kombi (PnL × Winrate) ist meist der beste Kompromiss. Reines „Höchster PnL" neigt zu wenigen Glückstreffern, reine Winrate zu Mini-Gewinnen mit großem Risiko.
- **Min. Trades**: Filter gegen Zufall – Kandidaten mit weniger Trades werden verworfen. Nie unter ~10 pro Coin setzen.
- **Max. Regeln**: Mehr Regeln = mehr Overfitting-Gefahr. 3–4 ist der Sweet-Spot.
- **Deep-Test (deep/extreme)**: Erschöpfende Paar-/Beam-Suche statt greedy – findet Indikator-Synergien, dauert deutlich länger.

### Robustheits-Checks (alle Modi, dringend empfohlen)
- **Walk-Forward** (single/rolling/anchored): Training auf X%, Validierung auf frischen Daten. **Der wichtigste Schutz gegen Overfitting.**
- **Drawdown-Filter**: verwirft Kandidaten mit zu hohem max. Drawdown.
- **Konstanz-Test**: teilt den Zeitraum in Abschnitte – ist die Strategie in fast allen profitabel oder lebt sie von einem Glücks-Monat?
- **Stress-Test**: vervielfachte Gebühren/Slippage – überlebt die Strategie reale Kosten?
- **Stabilität (Plateau)**: alle Schwellen ±X% variiert – kippt das Ergebnis, war es ein Zufalls-Spike.
- **Monte-Carlo**: mischt die Trade-Reihenfolge und prüft die Drawdown-Verteilung.
- **Regime-Report**: zeigt PnL **pro Marktphase** (Bulle/Bär/Seitwärts) – hier siehst du das Seitwärts-Problem sofort.

---

## 2. Backtester & Regime-Lab (die anderen Labor-Werkzeuge)

- **Backtester**: simuliert Strategien auf historischen Daten mit exakt derselben Logik wie Paper/Live (Struktur-SL, TP1-Teilverkauf, Break-Even, ATR-Trailing, Gebühren pro Fill). CSV-Export jedes einzelnen Trades zum Nachprüfen.
- **Regime-Lab**: analysiert Marktphasen (K-Means/v2-Engine auf Trend/Volatilität/Effizienz), validiert die Label-Qualität und verwaltet die **dynamischen Strategien im Live-Betrieb** (aktuelles Regime je Coin, Auto-Umschaltung, Wechsel-Protokoll, Übergangsschutz).

---

## 3. Das Seitwärtsmarkt-Problem – und die zwei Lösungen

**Beobachtung:** Eine per Discovery gefundene Strategie ist im Bullen- und Bärenmarkt
stark im Plus, im Seitwärtsmarkt aber deutlich im Minus.

**Warum passiert das?** Discovery optimiert über den GESAMTEN Zeitraum. Trendfolge-
Indikatoren (EMA-Kreuzungen, MACD, Momentum) liefern in Trendphasen echte Signale,
im Seitwärtsmarkt aber laufend Fehlsignale („Whipsaws"). Solange die Trend-Gewinne die
Seitwärts-Verluste überkompensieren, sieht das Gesamtergebnis gut aus – die Strategie
handelt trotzdem dumm weiter, wenn der Markt seitwärts läuft. **Du hast nichts falsch
gemacht** – das ist eine Eigenschaft statischer Strategien.

### Lösung A (einfach): Marktphasen-Filter **[NEU]**
Im Auto-Trade-Setup jeder Strategie (Blitz-Symbol → „MARKTPHASEN-FILTER"):
- Filter aktivieren und z.B. **Seitwärts** blockieren.
- Die Phase wird pro Coin automatisch erkannt (1h-Kerzen, 30 Tage, alle 15 Min. aktualisiert, ohne Lookahead).
- In blockierten Phasen werden **neue Trades übersprungen** (offene Trades laufen normal weiter, inkl. SL/TP-Management).
- Fail-open: Bei Datenfehlern wird NIE fälschlich blockiert – das bestehende Verhalten bleibt stabil.

### Lösung B (mächtiger): Dynamische Strategie
Der Modus „Dynamische Strategie" im Optimizer sucht **pro Marktphase eigene
Parameter oder eigene Regeln** – im Seitwärtsmarkt z.B. eine Mean-Reversion-Logik
(RSI/Bollinger) statt Trendfolge, oder schlicht „nicht traden". Das Regime-Lab
übernimmt die Umschaltung im Live-Betrieb automatisch (inkl. Übergangsschutz).

**Empfehlung:** Starte mit Lösung A für deine bestehende Discovery-Strategie
(sofort wirksam, kein neuer Suchlauf nötig). Wenn du mehr willst, lass eine
dynamische Suche mit „pro Regime eigene Regeln" laufen.

---

## 4. Strategie-Copilot **[VERBESSERT]**

Der Copilot ist in **allen Labor-Funktionen** verfügbar (Optimizer – alle 5 Modi,
Strategie-Builder, Backtester, Regime-Lab) und wurde grundlegend überarbeitet:

- **Reiner Berater + Einstellungs-Assistent**: Er entwickelt NIE mehr eigenmächtig
  Strategien. Strategie-Entwürfe macht er nur noch, wenn du es ausdrücklich verlangst.
- **Voller Kontext**: Er sieht deine aktuellen Panel-Einstellungen, das letzte Ergebnis
  (inkl. deterministischer Sanity-Checks) UND eine Übersicht **aller vorhandenen
  Strategien** mit ihren Indikatoren/Regeln und echten Paper-/Live-Ergebnissen sowie
  Optimizer-Bestwerten – damit kann er dir sagen, welche Strategie du optimieren
  solltest, warum, und welche Setups fehlen.
- **Einstellungen ändern auf Zuruf**: Neuer Vorschlags-Typ „Einstellungen" – der
  Copilot schickt z.B. „Modus: Explore, 90 Tage, Walk-Forward rolling, 4 Fenster"
  als Karte, du klickst „Übernehmen" und das Formular wird direkt gesetzt.
- **Modell-Auswahl (Zahnrad „Modell" im Copilot)**: Du kannst das OpenRouter-Modell
  frei wählen – z.B. **DeepSeek** (deepseek-v4-flash/pro), GPT (gpt-oss), GLM, Grok
  oder die schnellen Free-Modelle. Bezahlte Modelle laufen über dein
  OpenRouter-Guthaben und werden nie automatisch als Fallback benutzt.
- **Key-Fallback**: Sind keine eigenen `COPILOT_OPENROUTER_API_KEY*` gesetzt, nutzt
  der Copilot automatisch deine vorhandenen `OPENROUTER_API_KEY*` – er funktioniert
  damit ohne zusätzliche .env-Einträge (vorher: „Kein Copilot-Key konfiguriert").

---

## 5. Neue/geänderte Dateien (für Render unverändert deploybar)

Backend:
- `backend/services/regime_gate.py` **(neu)** – Marktphasen-Filter (gecachte Regime-Erkennung, Fail-open)
- `backend/services/bitunix_trade.py` – Gate-Aufruf in `on_signal` + neue Default-Config-Keys
- `backend/services/strategy_copilot.py` – Advisor-Prompt, Key-Fallback, Settings-Vorschläge, Strategie-Übersicht
- `backend/routers/copilot.py` – `key_source` im Status, Schutz für Settings-Vorschläge
- `backend/routers/autotrade.py` – `GET /api/autotrade/regime_phase/{symbol}` (Anzeige)
- `backend/tests/test_regime_gate.py`, `backend/tests/test_copilot_advisor.py` **(neu)** – Regressionstests

Frontend:
- `StrategyCopilot.js/.css` – Modell-Auswahl, Settings-Vorschläge, neue Quick-Prompts
- `Optimizer.js` – voller Kontext an den Copilot + Übernahme von Einstellungs-Vorschlägen
- `StrategyAutoTradeModal.js` – UI für den Marktphasen-Filter inkl. Live-Phase-Anzeige
- `Backtester.js`, `RegimeLab.js` – Copilot auch dort eingebettet
