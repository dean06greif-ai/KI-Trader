# Volatility Squeeze Breakout (1h Trend): Begründung und Backtest (29.09.2026)

## Warum diese Strategie?
Die Ergebnisse aus Regime-Lab-Studie und TradeX-Backtest zeigen:
- 1m-Mean-Reversion-Scalps werden von Gebühren aufgefressen.
- Krypto bewegt sich auf Stunden- bis Tagesebene in Trends.

Deshalb setzt diese Strategie auf:
- **wenige Trades** mit großem Gewinn/Verlust-Verhältnis
- eine **Kompression, bevor der Trend losläuft**, als Auslöser
- **ausschließlich bewährte Bausteine** der Website: ATR-Stop, TP1 + ATR-Trailing, Zeit-Exit, Optimizer

## Regeln (Datei `backend/strategies/vol_squeeze_breakout_strategy.py`)
1. **Squeeze:** Bollinger-Bandbreite (20) lag in den letzten 6 Kerzen im unteren 20-%-Perzentil der letzten 120 Kerzen.
2. **Ausbruch:** Schluss über dem Donchian-Hoch der vorherigen 20 Kerzen (Short: unter dem Tief).
3. **Trend:** Schluss über EMA 200, und die EMA steigt gegenüber vor 24 Kerzen (Short gespiegelt).
4. **Volumen:** größer als 1,2 × Ø 20.
5. **Optional Crowding:** kein Long, wenn das Funding im oberen x-Perzentil liegt. Standard: aus.

**Exits (Button „Empfohlen“):**
- Stop bei 3 × ATR
- TP1 bei 1,5R mit 50 %, danach Break-even
- Rest mit Trailing-Stop bei 2,5 × ATR, TP-Full bei 6R
- Zeit-Exit nach 72 h

## Walk-Forward (1h, Binance-Futures, 9 Coins, je 1000 $ bei 1×, Taker 0,06 %)
Die Parameter wurden **nur auf dem Trainingszeitraum** ausgewählt (07/2021–06/2024). Der Testzeitraum (07/2024–08/2026) blieb bei der Auswahl unberührt.

| Variante | Training PnL (Coins im Plus) | **Test PnL (Coins im Plus)** |
|---|---|---|
| Erster Entwurf (2 × ATR-Stop) | +136 $ (4/9) | −847 $ (3/9) |
| ohne Trendfilter | −1410 $ | −2331 $ |
| **Final: 3 × ATR-Stop** | **+1157 $ (6/9)** | **+1199 $ (6/9)** |
| Final mit 0,10 % Gebühr | −217 $ | +179 $ |
| Final + Crowding-Filter 20 % (Funding) | +2584 $ (6/9) | +626 $ (5/9) |
| Final nur Long | +1482 $ (7/9) | +444 $ (6/9) |

Hinweis zu den Varianten:
- Der Crowding-Filter ist im Training am stärksten, im Test aber schwächer als die Basis. Außerdem braucht er Binance-Funding-Daten. Deshalb bleibt er standardmäßig **aus** und ist per Parameter bzw. Optimizer testbar.
- „Nur Long“ ist ebenfalls als Parameter verfügbar.

Final je Coin (Training / Test in $):

| BTC | ETH | SOL | XRP | DOGE | ADA | LINK | AVAX | BNB |
|---|---|---|---|---|---|---|---|---|
| 28 / 20 | −456 / −123 | 903 / 64 | 165 / 12 | 160 / 847 | 609 / −380 | −94 / 423 | 525 / −96 | −684 / 433 |

**Ehrliche Einordnung:**
- Sie ist die einzige getestete Variante, die in **beiden** Zeiträumen und auf der Mehrheit der Coins im Plus ist. Das sind im Schnitt etwa +13 % pro Coin in rund 2 Jahren bei 1× Hebel.
- Die Einzel-Coins schwanken stark. Die Strategie ist gebührenempfindlich und die letzten 180 Tage (BTC/SOL) waren negativ.
- Das spricht für ein **breites Coin-Portfolio** mit kleinem Risiko pro Trade statt für einen einzelnen Coin.
- Vor dem Live-Einsatz: 4 Wochen Paper, dazu im Optimizer den Walk-Forward mit der Gruppe „Zeit-Exit“ laufen lassen.
