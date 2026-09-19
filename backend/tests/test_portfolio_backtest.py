"""Unit-Tests Portfolio-Backtest (Audit 3.5) – reine Kernfunktionen, ohne Netzwerk."""
import pytest

from services.portfolio_backtest import (
    DEFAULT_PORTFOLIO_CFG, apply_outage, apply_slippage, correlation_pairs,
    daily_pnl_by_symbol, normalize_trades, outage_windows, portfolio_replay,
    run_portfolio_analysis, sanitize_portfolio_cfg,
)

H = 3600 * 1000
T0 = 1750000000000  # fixe Basis (ms)


def _t(symbol, open_h, close_h, pnl, risk=10.0, qty=1.0, entry=100.0, fees=0.2, sid="s1"):
    from datetime import datetime, timezone
    iso = lambda h: datetime.fromtimestamp((T0 + h * H) / 1000, tz=timezone.utc).isoformat()
    return {"strategy_id": sid, "symbol": symbol, "opened": iso(open_h), "closed": iso(close_h),
            "pnl": pnl, "fees": fees, "risk": risk, "qty": qty, "entry": entry}


def _cfg(**kw):
    return sanitize_portfolio_cfg(kw)


def test_normalize_and_sort():
    rows = normalize_trades([_t("ETHUSDT", 5, 6, 1.0), _t("BTCUSDT", 1, 3, -2.0),
                             {"opened": "kaputt", "closed": None}])
    assert [r["symbol"] for r in rows] == ["BTCUSDT", "ETHUSDT"]
    assert rows[0]["risk_usdt"] == 10.0 and rows[0]["notional"] == 100.0
    assert rows[0]["cluster"]  # Anlageklasse gesetzt


def test_replay_max_positions_and_sequence():
    # 3 überlappende Trades, max 2 gleichzeitig -> 1 übersprungen
    trades = [_t("BTCUSDT", 0, 10, 5.0), _t("ETHUSDT", 1, 10, 5.0), _t("SOLUSDT", 2, 10, 5.0),
              _t("ADAUSDT", 11, 12, 5.0)]  # nach den Closes -> wird genommen
    res = portfolio_replay(normalize_trades(trades),
                           _cfg(start_capital=1000, max_open_trades=2,
                                max_portfolio_risk_pct=0, max_cluster_risk_pct=0), 100.0)
    assert res["trades"] == 3
    assert res["skipped"]["max_positions"] == 1
    assert res["max_concurrent"] == 2
    assert res["pnl"] == 15.0 and res["final_equity"] == 1015.0


def test_replay_capital_limit():
    # Kapital 150, Marge 100 je Trade -> zweiter paralleler Trade passt nicht
    trades = [_t("BTCUSDT", 0, 10, 5.0), _t("ETHUSDT", 1, 10, 5.0)]
    res = portfolio_replay(normalize_trades(trades),
                           _cfg(start_capital=150, max_open_trades=9,
                                max_portfolio_risk_pct=0, max_cluster_risk_pct=0), 100.0)
    assert res["trades"] == 1 and res["skipped"]["kapital"] == 1


def test_replay_risk_budget_and_cluster():
    # Risiko je Trade 10 USDT, Budget 6% von 250 = 15 -> zweiter paralleler Trade blockt
    trades = [_t("BTCUSDT", 0, 10, 1.0), _t("ETHUSDT", 1, 10, 1.0)]
    res = portfolio_replay(normalize_trades(trades),
                           _cfg(start_capital=250, per_trade_margin=50, max_open_trades=9,
                                max_portfolio_risk_pct=6, max_cluster_risk_pct=0), 100.0)
    # Skalierung 50/100: Risiko je Trade 5, 5+5=10 <= 15 -> beide ok
    assert res["trades"] == 2
    res2 = portfolio_replay(normalize_trades(trades),
                            _cfg(start_capital=250, per_trade_margin=50, max_open_trades=9,
                                 max_portfolio_risk_pct=3, max_cluster_risk_pct=0), 100.0)
    assert res2["trades"] == 1 and res2["skipped"]["risikobudget"] == 1
    # Cluster-Limit (beide Krypto): 3% Cluster = 7.5 -> zweiter blockt
    res3 = portfolio_replay(normalize_trades(trades),
                            _cfg(start_capital=250, per_trade_margin=50, max_open_trades=9,
                                 max_portfolio_risk_pct=0, max_cluster_risk_pct=3), 100.0)
    assert res3["trades"] == 1 and res3["skipped"]["cluster"] == 1


def test_replay_drawdown_scaling():
    # Marge 200 statt Sim-100 -> PnL ×2; Verlust dann Gewinn -> DD = 20
    trades = [_t("BTCUSDT", 0, 1, -10.0), _t("BTCUSDT", 2, 3, 30.0)]
    res = portfolio_replay(normalize_trades(trades),
                           _cfg(start_capital=1000, per_trade_margin=200,
                                max_portfolio_risk_pct=0, max_cluster_risk_pct=0), 100.0)
    assert res["pnl"] == 40.0
    assert res["max_drawdown"] == 20.0
    assert res["per_trade_margin"] == 200.0


def test_outage_windows_and_scenario():
    ws = outage_windows(0, 100 * H, 2, 1.0)
    assert len(ws) == 2 and all(b - a == H for a, b in ws)
    # Fenster bei count=2: Start + 33.33h bzw. 66.67h, je 1h lang
    rows = normalize_trades([
        _t("BTCUSDT", 33.5, 40, 9.0),  # Entry im 1. Fenster -> entfällt
        _t("ETHUSDT", 10, 67, 9.0),    # Exit im 2. Fenster -> SL: -(10+0.2)
        _t("SOLUSDT", 90, 95, -20.0, risk=10.0),  # Exit-Worst-Case nie besser als real
    ])
    out, meta = apply_outage(rows, outage_windows(T0, T0 + 100 * H, 2, 1.0))
    assert meta["entries_dropped"] == 1 and meta["exits_forced_sl"] == 1
    eth = next(r for r in out if r["symbol"] == "ETHUSDT")
    assert eth["pnl"] == -(10.0 + 0.2) and eth.get("outage_forced_sl")
    sol = next(r for r in out if r["symbol"] == "SOLUSDT")
    assert sol["pnl"] == -20.0  # bleibt schlechter als der SL-Worst-Case


def test_slippage_scenario():
    rows = normalize_trades([_t("BTCUSDT", 0, 1, 10.0, qty=2.0, entry=100.0)])
    out = apply_slippage(rows, 0.05)  # 0.05% je Seite auf Notional 200 -> 0.2
    assert out[0]["pnl"] == pytest.approx(10.0 - 0.2)


def test_correlation_pairs():
    daily = {"BTCUSDT": {f"2026-06-{d:02d}": float(d) for d in range(1, 8)},
             "ETHUSDT": {f"2026-06-{d:02d}": float(d) * 2 for d in range(1, 8)},
             "SOLUSDT": {f"2026-06-{d:02d}": float(-d) for d in range(1, 8)},
             "DOGEUSDT": {"2026-06-01": 1.0}}  # zu wenige Tage -> ausgelassen
    res = correlation_pairs(daily, min_days=5)
    corr = {(p["a"], p["b"]): p["corr"] for p in res["pairs"]}
    assert corr[("BTCUSDT", "ETHUSDT")] == pytest.approx(1.0)
    assert corr[("BTCUSDT", "SOLUSDT")] == pytest.approx(-1.0)
    assert res["n_pairs"] == 3  # DOGE-Paare fehlen


def test_run_portfolio_analysis_end_to_end():
    trades = [_t("BTCUSDT", 0, 5, 10.0), _t("ETHUSDT", 1, 6, -4.0),
              _t("BTCUSDT", 30, 31, 2.0)]
    res = run_portfolio_analysis(trades, 100.0, _cfg(start_capital=500, outage_count=1))
    assert res["portfolio"]["trades"] == 3
    assert "slippage_stress" in res["scenarios"] and "outage" in res["scenarios"]
    assert res["portfolio"]["equity_curve"]
    assert "equity_curve" not in res["scenarios"]["slippage_stress"]


def test_sanitize_cfg():
    cfg = sanitize_portfolio_cfg({"start_capital": "2000", "max_open_trades": 99,
                                  "unbekannt": 1, "slippage_pct": -5})
    assert cfg["start_capital"] == 2000.0
    assert cfg["max_open_trades"] == 50
    assert cfg["slippage_pct"] == 0.0
    assert "unbekannt" not in cfg
    assert sanitize_portfolio_cfg(None) == DEFAULT_PORTFOLIO_CFG
