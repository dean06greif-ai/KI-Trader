# KI-Trader – Signal-Broker „Detektor schlägt vor, KI entscheidet“ (Umsetzung 10/2026)

Basis: Deep-Analyse `KI_TRADER_DEEP_ANALYSE_1010.md` + eigene Prod-Auswertung (nur lesend, 14 bzw. 7 Tage).

## 1. Befunde aus den Prod-Daten (vor der Änderung)

| Befund | Zahl | Ursache im Code |
|---|---|---|
| divergence-Treffer (einziges Setup mit Vorteil) ungehandelt | **554 von 633** in 14 T | `paper_wanted()` nur mit Backtest-Edge – Lab-Status Krypto `exhausted` |
| KI-Setup-Label ohne Detektor-Treffer | 81 % der KI-Trades | Label wurde nie geprüft |
| Live-Gate bei fehlendem Setup | übersprungen | `if not setup: return None` |
| Fee-Wächter-Blocks | **440 / 7 T, davon 420 Paper-Sammeltrades** | Paper-Trades wurden verworfen statt erweitert |
| Richtungs-/Duplikat-Guard | ~190 / 7 T | Regel-Paper und KI-Trades teilten Slots/Cooldowns |
| KI-eigene Setups (6 Stück) | **0 Trades** seit Anlage | nur Textbeschreibung, kein Detektor |
| Konfidenz | kein Zusammenhang mit R | 55-64: −0,52 R · 65-74: −0,43 R · 75-84: −0,20 R |

## 2. Neue Architektur (modular, `signal_broker_enabled` = aus -> exakt Altverhalten)

```
5m-Kerze ─► Detektoren (Bibliothek + KI-Regel-Detektoren)
              │
              ├─► Regel-Paper-Trade   (Quelle "regel" = Vergleichsbasis, alle Treffer)
              └─► SIGNAL-FENSTER      (services/signal_broker.py, Dauer je Setup)
                      │  Prompt-Block "OFFENE SETUP-SIGNALE" + gezielte KI-Prüfung
                      ▼
                 KI entscheidet: sofort · später im Fenster · verwerfen (HOLD = Veto)
                      │
     signal_id/Symbol+Seite+Setup passt ─► "ki_geprueft" ─► Live-Gate (Reife je Klasse)
     kein offenes Signal               ─► "ki_frei"     ─► nur Paper (getrennt gemessen)
```

* **Signal-Fenster je Setup** (Minuten): momentum_news 10 · breakout/squeeze 15 · session_open/liquidity_sweep 20 ·
  vwap_reclaim 25 · trend_follow/pullback 30 · mean_reversion/range_fade 40 · divergence 45 · htf_range 60
  (überschreibbar `signal_window_overrides`). Ungültig, wenn der Kurs hinter dem SL liegt oder > 0,6 R davongelaufen ist.
* **Live nur KI-geprüft**; die ersten **5** KI-geprüften Trades je Setup × Anlageklasse laufen als Paper-Datensammlung,
  danach live bei geschrumpftem Erwartungswert ≥ +0,05 R; Live-Ergebnis ≤ −0,15 R (≥ 5 Live-Trades) -> zurück in Paper.
* **Gate-Lücke geschlossen**: ohne Setup kein Live-Trade (gilt auch im Altmodus).
* **Altsystem blockiert nicht mehr**: Rückstufung aus der Mischstatistik, Live-Bypass über Konfidenz und die verdeckte
  ±10-Konfidenz-Korrektur greifen im Broker-Modus nicht; sie erscheinen nur noch als Hinweis.
* **Statistik nach Quelle** (`services/source_stats.py`): regel · ki_geprueft · ki_frei, in R, inkl. Timing
  (sofort / 6-20 min / > 20 min) und „KI war früher als der Detektor“ (`ki_frueh`).
* **Konfidenz-Kalibrierung** (`services/confidence_calibration.py`): je Stufe echte R/Trefferquote (geschrumpft),
  an jeder Entscheidung `confidence_cal`, Rückmeldung im Prompt.
* **Lernen**: Prompt bekommt je Setup „Regel vs. KI-geprüft“ inkl. Timing, die Kalibrierung und die Wächter-Bilanz
  (Schattentrades: Block richtig/falsch).
* **KI-eigene Setups** (`services/custom_detector.py`): kleine Regel-Sprache auf 5m-Features; neue Vorschläge liefern
  `rule` direkt, bestehende werden vom Research-Analysten übersetzt (eins je 30 min).

## 3. Sperren / Blockaden

| Sperre | Neu |
|---|---|
| Fee-Wächter Live | unverändert hart |
| Fee-Wächter Paper | SL bis zum Wächter-Minimum erweitert (TPs skalieren, CRV bleibt) – `fee_guard_widen_collection` |
| Richtungs-/Duplikat-Guard, Sammel-Cooldown | getrennte Welten Regel-Paper vs. KI |
| Wochenende, Stale-Price, MasterPrompt-/Tageslimits, Korrelations-Guard, FOMC/CPI | unverändert hart |
| Rückstufung (Mischstatistik), Live-Bypass, Setup-Gewichtung | im Broker-Modus nur Hinweis |

## 4. KI-Trader-Lab: Mindest-Trades

`MIN_TRADES_SHARE` 0,4 -> 0,3, Deckel (60/40) -> (48/30). Beispiele, die nur an der Anzahl scheiterten:
mean_reversion Krypto 57/15 (OOS +7,58), breakout Krypto 66/12 (+0,83). Walk-Forward 2/3 und PnL > 0 in IS + OOS
bleiben Pflicht (Overfitting-Bremse). Wichtiger: die Datensammlung hängt nicht mehr am Lab-Edge.

## 5. Bedienung

KI-Labor -> Tab **Adaptiv** -> Karte **Signal-Broker** (an/aus, Paper-Trades bis Live, KI-Prüfungen/Tag,
offene Fenster, Bilanz nach Quelle, Kalibrierung, Verlauf). In der Entscheidungsliste zeigt ein Tag
`KI-GEPRÜFT · sofort/später`, `KI-FREI` oder `REGEL`. API: `GET /api/ai/signal-broker`.

## 6. Tests

`backend/tests/test_signal_broker.py` (23 Tests) + angepasste Alt-Tests (`test_setup_trigger.py`,
`test_revision_backtest_lift.py`, `test_tf2_setup_review.py`). Unit-Suite: keine neuen Fehler gegenüber dem
Ausgangsstand (vorbestehende Fehler identisch).

## 7. Erwartung nach dem Deploy

Live-Trades werden anfangs **seltener** (jedes Setup braucht 5 KI-geprüfte Paper-Trades mit positivem R) – Paper-Daten
dagegen deutlich **mehr** (alle Detektor-Treffer + KI-Prüfung). Nach 1–2 Wochen zeigt die Karte je Setup, ob die
KI-Prüfung gegenüber der Regel Mehrwert bringt (`KI-Mehrwert`).
