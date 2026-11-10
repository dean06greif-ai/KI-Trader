"""06/2026: Copilot-Wissen bleibt mit den Code-Konstanten synchron + Assistent-Kontext."""
from services import dynamic_strategy as dyn
from services import evidence_stats as es
from services import regime_assistant as ra
from services import regime_copilot_knowledge as rck
from services import strategy_copilot as sc


def test_strategy_prompt_numbers_match_constants():
    p = sc.SYSTEM_PROMPT
    assert f"ab {dyn.ROBUST_MIN_TEST_TRADES} Test-Trades" in p
    assert f"≥ {dyn.ROBUST_MIN_PROB * 100:.0f} %" in p
    assert f"n/(n+{es.PRIOR_TRADES})" in p
    for k in ("breakeven", "auto_leverage", "time_exit", "entry_order", "direction"):
        assert f'"{k}"' in p
    assert '"be": true' not in p and '"sl": true' not in p


def test_assistant_lines_grey_and_current(monkeypatch):
    monkeypatch.setitem(ra._latest, "crypto", {
        "days": 150, "created_at": "2026-11-06T10:00:00", "recipe": {"macro_tf": "1d", "primary_tf": "8h"},
        "summary": {"light": "grau", "grade": "zu wenig Daten", "scores": {"combined": None, "primary": None},
                    "best_label": None, "recommendation": "Zu wenig Daten – Zeitraum erhöhen"},
        "current": [{"symbol": "BTCUSDT", "macro": "sideways", "primary": "up", "primary_prob": 0.77,
                     "label": "Aufwärtsbewegung in einer Range", "allow_long": True, "allow_short": False,
                     "size_factor": 0.75, "score_primary": None}]})
    text = "\n".join(rck.assistant_lines())
    assert "None" not in text
    assert "zu wenig Daten" in text and "Zeitraum erhöhen" in text
    assert "BTCUSDT jetzt: Makro sideways · Haupt up 77 %" in text
    assert "grau = zu wenig Daten" in rck.static_knowledge()


def test_current_states():
    rows = [{"symbol": "X", "error": "zu wenig Kerzen"},
            {"symbol": "Y", "macro": {"direction": "up"}, "primary": {"direction": "up", "prob": 0.8},
             "combined": {"label": "Starker Aufwärtstrend", "allow_long": True, "allow_short": False,
                          "size_factor": 1.0},
             "eval": {"variants": {"primary": {"score": 61.0}, "combined": {"score": 60.0}}}}]
    out = ra.current_states(rows)
    assert out[0]["error"] and out[1]["score_primary"] == 61.0 and out[1]["allow_long"] is True
