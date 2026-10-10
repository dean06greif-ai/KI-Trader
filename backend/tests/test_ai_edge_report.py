"""Edge-Bericht in R (services/ai_edge_report) – reine Funktionen."""
from services import ai_edge_report as er


def _t(pnl, risk=1.0, fees=0.2, tp1=False, conf=60, setup="a", mins=30):
    return {"realized_pnl": pnl, "risk_usdt": risk, "fees_paid": fees, "tp1_hit": tp1,
            "ai_confidence": conf, "setup": setup,
            "opened_at": "2026-10-01T10:00:00+00:00",
            "closed_at": f"2026-10-01T{10 + mins // 60:02d}:{mins % 60:02d}:00+00:00"}


def test_agg_r_numbers():
    rows = [_t(1.5, tp1=True), _t(-1.0), _t(-1.0), _t(-1.0)]
    a = er.agg_r(rows)
    assert a["with_risk"] == 4 and a["exp_r"] == -0.375 and a["fee_r"] == 0.2
    assert a["gross_r"] == round((-1.5 + 0.8) / 4, 3)
    assert a["tp1_rate"] == 25.0 and a["tp1_breakeven"] == 40.0     # 1/(1.5+1)


def test_rows_without_risk_are_ignored():
    a = er.agg_r([{"realized_pnl": 5, "risk_usdt": 0}])
    assert a == {"trades": 1, "with_risk": 0}
    assert er.edge_findings(a, {}, {}, {}, {}, {}, None)[0]["severity"] == "info"


def test_findings_flag_fees_confidence_llm_and_free_model():
    rows = [_t(-0.4, conf=50 + (i % 30)) for i in range(60)]
    overall = er.agg_r(rows)
    by_conf = er.group_r(rows, er.conf_key)
    by_source = {"LLM": er.agg_r(rows[:30]), "Regel-Trigger": er.agg_r([_t(0.2)] * 30)}
    f = er.edge_findings(overall, {"a": overall}, by_source, by_conf, {},
                         {"fee_guard_mult": 3.5, "live_gate_bypass_enabled": True},
                         "nvidia/x:free")
    text = " ".join(x["text"] for x in f)
    assert all(x["frage"] == "edge" for x in f)
    assert "Gebühren kosten" in text and "29 %" in text
    assert "LLM-Einstiege" in text and "Konfidenz trennt" in text and "Live-Bypass" in text
    assert "kostenloses Modell" in text and "Klar negative Setups" in text


def test_positive_setup_listed_and_short_holds():
    good = [_t(0.6, fees=0.05, setup="divergence") for _ in range(25)]
    short = [_t(-0.9, mins=5) for _ in range(25)]
    mid = [_t(0.1, mins=30) for _ in range(25)]
    by_setup = er.group_r(good, lambda t: t["setup"])
    by_hold = er.group_r(short + mid, er.hold_key)
    f = er.edge_findings(er.agg_r(good), by_setup, {}, {}, by_hold, {}, "gemini-3.5-flash")
    text = " ".join(x["text"] for x in f)
    assert "divergence" in text and "unter 15 min" in text and "kostenloses" not in text


def test_source_key():
    dec = {"1": {"source": "setup_trigger"}, "2": {"source": None}}
    assert er.source_key({"decision_id": "1"}, dec) == "Regel-Trigger"
    assert er.source_key({"decision_id": "2"}, dec) == "LLM"
    assert er.source_key({}, dec) == "unbekannt"
