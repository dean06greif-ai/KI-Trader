# Strategien & Regime-Erkennung – Deep-Analyse 03.11.2026

Datenbasis (Prod-DB, nur lesend): 533 geschlossene Nicht-KI-Trades, 27 dynamische Strategien,
1076 Regime-Wechsel (`dynamic_switch_log`), Regime-Kalibrierungen. Skript: `scripts/prod_strategy_regime_probe.py`.

## 1. Statische Strategien

| Strategie | Trades | netto | brutto | Bemerkung |
|---|---|---|---|---|
| **TrendFolge2** (5m) live | 322 | **−0,01 R** | **+0,08 R** | fast Break-even – beste Strategie der Plattform |
| TrendFolge2 paper | 115 | −0,12 R | ±0 R | |
| TrendFolge2 Remastered (3 Varianten) | 27 | gemischt | | zu wenige Trades für ein Urteil |
| Manuell (Bitunix, live) | 24 | −0,07 R | | **−908 USDT** – größter echter Verlust aller Quellen |

**TrendFolge2 nach Kurzfrist-Regime beim Einstieg** (das ist der Hebel):

| Regime | Trades | netto |
|---|---|---|
| trend_down_volatil | 86 | **+0,27 R** |
| trend_down_normal / _ruhig | 75 | +0,06 / +0,13 R |
| drift_volatil | 17 | +0,22 R |
| trend_up_volatil | 76 | **−0,32 R** |
| breakout_volatil | 27 | **−0,36 R** |
| drift_normal | 17 | **−0,59 R** |

Empfehlungen:
1. **Kurzfrist-Regime-Filter für TrendFolge2** (neu eingebaut: Strategie → Auto-Trade-Einstellungen →
   *Kurzfrist-Regime-Filter*, Knopf „Empfehlung TrendFolge2“): `trend_up_volatil`, `breakout_volatil`, `drift_normal`
   sperren. In-sample hätte das ~120 Trades mit Ø −0,37 R vermieden und TF2 von −0,01 R auf ≈ +0,15 R gehoben.
   **Zuerst 2–3 Wochen Paper** (Stichproben je Regime 17–86 Trades). Code: `services/context_regime_filter.py`,
   Prüfung in `entry_guard.check_entry` (leer = aus, Sammel-Trades unberührt, fail-open).
2. **Gebühren halbieren**: TF2 verliert brutto +0,08 R → netto −0,01 R nur durch Gebühren. Maker-Einstieg (Limit am
   Signalpreis) würde ihn ins Plus drehen.
3. **Keine weiteren Remastered-Varianten je Coin-Liste** – jede Variante mit 3–20 Trades ist Rauschen
   (Overfitting auf Coin-Auswahl). Lieber EINE TF2-Version + Regime-Filter.
4. Coins: HYPE (+0,19 R), SUI (+0,21 R), BNB (+0,13 R) positiv; DOT (−0,58 R), DOGE (−0,35 R), SOL (−0,27 R) negativ –
   erst nach dem Regime-Filter neu bewerten (die Coin-Effekte sind teils Regime-Effekte).
5. Manuelle Trades: −908 USDT auf 24 Trades. Wenn manuell gehandelt wird, gleiche Regeln wie für die Bots
   (Risiko je Trade fest, Stop vorher, nicht gegen den Tagestrend).

## 2. Dynamische Strategien

- **0 Trades** aus dynamischen Strategien in der gesamten Historie – alle stehen im Release-Status „draft“.
  Sie laufen nur als Regime-Anzeige.
- **27 Dokumente, davon ~11 Test-Reste** (`TEST_iter15_subs`, `TEST_dyn_bollinger`, `AP04-Test-*`) in der Prod-DB →
  im Dynamik-Bereich archivieren, damit Auswahl/Autopilot nicht auf Test-Daten laufen.
- **Regime-Flackern (Bug, behoben):** 1076 Wechsel, Median-Abstand **1,0 h**, **56 % Ping-Pong** (A→B→A < 6 h),
  obwohl `min_hold_days = 1` eingestellt ist. Ursache: Jeder Runtime-Refresh berechnet das Regime neu aus einem
  gleitenden Fenster; am rechten Rand gibt es keine Glättung und der Live-Zustand hatte kein Gedächtnis.
  **Fix:** `services/regime_dwell.py` – ein Wechsel gilt live erst nach der Mindest-Haltedauer **und** 2
  aufeinanderfolgenden Bestätigungen (wie im Backtest). Je Strategie abschaltbar: `settings.runtime_dwell_enabled=false`.
  Der zurückgehaltene Wechsel wird am Zustand angezeigt (`held`, `pending`).

## 3. Regime-Erkennung – Verbesserungsvorschläge

1. **Weniger Regime:** Das 9-Regime-Modell (Trend auf/ab/seitwärts × 3 Vola-Stufen) auf 1h ist zu fein – die
   Wechsel 1↔4, 3↔4, 2↔5 dominieren (benachbarte Vola-Stufen). Vorschlag: **5 Regime** (Aufwärts, Abwärts,
   Seitwärts ruhig, Seitwärts volatil, Ausbruch) oder Vola nur als Größen-Faktor statt als eigenes Regime.
2. **Zeiteinheit trennen:** Regime aus **4h/1d** (Struktur) bestimmen, gehandelt wird auf 5m/1h. Das 1h-Regime ändert sich
   zu oft, um eine Strategie daran zu binden.
3. **Regime nach Nutzen bewerten, nicht nach Trefferquote:** Ein Regime ist gut, wenn sich die Strategie-Ergebnisse
   je Regime deutlich unterscheiden (wie bei TF2: +0,27 R vs. −0,32 R). Kalibrierung künftig auf „Spreizung der R je Regime
   out-of-sample“ optimieren.
4. **Kurzfrist-Regime als Filter nutzen:** Das Markt-Regime des KI-Beobachters (`entry_market_snapshot.features.regime`)
   trennt bereits gut – es ist der einfachste Weg, statische Strategien regime-bewusst zu machen, ohne dynamische
   Strategie-Wechsel.
5. **Dynamisch erst live, wenn** der Walk-Forward-Holdout pro Regime ≥ 30 Trades und eine positive R-Spreizung zeigt.

## 4. KI-Trader – Antwort zu „KI als Prüfer: jetzt oder warten?“

**Jetzt umschalten.** Der Trader läuft ohnehin im Paper-Beweis; der Prüfer-Modus spart sofort den
periodischen LLM-Zyklus und die Daten (KI-geprüft +0,32 R vs. KI-frei −0,13 R) sprechen dafür. Umschaltbar unter
KI Trader → Setup → *Rolle der KI* (frei ↔ Prüfer); der Edge-Bericht zeigt die Gruppe *Signal-Quelle*.
**Zurück auf „frei“**, wenn nach ~4 Wochen „ki_geprueft“ ≤ „regel“ liegt (dann bringt die KI-Prüfung keinen Mehrwert).
