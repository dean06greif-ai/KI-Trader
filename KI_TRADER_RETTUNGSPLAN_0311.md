# KI-Trader – Deep-Analyse & Rettungsplan 03.11.2026

Datenbasis (Prod-DB, **nur lesend**): alle geschlossenen KI-Trades `auto_trades.strategy_id = ai_trader`
seit Start (1676 Trades mit `risk_usdt`, 07.09.–03.11.), davon 647 seit dem letzten Bericht (10.10.).
Einheit **R** = PnL ÷ riskierter Betrag (netto nach Gebühren). Skripte zum Nachrechnen:
`scripts/prod_ki_trader_deep_probe.py`, `scripts/prod_ki_trader_filter_sim.py` (beide read-only).

## 1. Ergebnis in einem Satz

Der Trader verliert **−0,23 R pro Trade** (−13.048 USDT Paper-PnL auf 1676 Trades). Davon kommen
**−0,06 R aus den Einstiegen selbst** (≈ Zufall) und **−0,17 R aus Gebühren** (+ ~0,07 R Slippage).
Er ist also kein „schlechter Trader“, sondern ein **Zufalls-Einsteiger, der zu teuer handelt**.
Seit dem 10.10. hat sich daran nichts geändert (−0,235 R, 647 Trades) – die Woche ist egal, das Muster ist stabil.

## 2. Ursachen (nach Wirkung sortiert)

| # | Ursache | Beleg (Prod) |
|---|---|---|
| 1 | **1-Minuten-Scalping mit engen Stops** – 99,6 % aller Trades sind `timeframe 1m / horizon scalp` | SL 0,4–0,7 %: −0,34 R (865 Trades) · SL ≥ 1 %: −0,03 R · SL ≥ 2 %: +0,01 R |
| 2 | **Gebühren fressen das Risiko** – Fee-Wächter-Faktor 3,5 erlaubt bis 29 % des Risikos als Gebühr | Gebühr ≤ 10 % des Risikos: **±0 R** · 10–20 %: −0,23 R · 20–30 %: −0,34 R · > 30 %: −0,82 R |
| 3 | **Hebel 50–100×** (folgt aus engen Stops + Auto-Leverage) | 50–100×: −0,35 R (1049 Trades) · 25–50×: −0,04 R |
| 4 | **Handeln gegen/ohne Tagestrend** | mit Tagestrend −0,11 R · gegen/flat −0,21 R · ohne Bias-Daten **−0,39 R** |
| 5 | **Nicht-Krypto-Märkte** (Öl, Gold, Silber, FX, Indizes) | Krypto −0,12 R · Rohstoffe −0,49 R · FX −0,48 R · Indizes −0,43 R (Slippage bis 0,15 R) |
| 6 | **Kein Setup hat einen stabilen Vorteil** – „Gewinner“ kippen mit mehr Daten | `divergence` war am 10.10. +0,20 R → jetzt −0,23 R; `range_fade` +0,42 R (25 T.) → gesamt −0,15 R |
| 7 | **KI-Konfidenz ist nicht kalibriert** | < 55: −0,28 R · 55–64: −0,22 R · 65–74: −0,16 R · ≥ 75: −0,25 R |
| 8 | **TP1 zu selten** | TP1-Quote 20 %, nötig ~34 % · sehr kurze Trades (< 5 min) −1,27 R |
| 9 | Live-Bypass ist **AN** (`live_gate_bypass_enabled: true`) | nicht live-reife Setups gehen über „hohe“ (unkalibrierte) Konfidenz live |

Die Sprachmodelle (LLM) sind **nicht** der Hauptgrund: Regel-Trigger und LLM-Einstiege liegen beide bei −0,23 R.
Einziger Lichtblick bei der Quelle: `ki_geprueft` (Regel-Signal, das die KI geprüft hat) +0,32 R – aber nur 25 Trades.

## 3. Ehrliches Urteil: kann er gut werden?

- **Als 1-Minuten-Scalper: nein.** Bei 0,06 % Taker-Gebühr je Seite und Stops um 0,5 % gehen
  ~0,24 R pro Trade an die Börse. Dafür bräuchte man eine Trefferquote, die kein Detektor und kein
  LLM auf 1m-Kerzen liefert. Mehr KI, bessere Prompts oder teurere Modelle ändern daran nichts.
- **Als langsamerer, kostenbewusster Trend-Trader: vielleicht.** Die einzige Gruppe, die in beiden
  Hälften der Historie nicht negativ war: *Gebühr ≤ 10 % des Risikos* (2. Hälfte +0,18 R) und
  *mit Tagestrend + SL ≥ 0,7 %* (2. Hälfte +0,23 R). Kombiniert (Edge-Filter, s. u.):
  **+0,06 R auf 157 Trades** vs. −0,28 R für den Rest. Das ist ein Hinweis – **kein Beweis** (in-sample,
  kleine Stichprobe, ~20 Trades/Woche).
- **Kosten decken:** +0,06 R × 20 Trades/Woche × z. B. 10 USDT Risiko ≈ 12 USDT/Woche. Erst wenn sich ein
  Vorteil ≥ +0,15 R out-of-sample bestätigt, lohnt es sich, Risiko je Trade hochzufahren.

## 4. Was ich jetzt eingebaut habe (modular, rückwärtskompatibel)

1. **Edge-Filter** `backend/services/edge_filter.py` (reine Funktionen) + ein Schritt im Einstiegspfad
   `AutoTradeManager._edge_filter_step` (`services/bitunix_trade.py`), nach der endgültigen Live/Paper-Entscheidung:
   - Live nur, wenn **Roundtrip-Gebühr ≤ 10 % des Risikos** (≈ SL ≥ 1,2 % bei 0,06 % Taker),
     **Richtung = Tagestrend** und **Anlageklasse Krypto**.
   - **Paper- und Sammel-Trades werden NICHT verändert**, nur markiert (`trade.edge_filter`) – so läuft die
     Datensammlung unverändert weiter und liefert den Out-of-sample-Beweis.
   - Geblockte Live-Einstiege werden als **Wächter-Schattentrade** nachgespielt (`guard_shadow`, Wächter
     `edge_filter`) → man sieht, ob der Block richtig war.
   - Fail-open bei internen Fehlern, alles abschaltbar: KI-Trader → Setup → *Edge-Filter (Live)*,
     *Max. Gebühren je Risiko*, *Nur mit Tagestrend*, *Live nur Krypto*, *Auch Paper blocken*.
2. **Edge-Bericht erweitert** (`services/ai_edge_report.py`, Diagnose → „Hat er einen Vorteil?“):
   neue Gruppen *Edge-Filter besteht / besteht nicht* (rückwirkend für alle Trades), *Gebühren je Risiko*,
   *Tagestrend*, *Anlageklasse* + automatischer Befund.
3. Tests: `backend/tests/test_edge_filter.py`, `backend/tests/test_edge_filter_pipeline.py`.
4. Strategie-Namen im Verlauf/Performance-Liste brechen jetzt um statt mit „…“ abgeschnitten zu werden.

Nicht automatisch geändert (deine Entscheidung, Prod-DB bleibt unberührt): alle Einstellungen unten.

## 5. Rettungsplan (Reihenfolge)

**Phase 0 – sofort, nur Einstellungen (Geld schützen)**
- KI-Setup: `Live-Bypass` **AUS**.
- Edge-Filter **an** lassen (Standard nach Deploy).
- Fee-Wächter-Faktor **3,5 → 8** (Gebühren ≤ 12,5 % des Risikos), ATR-Faktor **3 → 4**.
- Rohstoffe/FX/Indizes: nur noch Paper (Edge-Filter erledigt das live automatisch).

**Phase 1 – 3 bis 4 Wochen Paper-Beweis (nichts live)**
- Trader im Paper-Modus laufen lassen. Im Edge-Bericht nur auf die Zeile **„Edge-Filter: besteht“** schauen.
- Ziel-Kriterium für Live: **≥ 60 Trades „besteht“ mit netto ≥ +0,10 R** und die 2. Hälfte davon ebenfalls > 0.
- Wird das nicht erreicht → aufhören, Geld nachzuschießen (siehe Phase 4).

**Phase 2 – Einstiege langsamer machen (nächster Ausbau)**
- Signale auf **15m/1h-Struktur** statt 1m-Kerzen stützen, Stop hinter 15m-Struktur (1–3 %), Hebel ≤ 20×.
- Maker-Einstiege (Limit) als Standard: halbe Gebühren brachten in der Simulation +0,11 R je Trade.
- LLM nur als **Veto/Prüfer** eines Regel-Signals (`ki_geprueft` war die einzige positive Quelle), nicht als Auslöser.

**Phase 3 – vorsichtig live**
- Erst nach Phase-1-Kriterium: Live mit **0,5 % Risiko je Trade**, max. 2 offene Trades.
- Abbruch-Regel: nach 40 Live-Trades netto < 0 R → zurück auf Paper.

**Phase 4 – wenn es nicht klappt**
- Bestätigt sich der Vorteil nicht, ist die ehrliche Antwort: Den KI-Trader als **Signal-Prüfer/Assistent**
  behalten (Diagnose, Regime, Strategie-Labor), aber **nicht automatisch mit Geld handeln lassen**.
  KI-Kosten sparen: Analyst-Aufrufe auf Regel-Signale beschränken.
