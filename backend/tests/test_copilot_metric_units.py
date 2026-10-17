"""Regressionstests: Strategie-Copilot liest Metriken mit korrekten Einheiten.

Bug-Report: 1600 USDT Drawdown wurde vom Copiloten als 1600 % Drawdown
interpretiert. Abgedeckt:
  * describe_metrics: USDT-Beträge vs. Prozent eindeutig beschriftet, Bezug
    zum Startkapital
  * metrics_summary: Optimizer-Ergebnis (metrics) und Backtest-Ergebnis
    (per_strategy) inkl. Kapital aus result.config / settings
  * sanity_check: Drawdown-Einordnung mit Kapital, Einheiten-Warnung bei
    inkonsistentem max_drawdown_pct; bestehende Prüfungen unverändert
  * System-Prompt enthält die Einheiten-Regel
"""
from services import strategy_copilot as sc


def test_describe_metrics_labels_usdt_and_percent():
    rows = sc.describe_metrics({"trades": 40, "wins": 22, "losses": 18, "win_rate": 55.0,
                                "pnl": 820.5, "pnl_pct": 8.2, "max_drawdown": 1600.0,
                                "max_drawdown_pct": 16.0, "profit_factor": 1.4,
                                "avg_pnl": 20.51, "fees": 33.2, "avg_leverage": 5},
                               capital=10000)
    text = "\n".join(rows)
    assert "Max. Drawdown: 1600.00 USDT (= 16.0 % des Startkapitals 10000 USDT)" in text
    assert "Max. Drawdown: 16.0 %" in text
    assert "PnL: 820.50 USDT (= 8.2 % des Startkapitals 10000 USDT)" in text
    assert "Winrate: 55.0 %" in text and "Trades: 40" in text
    assert "Ø Hebel: 5 x" in text and "Gebühren: 33.20 USDT" in text
    assert "1600 %" not in text


def test_describe_metrics_without_capital_and_bad_values():
    rows = sc.describe_metrics({"max_drawdown": 1600, "pnl": "n/a", "trades": None})
    assert rows == ["Max. Drawdown: 1600.00 USDT"]
    assert sc.describe_metrics(None) == [] and sc.describe_metrics("x") == []


def test_metrics_summary_optimizer_result_uses_trade_params_capital():
    result = {"score": 1.2, "metrics": {"trades": 12, "pnl": 150.0, "max_drawdown": 1600.0,
                                        "max_drawdown_pct": 160.0},
              "trade_params": {"max_capital": 1000}}
    s = sc.metrics_summary(result)
    assert s.startswith("METRIKEN (mit Einheiten")
    assert "Startkapital 1000 USDT" in s
    assert "Max. Drawdown: 1600.00 USDT (= 160.0 % des Startkapitals 1000 USDT)" in s


def test_metrics_summary_backtest_per_strategy():
    result = {"days": 30, "config": {"max_capital": 100},
              "per_strategy": [
                  {"strategy_id": "a", "strategy_name": "Alpha", "trades": 9, "wins": 5,
                   "losses": 4, "win_rate": 55.6, "pnl": 12.5, "pnl_pct": 12.5,
                   "max_drawdown": 6.0, "max_drawdown_pct": 6.0, "fees": 1.1},
                  {"strategy_id": "b", "trades": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                   "max_drawdown": 0.0}],
              "per_pair": [{"strategy_id": "a", "symbol": "BTCUSDT", "trades": 4,
                            "pnl": 7.0, "max_drawdown": 2.0}]}
    s = sc.metrics_summary(result)
    assert "Pro Strategie:" in s and "- Alpha: Trades: 9" in s
    assert "Max. Drawdown: 6.00 USDT (= 6.0 % des Startkapitals 100 USDT)" in s
    assert "Pro Strategie×Coin:" in s and "a @ BTCUSDT" in s
    assert sc.metrics_summary({"days": 3}) == ""
    assert sc.metrics_summary(None) == ""


def test_metrics_summary_limits_rows():
    rows = [{"strategy_id": f"s{i}", "trades": 1, "pnl": 1.0} for i in range(12)]
    s = sc.metrics_summary({"per_strategy": rows}, max_rows=3)
    assert "… 9 weitere" in s and "s3" not in s


def test_sanity_check_drawdown_units_with_capital():
    m = {"trades": 10, "wins": 6, "losses": 4, "win_rate": 60.0, "pnl": 10.0,
         "avg_pnl": 1.0, "max_drawdown": 1600.0, "max_drawdown_pct": 16.0}
    notes = sc.sanity_check(m, capital=10000)
    assert any("1600.00 USDT = 16.0% des Startkapitals 10000 USDT" in n for n in notes)
    assert not any("prüfen" in n for n in notes)
    # inkonsistente Prozentangabe -> Warnung
    notes = sc.sanity_check({**m, "max_drawdown_pct": 1600.0}, capital=10000)
    assert any("Drawdown-Einheiten prüfen" in n for n in notes)
    assert any("max_drawdown_pct > 100%" in n for n in notes)
    # ohne Kapital: nur Einordnung absolut/relativ
    notes = sc.sanity_check(m)
    assert any("absolut" in n and "relativ" in n for n in notes)
    # Regressions: bestehende Prüfungen unverändert (keine DD-Felder -> leer)
    assert sc.sanity_check({"trades": 10, "wins": 6, "losses": 4, "win_rate": 60.0,
                            "pnl": 10.0, "avg_pnl": 1.0}) == []


def test_system_prompt_contains_unit_rule():
    assert "EINHEITEN" in sc.SYSTEM_PROMPT
    assert "1600 USDT" in sc.SYSTEM_PROMPT and "NICHT 1600 %" in sc.SYSTEM_PROMPT
