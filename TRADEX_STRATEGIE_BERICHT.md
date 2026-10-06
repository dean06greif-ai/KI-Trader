# TradeX VWAP Scalping (Horst v2): Umsetzung und Backtest-Bericht (29.09.2026)

## Umsetzung (modular, rückwärtskompatibel)
| Baustein | Datei |
|---|---|
| Strategie (Live-Checkliste + vektorisierter Fast-Path, gleiche numpy-Funktionen) | `backend/strategies/tradex_vwap_scalping_strategy.py` |
| Funding + Long/Short-Ratio (Konten), Perzentil gegen Historie, ohne Look-Ahead | `backend/services/market_positioning.py` |
| Generische Trade-Einstellungen im Backtester: `max_hold_minutes`, `entry_order_type` (limit/market), `limit_expiry_bars`, `maker_fee_percent`, `tp_order_type`, `allowed_sides` | `backend/services/backtester.py` |
| Optimizer-Gruppen `time_exit`, `entry_order`, `direction` (Strategie-Parameter automatisch) | `backend/services/optimizer.py`, `frontend/src/components/Optimizer.js` |
| Live: Zeit-Exit, Limit-/Maker-Entry, TP als Limit, Richtungs-Filter | `backend/services/bitunix_trade.py` |
| UI-Felder und Button „Empfohlen“ | `frontend/src/components/ExecCfgFields.js`, `Backtester.js` |
| Regressionstests | `backend/tests/test_tradex_vwap_scalping.py` |

Datenquellen:
- **Historie:** data.binance.vision, also Funding-Settlements und 5m-`count_long_short_ratio`, dieselbe Quelle wie bei TradeX.
- **Live:** Binance fapi, Fallback OKX.
- **Fehlende Daten:** Die Setup-Bedingung gilt dann als nicht erfüllt (konservativ).

## Backtest (1m, Binance-Futures-Kerzen, 1× Hebel, 1000 $, Taker 0,06 %, Maker 0,02 %)
| Variante | BTC 01/22–08/26 | ETH | SOL |
|---|---|---|---|
| Horst-Logik ohne Setup-Filter | −216 %, PF 0,78 | – | – |
| TradeX-Original (2,0σ, TP 0,6 %, Market-TP) | −31 %, PF 0,92 | – | – |
| TradeX-Original mit Limit-TP | −6 %, PF 0,98 | −120 % | −51 % |
| **Verbessert: 2,5σ, TP 0,8 %, Limit-TP (Standard)** | **+26 %, PF 1,16, DD 19 %** | −16 % | −16 % |

Kontrolle: Die letzten 90 Tage auf BTC mit TradeX-Originalwerten ergeben 32 Trades, 66 % Trefferquote und +5,5 %. Im Video: 38 Trades, 58 % und +5 %.

**Ehrliche Einordnung:**
- Der Setup-Filter (Funding/L/S) wirkt deutlich.
- Der Edge ist aber klein und hängt stark von den Gebühren ab. BTC war 2022–2024 positiv und ist seit 2025 negativ, ETH und SOL sind negativ.
- Empfehlung: nur BTC, nur Paper/Shadow, mit Walk-Forward-Optimierung im Optimizer. Nicht live ohne Beobachtungsphase.
