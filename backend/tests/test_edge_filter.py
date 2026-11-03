"""Edge-Filter (services/edge_filter.py) + Edge-Bericht-Gruppen – reine Regressionstests."""
from services import edge_filter as ef
from services import ai_edge_report as er


def test_fee_r_roundtrip_share_of_risk():
    # 0,06 %/Seite, SL 1,2 % -> 0,12 / 1,2 = 0,10 R
    assert ef.fee_r(0.06, 100.0, 98.8) == 0.1
    assert ef.fee_r(0.06, 100.0, 100.0) is None
    assert ef.fee_r(0.06, 0, 1) is None


def test_bias_aligned():
    assert ef.bias_aligned("LONG", "up") is True
    assert ef.bias_aligned("SHORT", "down") is True
    assert ef.bias_aligned("LONG", "down") is False
    assert ef.bias_aligned("SHORT", "flat") is False
    assert ef.bias_aligned("LONG", None) is None


def test_evaluate_pass_and_reasons():
    ok = ef.evaluate({}, side="LONG", asset_class="crypto", fee_r_est=0.08, bias="up")
    assert ok["ok"] and ok["reasons"] == []
    bad = ef.evaluate({}, side="LONG", asset_class="resources", fee_r_est=0.25, bias="down")
    assert not bad["ok"] and len(bad["reasons"]) == 3
    nobias = ef.evaluate({}, side="SHORT", asset_class="crypto", fee_r_est=0.05, bias=None)
    assert not nobias["ok"] and "Bias" in nobias["reasons"][0]


def test_evaluate_respects_switches():
    cfg = {"edge_filter_require_bias": False, "edge_filter_crypto_only": False,
           "edge_filter_max_fee_r": 0}
    v = ef.evaluate(cfg, side="LONG", asset_class="forex", fee_r_est=0.9, bias=None)
    assert v["ok"]


def test_applies_only_live_by_default():
    assert ef.applies({}, "live", False) is True
    assert ef.applies({}, "paper", False) is False
    assert ef.applies({}, "live", True) is False           # Sammel-Trades nie blocken
    assert ef.applies({"edge_filter_apply_paper": True}, "paper", False) is True
    assert ef.applies({"edge_filter_enabled": False}, "live", False) is False


def _t(side, sym, fee, risk, bias, pnl):
    return {"side": side, "symbol": sym, "fees_paid": fee, "risk_usdt": risk, "realized_pnl": pnl,
            "entry_market_snapshot": {"features": {"daily_bias": bias}}}


def test_bucket_of_trade_uses_real_fees():
    assert ef.bucket_of_trade(_t("LONG", "BTCUSDT", 1, 20, "up", 5)) == "besteht"
    assert ef.bucket_of_trade(_t("LONG", "BTCUSDT", 5, 20, "up", 5)) == "besteht nicht"
    assert ef.bucket_of_trade(_t("LONG", "BTCUSDT", 1, 0, "up", 5)) is None


def test_edge_report_new_groups():
    rows = [_t("LONG", "BTCUSDT", 1, 20, "up", 10)] * 25 + [_t("SHORT", "BTCUSDT", 5, 20, "up", -20)] * 25
    by_edge = er.group_r(rows, lambda t: ef.bucket_of_trade(t))
    assert by_edge["besteht"]["exp_r"] > 0 > by_edge["besteht nicht"]["exp_r"]
    assert set(er.group_r(rows, er.bias_key)) == {"mit Tagestrend", "gegen/flat"}
    assert set(er.group_r(rows, er.cost_key)) == {"≤ 10 %", "20–30 %"}
    assert set(er.group_r(rows, er.asset_key)) == {"crypto"}
    overall = er.agg_r(rows)
    f = er.edge_findings(overall, {}, {}, {}, {}, {}, None, by_edge)
    assert any("Edge-Filter" in x["text"] for x in f)


def test_edge_findings_backward_compatible_signature():
    overall = er.agg_r([_t("LONG", "BTCUSDT", 1, 20, "up", 10)] * 3)
    assert isinstance(er.edge_findings(overall, {}, {}, {}, {}, {}, None), list)


def test_defaults_registered_in_ai_config():
    from services.ai_engine import DEFAULT_AI_CONFIG
    for k, v in ef.DEFAULTS.items():
        assert DEFAULT_AI_CONFIG[k] == v


def test_guard_shadow_knows_edge_filter():
    from services import guard_shadow
    assert "edge_filter" in guard_shadow.SHADOW_GUARDS
    assert "edge_filter" in guard_shadow.GUARD_LABELS
