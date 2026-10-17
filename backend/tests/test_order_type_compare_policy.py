"""Regression 10/2026: Market-vs-Limit-Vergleich im Backtester, Limit-Fallback,
Order-Art je Setup beim KI-Trader."""
import inspect

from services import order_compare as oc
from services import setup_order_policy as sop


def _row(sid, **kw):
    base = {"strategy_id": sid, "strategy_name": sid, "trades": 10, "wins": 6, "losses": 4,
            "pnl": 10.0, "fees": 2.0, "max_drawdown": 3.0}
    return {**base, **kw}


def test_compare_recommends_limit_only_when_clearly_better_and_filled():
    m = [_row("s", pnl=10.0)]
    good = [_row("s", pnl=20.0, limit_filled=8, limit_expired=2)]
    thin = [_row("s", pnl=20.0, limit_filled=3, limit_expired=7)]
    same = [_row("s", pnl=10.5, limit_filled=9, limit_expired=1)]
    assert oc.build(m, good, 100)["s"]["choice"] == "limit"
    assert oc.build(m, thin, 100)["s"]["choice"] == "market"
    assert "optimistisch" in oc.build(m, thin, 100)["s"]["why"]
    assert oc.build(m, same, 100)["s"]["choice"] == "market"
    r = oc.build(m, good, 100)["s"]
    assert r["limit"]["fill_rate"] == 0.8 and r["market"]["fill_rate"] is None


def test_compare_never_filled():
    assert oc.build([_row("s")], [_row("s", trades=0, limit_expired=10)], 100)["s"]["choice"] == "market"


def test_backtester_fallback_and_compare_wired():
    from services import backtester as bt
    src = inspect.getsource(bt.simulate_pair)
    assert "limit_fallback_market" in src and '"limit_fallback"' in src
    src = inspect.getsource(bt.run_backtest)
    assert "compare_order_types" in src and "order_compare" in src
    from routers import backtest
    assert '"compare_order_types"' in inspect.getsource(backtest)


def test_policy_presets():
    assert sop.decide("breakout", {})[0] == "market"
    assert sop.decide("pullback", {})[0] == "maker"
    assert sop.decide("pullback", {}, {"pullback": "market"}) == ("market", "manuell festgelegt", "manual")


def test_policy_learns_and_switches_back():
    st = {"pullback": {"attempts": 10, "fills": 2}}
    assert sop.decide("pullback", st)[0:3:2] == ("market", "learned")
    worse = {"pullback": {"maker": {"n": 10, "avg_r": -0.2}, "market": {"n": 10, "avg_r": 0.3}}}
    assert sop.decide("pullback", worse)[0] == "market"
    # Daten drehen sich -> zurück zur Vorgabe (Limit)
    better = {"pullback": {"attempts": 10, "fills": 8, "maker": {"n": 10, "avg_r": 0.4}, "market": {"n": 10, "avg_r": 0.3}}}
    assert sop.decide("pullback", better)[0] == "maker"
    brk = {"breakout": {"attempts": 10, "fills": 7, "maker": {"n": 10, "avg_r": 0.6}, "market": {"n": 10, "avg_r": 0.2}}}
    assert sop.decide("breakout", brk)[0:3:2] == ("maker", "learned")


def test_aggregate_from_trades():
    trades = [{"setup": "pullback", "order_kind": "maker", "realized_pnl": 2, "risk_usdt": 1},
              {"setup": "pullback", "order_kind": "taker_fallback", "realized_pnl": -1, "risk_usdt": 1},
              {"setup": "pullback", "order_kind": "market", "realized_pnl": 1, "risk_usdt": 1},
              {"setup": "pullback", "order_kind": "limit_live", "realized_pnl": 5, "risk_usdt": 1}]
    s = sop.aggregate(trades, {"pullback": [{"filled": True}, {"filled": False}]})["pullback"]
    assert s["maker"] == {"n": 1, "sum_r": 2.0, "avg_r": 2.0}
    assert s["market"]["n"] == 2 and s["attempts"] == 2 and s["fills"] == 1


def test_ai_engine_uses_policy_and_existing_maker_execution():
    from services import ai_engine, bitunix_trade
    src = inspect.getsource(ai_engine)
    assert "setup_order_policy.decide(" in src and '"maker_policy": "setup"' in src
    bsrc = inspect.getsource(bitunix_trade.BitunixTrader._record_maker_attempt) \
        if hasattr(bitunix_trade, "BitunixTrader") else inspect.getsource(bitunix_trade)
    assert "by_setup" in bsrc
