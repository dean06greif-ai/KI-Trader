"""Regressionstests: KI-Trader Live-Diagnose (deterministische Befund-Logik)."""
from services import ai_diagnosis as diag


def _rows(spec):
    out = []
    for pnl, extra in spec:
        out.append({"realized_pnl": pnl, **extra})
    return out


def test_agg_basic():
    a = diag._agg(_rows([(2.0, {}), (-1.0, {}), (0.0, {})]))
    assert a["trades"] == 3 and a["wins"] == 1 and a["losses"] == 1
    assert a["win_rate"] == 50.0
    assert a["pnl"] == 1.0
    assert diag._agg([]) == {"trades": 0, "wins": 0, "losses": 0,
                             "win_rate": None, "pnl": 0.0, "avg_pnl": None}


def test_group_by_setup():
    rows = [{"realized_pnl": 1, "setup": "breakout"},
            {"realized_pnl": -2, "setup": "breakout"},
            {"realized_pnl": 3, "setup": "trend_follow"}]
    g = diag._group(rows, "setup")
    assert g["breakout"]["trades"] == 2 and g["trend_follow"]["pnl"] == 3.0


def test_findings_low_live_winrate_flagged():
    live = {"trades": 8, "wins": 2, "losses": 6, "win_rate": 25.0, "pnl": -40.0}
    f = diag.build_findings(live, {"trades": 0, "win_rate": None}, {}, {}, {},
                            {"measured_trades": 0}, [], [], [], 0, 0)
    assert any(x["severity"] == "kritisch" and "25.0%" in x["text"] for x in f)


def test_findings_paper_live_gap_and_slippage():
    live = {"trades": 7, "wins": 1, "losses": 6, "win_rate": 14.3, "pnl": -30.0}
    paper = {"trades": 50, "wins": 28, "losses": 22, "win_rate": 55.1, "pnl": 80.0}
    slippage = {"measured_trades": 12, "avg_slippage_pct": 0.08,
                "total_slippage_usdt": -9.5}
    f = diag.build_findings(live, paper, {}, {}, {}, slippage, [], [], [], 0, 0)
    texts = " | ".join(x["text"] for x in f)
    assert "Kluft" in texts                      # Paper-vs-Live-Gap erkannt
    assert "Slippage" in texts and "Limit-Entries" in texts


def test_findings_losing_setup_with_paper_contrast():
    per_live = {"momentum_news": {"trades": 9, "wins": 3, "losses": 6,
                                  "win_rate": 33.3, "pnl": -25.0}}
    per_paper = {"momentum_news": {"trades": 30, "wins": 18, "losses": 12,
                                   "win_rate": 60.0, "pnl": 40.0}}
    f = diag.build_findings({"trades": 9, "win_rate": 33.3, "pnl": -25.0},
                            {"trades": 30, "win_rate": 60.0},
                            per_live, per_paper, {}, {"measured_trades": 0},
                            [], [], [], 0, 0)
    hit = [x for x in f if "momentum_news" in x["text"] and x["frage"] == "setups"]
    assert hit and "Ausführungs" in hit[0]["text"]


def test_findings_missing_setups_and_proven_blocked():
    setups = [
        {"setup": "range_fade", "trades": 0, "live_ready": False,
         "live_reason": "neues Setup – noch keine echten Daten gesammelt",
         "verdict": None},
        {"setup": "breakout", "trades": 25, "live_ready": False,
         "live_reason": "Setup-Urteil 'test'", "verdict": "bewährt"},
    ]
    f = diag.build_findings({"trades": 0, "win_rate": None}, {"trades": 0, "win_rate": None},
                            {}, {}, {}, {"measured_trades": 0}, [], setups, [], 0, 0)
    texts = " | ".join(x["text"] for x in f)
    assert "NIE gehandelt" in texts        # fehlendes Setup erkannt
    assert "datenreif" in texts            # bewährtes, aber blockiertes Setup


def test_findings_stale_data_and_guards():
    dq = [{"symbol": "EURUSD", "candles": 500, "last_candle_age_min": 45.0,
           "orderflow_real": False}]
    guards = [{"key": "stale", "label": "Stale-Price-Guard", "count": 12},
              {"key": "master", "label": "MasterPrompt-Limits", "count": 2}]
    f = diag.build_findings({"trades": 0, "win_rate": None}, {"trades": 0, "win_rate": None},
                            {}, {}, {}, {"measured_trades": 0}, guards, [], dq, 7, 2)
    texts = " | ".join(x["text"] for x in f)
    assert "Veraltete Kursdaten" in texts and "EURUSD" in texts
    assert "Stale-Price-Guard" in texts
    assert "Setup-Live-Gate" in texts and "Tagesverlust-Limit" in texts


def test_findings_clean_data_no_noise():
    f = diag.build_findings({"trades": 0, "win_rate": None}, {"trades": 0, "win_rate": None},
                            {}, {}, {}, {"measured_trades": 0}, [], [], [], 0, 0)
    assert all(x["severity"] == "info" for x in f)
