"""Regressionstests: Copilot – Min-Trades-Empfehlung, Prompt-Wissen,
Token-sparende Ergebnis-Verschlankung. Unit-Tests ohne LLM/Netzwerk."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import strategy_copilot as cp  # noqa: E402


def test_min_trades_scales_with_assets_and_years():
    one = cp.min_trades_hint({"coins": ["BTCUSDT"], "days": 365, "timeframe": "1h"})
    ten = cp.min_trades_hint({"coins": [f"C{i}" for i in range(10)], "days": 1440,
                              "timeframe": "1h", "min_trades": 300})
    assert one["recommended_min_trades"] == 100  # 1 × 1 × 80 -> Untergrenze 100
    # 10 Assets × 3.95 Jahre × 80 = ~3156 -> 300 ist viel zu wenig
    assert ten["recommended_min_trades"] > 2500
    assert ten["too_low"] is True
    txt = cp.min_trades_hint_text({"coins": [f"C{i}" for i in range(10)], "days": 1440,
                                   "timeframe": "1h", "min_trades": 300})
    assert "MIN-TRADES-EMPFEHLUNG" in txt and "ZU NIEDRIG" in txt


def test_min_trades_depends_on_timeframe():
    fast = cp.min_trades_hint({"coins": ["BTCUSDT", "ETHUSDT"], "days": 720, "timeframe": "5m"})
    slow = cp.min_trades_hint({"coins": ["BTCUSDT", "ETHUSDT"], "days": 720, "timeframe": "4h"})
    assert fast["recommended_min_trades"] > slow["recommended_min_trades"]
    assert cp.min_trades_hint({"coins": [], "days": 0}) is None
    assert cp.min_trades_hint(None) is None


def test_prompt_contains_overfitting_guidance_and_full_settings_schema():
    p = cp.SYSTEM_PROMPT
    for needle in ("OPTIMIEREN OHNE OVERFITTING", "MIN-TRADES-EMPFEHLUNG", "opt_groups",
                   "rule_timeframes", "robustness", "sessions", "execution", "KÜRZE"):
        assert needle in p, needle


def test_slim_result_drops_bulky_lists_but_keeps_metrics():
    res = {"metrics": {"pnl": 10}, "steps": [1] * 500, "refine_log": ["x"] * 300,
           "top5": [{"rank": 1, "metrics": {"pnl": 1}, "equity": [1, 2, 3], "score": 2}],
           "per_strategy": [{"strategy_id": "a", "pnl": 1, "last_trades": [1, 2, 3]}],
           "explore_report": {"tested": 5, "champions": [{"big": "x" * 1000}], "stop_reason": "t"},
           "definition": {"name": "d"}}
    slim = cp._slim_result(res)
    assert "steps" not in slim and "refine_log" not in slim
    assert slim["metrics"] == {"pnl": 10} and slim["definition"] == {"name": "d"}
    assert "equity" not in slim["top5"][0] and slim["top5"][0]["score"] == 2
    assert "last_trades" not in slim["per_strategy"][0]
    assert "champions" not in slim["explore_report"] and slim["explore_report"]["tested"] == 5
