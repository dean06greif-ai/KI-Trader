"""Rest-Budget-Trading (09/2026): Risikobudget verkleinert die Marge statt still
abzulehnen; Telegram zeigt echte Regelanzahl; Ablehnungsgrund landet am Signal.
Ohne Netzwerk/DB."""
import inspect

from core import pipeline
from core.instruments import GROUP_CRYPTO
from services import entry_guard
from services import risk_budget as rb
from services.bitunix_trade import AutoTradeManager
from services.telegram_bot import TelegramNotifier

CFG = {"enabled": True, "max_portfolio_risk_pct": 6.0, "max_cluster_risk_pct": 4.0}


# ---- risk_budget.headroom / fit_scale (rein) ---------------------------------
def test_headroom_min_of_portfolio_and_cluster():
    # Equity 22.66 (Befund Live-Konto): 6% = 1.36, Cluster 4% = 0.906 -> Cluster greift
    room = rb.headroom("ADAUSDT", 0.0, {}, 22.66, CFG)
    assert abs(room - 0.9064) < 1e-3
    # offenes Cluster-Risiko wird abgezogen
    room = rb.headroom("ADAUSDT", 0.5, {GROUP_CRYPTO: 0.5}, 22.66, CFG)
    assert abs(room - 0.4064) < 1e-3
    # anderes Cluster (GOLD): nur Portfolio-Rest zählt
    room = rb.headroom("GOLD", 0.5, {GROUP_CRYPTO: 0.5}, 100.0, CFG)
    assert abs(room - 4.0) < 1e-9


def test_headroom_none_when_disabled_or_no_equity_and_never_negative():
    assert rb.headroom("BTCUSDT", 0, {}, 100.0, {**CFG, "enabled": False}) is None
    assert rb.headroom("BTCUSDT", 0, {}, None, CFG) is None
    assert rb.headroom("BTCUSDT", 0, {}, 0, CFG) is None
    assert rb.headroom("BTCUSDT", 99.0, {GROUP_CRYPTO: 99.0}, 100.0, CFG) == 0.0


def test_fit_scale_only_when_risk_exceeds_room():
    assert rb.fit_scale(0.5, 1.0) is None          # passt -> nichts ändern
    assert rb.fit_scale(1.0, 1.0) is None          # exakt am Limit -> ok
    assert rb.fit_scale(0.0, 1.0) is None
    assert rb.fit_scale(10.0, None) is None        # kein Limit
    assert abs(rb.fit_scale(42.0, 1.36) - 1.36 / 42.0) < 1e-6
    assert rb.fit_scale(5.0, 0.0) == 0.0


def test_default_config_has_scale_to_fit_and_update_accepts_bool():
    assert rb.DEFAULT_CONFIG["scale_to_fit"] is True
    src = inspect.getsource(rb.update_config)
    assert "scale_to_fit" in src


# ---- Integration im Entry-Pfad (Quelltext-Verträge) --------------------------
def test_on_signal_scales_before_final_budget_check_and_notifies_on_block():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    i_fit = src.index("self._fit_risk_budget(")
    i_chk = src.index("entry_guard.check_risk_budget")
    assert i_fit < i_chk < src.index("client.place_order(")
    # der finale Budget-Block meldet jetzt per Telegram (vorher still)
    tail = src[i_chk:i_chk + 600]
    assert "_notify_reject(symbol, side, rb_why)" in tail
    # Live-Prefill (Limit bereits an der Börse gefüllt) wird nicht nachträglich verkleinert
    assert '_live_prefill' in src[i_fit - 300:i_fit]


def test_fit_helper_rejects_below_min_margin_and_records_note():
    src = inspect.getsource(AutoTradeManager._fit_risk_budget)
    assert "capital_fit.MIN_MARGIN_USDT" in src
    assert "min_qty" in src and "_notify_reject" in src
    assert 'checks.record("risk_budget_fit"' in src
    assert "_risk_budget_note" in src


def test_entry_guard_fit_wrapper_is_fail_open():
    src = inspect.getsource(entry_guard.fit_risk_budget)
    assert "risk_budget.scale_for_new_trade" in src
    assert "return None, None" in src


# ---- Telegram: echte Regelanzahl statt festem /4 -----------------------------
def test_telegram_rules_counts_from_signal():
    assert TelegramNotifier.rules_counts({"rules_met_count": 3, "rules_total": 3}) == (3, 3)
    assert TelegramNotifier.rules_counts({"rules_met_count": 4}) == (4, 4)          # Legacy Scalping
    assert TelegramNotifier.rules_counts({"rules_met": {"a": True, "b": False}}) == (1, 2)
    assert TelegramNotifier.rules_counts({}) == (4, 4)


def test_telegram_message_shows_3_of_3_for_tf2():
    n = TelegramNotifier.__new__(TelegramNotifier)
    n.frontend_url = "https://x"
    msg = n.format_signal_message({
        "type": "LONG", "symbol": "ADAUSDT", "strategy_name": "TrendFolge2",
        "rules_met_count": 3, "rules_total": 3, "entry_price": 0.2229,
        "stop_loss": 0.2184, "take_profit_1": 0.2274, "take_profit_full": 0.2319})
    assert "Rules: 3/3" in msg and "/4" not in msg


# ---- Pipeline: Ablehnungsgrund am Signal persistieren ------------------------
def test_pipeline_persists_trade_result_on_signal():
    src = inspect.getsource(pipeline.process_signal)
    assert '"trade_reject_reason": signal.get("_reject_reason")' in src
    assert '"trade_opened"' in src


# ---- Preview-Guard deckt jetzt auch Scanner-Auto-Trades + Monitor ab ---------
def test_preview_guard_skips_autotrade_but_not_manual(monkeypatch):
    monkeypatch.setenv("AI_TRADER_LOCAL_DISABLE", "1")
    from core.config import local_engine_disabled
    assert local_engine_disabled()
    src = inspect.getsource(pipeline.process_signal)
    i_guard = src.index("local_engine_disabled()")
    assert i_guard < src.index("autotrader.on_signal(")
    assert 'signal.get("manual_trade")' in src[i_guard:i_guard + 200]


def test_preview_guard_blocks_autotrade_and_monitor():
    src = inspect.getsource(pipeline.process_signal)
    i_guard = src.index("local_engine_disabled()")
    assert i_guard < src.index("autotrader.on_signal(")
    assert 'signal.get("manual_trade")' in src[i_guard:i_guard + 200]
    from core import scheduler
    ssrc = inspect.getsource(scheduler)
    assert ssrc.index("local_engine_disabled()") < ssrc.index("autotrader.monitor(prices)")
