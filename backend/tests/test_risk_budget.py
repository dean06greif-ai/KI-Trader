"""Phase 1.6 (Audit E1): Gesamt-Risikobudget offener Positionen. Ohne Netzwerk."""
import inspect

from services import risk_budget as rb
from services.bitunix_trade import AutoTradeManager
from core.instruments import GROUP_CRYPTO

CFG = {"enabled": True, "max_portfolio_risk_pct": 6.0, "max_cluster_risk_pct": 4.0}


def _t(sym, side, entry, sl, qty, mode="live", **kw):
    return {"status": "open", "mode": mode, "symbol": sym, "side": side, "entry": entry,
            "sl": sl, "qty": qty, "qty_remaining": qty, **kw}


def test_trade_risk_usdt():
    assert rb.trade_risk_usdt(_t("BTCUSDT", "LONG", 100.0, 99.0, 2.0)) == 2.0
    assert rb.trade_risk_usdt(_t("BTCUSDT", "SHORT", 100.0, 101.0, 2.0)) == 2.0
    assert rb.trade_risk_usdt(_t("BTCUSDT", "LONG", 100.0, 101.0, 2.0)) == 0.0  # SL im Gewinn
    assert rb.trade_risk_usdt({"entry": None}) == 0.0


def test_open_risk_filters_mode_and_collection():
    rows = [_t("BTCUSDT", "LONG", 100, 99, 10),           # 10 USDT live
            _t("ETHUSDT", "LONG", 50, 49, 5, mode="paper"),  # paper
            _t("XRPUSDT", "LONG", 1, 0.9, 100, data_collection=True),  # Sammel
            {**_t("GOLD", "SHORT", 2000, 2010, 1), "status": "closed"}]
    total, by = rb.open_risk(rows, "live")
    assert total == 10.0 and by == {GROUP_CRYPTO: 10.0}


def test_check_portfolio_limit():
    ok, why = rb.check(20.0, "BTCUSDT", 45.0, {GROUP_CRYPTO: 45.0}, 1000.0, CFG)
    assert not ok and "Risikobudget" in why and "6%" in why
    ok, _ = rb.check(10.0, "BTCUSDT", 45.0, {GROUP_CRYPTO: 45.0}, 1000.0,
                     {**CFG, "max_cluster_risk_pct": 0})
    assert ok


def test_check_cluster_limit_uses_instrument_group():
    ok, why = rb.check(15.0, "ETHUSDT", 30.0, {GROUP_CRYPTO: 30.0}, 1000.0, CFG)
    assert not ok and GROUP_CRYPTO in why
    ok, _ = rb.check(15.0, "GOLD", 30.0, {GROUP_CRYPTO: 30.0}, 1000.0, CFG)
    assert ok  # anderes Cluster, Gesamt 45 <= 60


def test_check_fail_closed_without_equity_and_open_when_disabled():
    # AP02c (T06): Equity unbekannt -> fail-closed (Solltest, ersetzt das
    # frühere fail-open-Verhalten); Abschalten/expliziter Opt-out bleibt möglich.
    ok, why = rb.check(999.0, "BTCUSDT", 0.0, {}, None, CFG)
    assert not ok and "Equity" in why
    assert rb.check(999.0, "BTCUSDT", 0.0, {},
                    None, {**CFG, "fail_open_no_equity": True}) == (True, "")
    assert rb.check(999.0, "BTCUSDT", 0.0, {}, 100.0, {**CFG, "enabled": False}) == (True, "")


def test_hook_in_on_signal_before_order_and_not_for_collection():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    # Seit Audit 2.1 läuft das Risikobudget über den zentralen Entry-Guard
    i = src.index("entry_guard.check_risk_budget")
    assert 0 <= i - src.rfind("if not collection:", 0, i) < 2000  # innerhalb des Blocks
    assert i < src.index("client.place_order(")
    assert "abs(float(entry) - float(sl)) * qty" in src
    from services import entry_guard
    assert "risk_budget.check_new_trade" in inspect.getsource(entry_guard.check_risk_budget)
