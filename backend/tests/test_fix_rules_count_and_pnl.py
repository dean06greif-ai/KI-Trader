"""Regressionstests (Nutzer-Report 26.08.):

1. Richtungs-bewusste Regel-Zählung: Bei einem LONG-Signal zählen nur die
   LONG-Regeln (rules_met_count/rules_total). Vorher wurden Long+Short-Regeln
   gemischt gezählt, wodurch require_all_rules Custom-Strategien mit beiden
   Richtungen IMMER blockierte (Live 0 Trades, Backtest voll: Paritäts-Bug
   zum Fast-Path in services/fast_sim.py, der schon richtungs-bewusst zählt).
2. parse_closed_position: Sind Fees bereits in realizedPNL enthalten, wird
   das Funding NICHT nochmal addiert (Website-PnL == Bitunix-App-PnL).
3. require_all_rules blockiert keine Pre-Signale (eigener Schalter
   trade_pre_signals) und ist jetzt Standard (fest verankert).
"""
import pytest

from services.bitunix_trade import parse_closed_position, DEFAULT_COIN_CFG
from strategies.custom_strategy import CustomStrategy


def _candles(n=120, base=100.0, step=0.5):
    out = []
    for i in range(n):
        px = base + i * step
        out.append({"timestamp": 1700000000000 + i * 60000,
                    "open": px, "high": px + 0.3, "low": px - 0.3,
                    "close": px + 0.1, "volume": 1000.0})
    return out


DEFINITION = {
    "id": "custom_test1", "name": "Test Custom", "timeframe": "1m",
    "indicators": {"ema_fast_period": 5, "ema_slow_period": 10, "rsi_period": 14},
    "long_rules": [
        {"indicator": "price", "op": ">", "value": "ema_slow", "label": "Preis > EMA"},
        {"indicator": "rsi", "op": ">", "value": 20, "label": "RSI > 20"},
    ],
    "short_rules": [
        {"indicator": "price", "op": "<", "value": "ema_slow", "label": "Preis < EMA"},
        {"indicator": "rsi", "op": "<", "value": 80, "label": "RSI < 80"},
    ],
    "sl_mode": "percent", "sl_percent": 2.0, "crv_target": 2.0,
}


class TestDirectionAwareRuleCount:
    def test_custom_long_signal_counts_only_long_rules(self):
        strat = CustomStrategy(dict(DEFINITION))
        sig = strat.check_signal(_candles(), "BTCUSDT", {})
        assert sig is not None and sig["type"] == "LONG"
        # 2 Long-Regeln, beide erfüllt -> 2/2 (NICHT 2/4 über alle Regeln)
        assert sig["rules_total"] == 2
        assert sig["rules_met_count"] == 2

    def test_require_all_rules_would_pass(self):
        strat = CustomStrategy(dict(DEFINITION))
        sig = strat.check_signal(_candles(), "BTCUSDT", {})
        # exakt der Guard aus AutoTradeManager.on_signal / backtester
        blocked = (sig.get("rules_total")
                   and (sig.get("rules_met_count") or 0) < sig["rules_total"])
        assert not blocked

    def test_default_require_all_rules_enabled(self):
        assert DEFAULT_COIN_CFG["require_all_rules"] is True


class TestFundingNotDoubleCounted:
    def test_fee_included_keeps_exchange_pnl(self):
        # Realer Fall (BTC 25.08.): App zeigt +29.876 (realizedPNL inkl.
        # Fees+Funding), Website zeigte +30.478 (Funding doppelt addiert).
        # closePrice = Ø-Close (Teilschließung 0.0095 @ 79712 + Rest @ 79435.6).
        hist = {"code": 0, "data": {"positionList": [{
            "positionId": "btc1", "symbol": "BTCUSDT", "maxQty": "0.0479",
            "entryPrice": "78784.9", "closePrice": "79490.4", "side": "BUY",
            "fee": "4.159", "funding": "0.602", "realizedPNL": "29.876",
        }]}}
        r = parse_closed_position(hist, "btc1")
        assert r["fee_included_in_pnl"] is True
        assert r["net_pnl"] == pytest.approx(29.876)

    def test_fee_excluded_still_adds_funding(self):
        hist = {"code": 0, "data": {"positionList": [{
            "positionId": "p2", "symbol": "BTCUSDT", "maxQty": "0.5",
            "entryPrice": "60000", "closePrice": "61000", "side": "BUY",
            "fee": "36.3", "funding": "-1.2", "realizedPNL": "500",
        }]}}
        r = parse_closed_position(hist, "p2")
        assert r["fee_included_in_pnl"] is False
        assert r["net_pnl"] == pytest.approx(462.5)
