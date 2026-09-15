"""Audit 3.4: Netto-Erfolgsbericht je Policy-Version. Ohne Netzwerk."""
from services import policy_fingerprint as pf
from services import setup_variant


def _t(fp, pnl, mode="live", collect=False, **kw):
    return {"policy_version": fp, "realized_pnl": pnl, "mode": mode,
            "data_collection": collect,
            "result": "win" if pnl > 0 else "loss",
            "fees_paid": 0.1, "slippage_usdt": 0.02,
            "risk_usdt": 10.0, "opened_at": kw.pop("opened_at", "2026-06-25T10:00:00+00:00"),
            **kw}


def test_policy_report_rows_worlds_and_totals():
    fp_a = pf.build(prompt_hash="a", model="m1", gate_version=2)
    fp_b = pf.build(prompt_hash="b", model="m2")
    trades = [
        _t(fp_a, 20.0, mode="live"),
        _t(fp_a, -10.0, mode="live", opened_at="2026-06-26T10:00:00+00:00"),
        _t(fp_a, 5.0, mode="paper"),
        _t(fp_a, 2.0, mode="paper", collect=True),
        _t(fp_b, 1.0, mode="paper", opened_at="2026-06-27T09:00:00+00:00"),
        _t(None, 3.0, mode="live"),   # Alt-Trade ohne Fingerprint
    ]
    rows = setup_variant.policy_report_rows(trades)
    assert len(rows) == 3
    # jüngste Policy zuerst, Alt-Trades zuletzt
    assert rows[0]["combined"] == fp_b["combined"]
    assert rows[1]["combined"] == fp_a["combined"]
    assert rows[2]["combined"] is None and rows[2]["policy"] is None
    a = rows[1]
    assert a["total"]["trades"] == 4 and a["total"]["pnl"] == 17.0
    assert a["worlds"]["live"]["trades"] == 2 and a["worlds"]["live"]["pnl"] == 10.0
    assert a["worlds"]["live"]["wr"] == 50.0
    assert a["worlds"]["paper"]["trades"] == 1     # Sammel-Trade zählt NICHT als Paper
    assert a["worlds"]["collect"]["trades"] == 1
    # R in Geld: pnl / risk_usdt (20/10=2R, -10/10=-1R -> Ø live 0.5R)
    assert a["worlds"]["live"]["avg_r"] == 0.5
    assert a["total"]["fees"] == 0.4
    assert a["first_ts"].startswith("2026-06-25") and a["last_ts"].startswith("2026-06-26")
    assert a["policy"]["model"] == "m1" and a["policy"]["gate_version"] == 2


def test_policy_report_rows_empty_and_no_risk():
    assert setup_variant.policy_report_rows([]) == []
    fp = pf.build(prompt_hash="x")
    rows = setup_variant.policy_report_rows([
        {"policy_version": fp, "realized_pnl": 5.0, "mode": "live",
         "result": "win", "opened_at": "2026-06-25T00:00:00+00:00"}])
    assert rows[0]["total"]["avg_r"] is None       # ohne risk_usdt kein R
    assert rows[0]["total"]["wr"] == 100.0


def test_endpoint_wiring():
    import inspect

    from routers import analytics
    src = inspect.getsource(analytics)
    assert '"/api/analytics/policy-report"' in src
    assert "setup_variant.policy_report_rows" in src
    assert "token_estimate_total" in src           # Betriebskosten separat
