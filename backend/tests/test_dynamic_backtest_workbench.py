"""Regressionstests: dynamische Strategien im Backtester + Werkbank-Durchreichung.

Unit-Tests ohne laufendes Backend/Netzwerk: reine Funktionen von
services.dynamic_backtest und services.regime_opt.limit_segments_to_days sowie
die Werkbank-Body-Erzeugung (_search_body) mit den neuen Einstellungen.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import dynamic_backtest as dbt  # noqa: E402
from services import dynamic_workbench as wb  # noqa: E402
from services import regime_opt  # noqa: E402


def _m(trades, pnl, wr=55.0, dd=1.0):
    return {"trades": trades, "pnl": pnl, "win_rate": wr, "max_drawdown": dd}


class TestRecommend:
    def test_untraded(self):
        r = dbt.recommend({"traded": False}, [])
        assert r["action"] == "untraded"

    def test_trade_when_profitable(self):
        r = dbt.recommend({"traded": True, "metrics": _m(20, 12.0)}, [])
        assert r["action"] == "trade"

    def test_skip_when_losing(self):
        r = dbt.recommend({"traded": True, "metrics": _m(20, -5.0)}, [])
        assert r["action"] == "skip"
        assert "nicht handeln" in r["text"]

    def test_switch_when_alternative_clearly_better(self):
        alt = {"strategy_name": "Trend", "regime_label": "Aufwärts", "metrics": _m(15, 9.0)}
        r = dbt.recommend({"traded": True, "metrics": _m(20, 2.0)}, [alt])
        assert r["action"] == "switch"
        assert r["alternative"]["strategy_name"] == "Trend"

    def test_no_switch_for_marginal_alternative(self):
        alt = {"strategy_name": "Trend", "regime_label": "Aufwärts", "metrics": _m(15, 2.1)}
        r = dbt.recommend({"traded": True, "metrics": _m(20, 2.0)}, [alt])
        assert r["action"] == "trade"

    def test_alternatives_need_enough_trades(self):
        alt = {"strategy_name": "Trend", "regime_label": "Aufwärts", "metrics": _m(2, 50.0)}
        r = dbt.recommend({"traded": True, "metrics": _m(20, -1.0)}, [alt])
        assert r["action"] == "skip"

    def test_unclear_with_few_trades(self):
        r = dbt.recommend({"traded": True, "metrics": _m(2, 1.0)}, [])
        assert r["action"] == "unclear"


class TestEquityPoints:
    def test_points_carry_regime_and_running_equity(self):
        rows = [{"closed": "2026-01-02T00:00:00", "pnl": 2.0, "regime": 1, "symbol": "BTCUSDT", "side": "LONG"},
                {"closed": "2026-01-01T00:00:00", "pnl": -1.0, "regime": 0, "symbol": "BTCUSDT", "side": "SHORT"}]
        pts = dbt._equity_points(rows, {0: "A", 1: "B"})
        assert [p["equity"] for p in pts] == [-1.0, 1.0]
        assert pts[0]["regime_label"] == "A" and pts[1]["regime_label"] == "B"
        assert pts[1]["drawdown"] == 0.0 and pts[0]["drawdown"] == 1.0

    def test_pair_row_has_backtester_fields(self):
        rows = [{"closed": "2026-01-01T00:00:00", "pnl": 3.0, "fees": 0.1, "duration_min": 30,
                 "side": "LONG", "profit_secured": True, "liquidated": False}]
        row = dbt._pair_row("dyn_x", "Dyn", "BTCUSDT", "1h", 500, rows, 100.0)
        for k in ("trades", "wins", "losses", "breakevens", "win_rate", "pnl", "pnl_pct", "fees",
                  "avg_pnl", "max_drawdown", "max_drawdown_pct", "secured", "liquidations",
                  "avg_duration_min", "timeframe", "candles"):
            assert k in row, k
        assert row["dynamic"] is True and row["secured"] == 1 and row["avg_duration_min"] == 30


class TestLimitSegmentsToDays:
    def _seg(self, start_day, n_days, rid=0):
        cds = [{"timestamp": (start_day * 24 + h) * 3600000, "close": 1.0}
               for h in range(n_days * 24)]
        return {"regime": rid, "start_ts": cds[0]["timestamp"], "candles": cds, "n_bars": len(cds)}

    def test_no_limit_returns_input(self):
        segs = {"BTCUSDT": [self._seg(0, 5)]}
        assert regime_opt.limit_segments_to_days(segs, 0) is segs

    def test_old_segments_dropped_and_boundary_segment_clipped(self):
        # Ende = Tag 35 -> Fenster 10 Tage ~ ab Tag 25: Segment 0-5 weg,
        # Segment 20-30 wird am Fensteranfang abgeschnitten, 30-35 bleibt ganz
        segs = {"BTCUSDT": [self._seg(0, 5), self._seg(20, 10), self._seg(30, 5)]}
        out = regime_opt.limit_segments_to_days(segs, 10)
        kept = out["BTCUSDT"]
        assert len(kept) == 2
        assert kept[1]["start_ts"] == self._seg(30, 5)["start_ts"] and kept[1]["n_bars"] == 120
        assert kept[0]["start_ts"] > self._seg(20, 10)["start_ts"]
        assert 0 < kept[0]["n_bars"] <= 5 * 24 + 1


class TestWorkbenchSearchBody:
    def test_passthrough_settings(self):
        p = {"iterations": 30, "min_trades": 8, "timeframe": "15m", "days": 90,
             "max_capital": 250, "leverage": 5, "fee_percent": 0.04, "sessions": "09:00-17:00",
             "optimize": {"tpsl": True}, "indicators": ["rsi"], "regime_train_pct": 70,
             "regime_walk_forward": True}
        body = wb._search_body(p, "ra_1", 2, "params", "trend_following", 1)
        for k in wb.PASSTHROUGH_KEYS:
            if k in p:
                assert body[k] == p[k], k
        assert body["strategy_id"] == "trend_following"
        assert body["iterations"] == 30 and body["min_trades"] == 8

    def test_unset_settings_not_sent(self):
        body = wb._search_body({"iterations": 40}, "ra_1", 0, "discovery", None, 2)
        for k in wb.PASSTHROUGH_KEYS:
            assert k not in body
        assert body["iterations"] == 50 and "strategy_id" not in body


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q", "-n", "0"]))
