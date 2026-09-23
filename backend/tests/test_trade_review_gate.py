"""Smart-Skip des Trade-Managers (services/trade_review_gate.py): LLM-Review
nur bei materieller Änderung, spätestens nach max_llm_gap_min."""
from services import trade_review_gate as g
from services.ai_trade_manager import DEFAULT_SETTINGS

S = dict(g.DEFAULTS)


def _trade(tid="t1", side="LONG", entry=100.0, sl=99.0, tp1=101.5, tpf=103.0, **kw):
    return {"id": tid, "symbol": "BTCUSDT", "side": side, "entry": entry, "sl": sl,
            "tp1": tp1, "tpf": tpf, "qty": 1.0, "qty_remaining": 1.0, **kw}


def test_defaults_merged_into_trade_manager_settings():
    for k in g.DEFAULTS:
        assert k in DEFAULT_SETTINGS


def test_first_review_always_runs():
    ok, why = g.should_review(None, {}, None, S)
    assert ok and "erstes" in why


def test_skip_when_nothing_changed():
    fp1 = g.fingerprint([_trade()], {"BTCUSDT": 100.2})
    fp2 = g.fingerprint([_trade()], {"BTCUSDT": 100.25})
    ok, why = g.should_review(fp1, fp2, 15, S)
    assert not ok and "nichts Relevantes" in why


def test_review_on_significant_move():
    fp1 = g.fingerprint([_trade()], {"BTCUSDT": 100.0})
    fp2 = g.fingerprint([_trade()], {"BTCUSDT": 100.6})   # +0.6 R
    ok, why = g.should_review(fp1, fp2, 15, S)
    assert ok and "Bewegung" in why


def test_review_when_near_sl():
    fp1 = g.fingerprint([_trade()], {"BTCUSDT": 100.5})
    fp2 = g.fingerprint([_trade()], {"BTCUSDT": 99.2})    # 0.2 R vor dem SL
    ok, why = g.should_review(fp1, fp2, 12, S)
    assert ok and "nahe" in why


def test_review_on_trade_set_change():
    fp1 = g.fingerprint([_trade()], {"BTCUSDT": 100.0})
    fp2 = g.fingerprint([_trade(), _trade("t2")], {"BTCUSDT": 100.0})
    ok, why = g.should_review(fp1, fp2, 12, S)
    assert ok and "Trade-Set" in why


def test_min_gap_blocks_even_with_change():
    fp1 = g.fingerprint([_trade()], {"BTCUSDT": 100.0})
    fp2 = g.fingerprint([_trade(sl=99.5)], {"BTCUSDT": 100.0})
    ok, why = g.should_review(fp1, fp2, 3, S)
    assert not ok and "Mindestabstand" in why


def test_max_gap_forces_review():
    fp = g.fingerprint([_trade()], {"BTCUSDT": 100.0})
    ok, why = g.should_review(fp, fp, 31, S)
    assert ok and "Sicherheitsnetz" in why


def test_smart_skip_disabled_always_reviews():
    fp = g.fingerprint([_trade()], {"BTCUSDT": 100.0})
    ok, _ = g.should_review(fp, fp, 1, {**S, "smart_skip": False})
    assert ok


def test_trade_r_short():
    assert g.trade_r(_trade(side="SHORT", entry=100, sl=101), 99.0) == 1.0
    assert g.trade_r(_trade(entry=0), 99.0) is None
