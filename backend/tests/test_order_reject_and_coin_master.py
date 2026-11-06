"""Regressionstests Bug-Report 25.09.: "ORDER ABGEBROCHEN"-Spam, HYPE trotz
Coin-Schalter AUS, Master-Modus der Signal-Benachrichtigungen."""
import asyncio

import pytest

from core import pipeline, state
from services import notifications, signal_notify
from services.bitunix_trade import AutoTradeManager
from services.telegram_bot import TelegramNotifier

pytestmark = pytest.mark.unit

RB = ("Risikobudget: Rest-Budget 0.00 USDT Risiko erlaubt nur ~0.00 USDT Marge @ {lev}x "
      "(gewünscht 5.00) -> unter Untergrenze/Börsen-Minimum, kein Trade")


def test_reject_key_ignores_changing_numbers():
    k1 = notifications.reject_notify_key("HYPEUSDT", "SHORT", RB.format(lev="74.78"), True)
    k2 = notifications.reject_notify_key("HYPEUSDT", "SHORT", RB.format(lev="75"), True)
    k3 = notifications.reject_notify_key("POLUSDT", "SHORT", RB.format(lev="51.93"), True)
    assert k1 == k2 == k3, "interne Stopps gleicher Ursache = EINE Meldung coin-übergreifend"
    assert k1[1] >= 3600
    e1 = notifications.reject_notify_key("BTCUSDT", "LONG", "code 30001: qty 0.0012 too small", False)
    e2 = notifications.reject_notify_key("BTCUSDT", "LONG", "code 30001: qty 0.0019 too small", False)
    e3 = notifications.reject_notify_key("ETHUSDT", "LONG", "code 30001: qty 0.0019 too small", False)
    assert e1 == e2 and e1 != e3


class _Tg:
    def __init__(self):
        self.calls = []

    async def send_rejection(self, symbol, side, reason, internal=False):
        self.calls.append((symbol, side, reason, internal))
        return True


def _mgr(tg):
    m = AutoTradeManager.__new__(AutoTradeManager)
    m.telegram, m.db, m.config = tg, None, {"coins": {}}
    return m


def test_notify_reject_dedupes_and_respects_coin_alerts(monkeypatch):
    async def _enabled(*a, **k):
        return True
    monkeypatch.setattr(notifications, "enabled", _enabled)
    monkeypatch.setattr(state.scanner, "is_notify_enabled", lambda s: s != "HYPEUSDT")
    tg = _Tg()
    m = _mgr(tg)

    async def run():
        for lev in ("51.93", "51.98", "52.10"):
            await m._notify_reject("POLUSDT", "SHORT", RB.format(lev=lev))
        await m._notify_reject("HYPEUSDT", "SHORT", RB.format(lev="75"))
    asyncio.run(run())
    assert len(tg.calls) == 1 and tg.calls[0][0] == "POLUSDT"


def test_coin_master_off_only_for_explicit_off():
    m = _mgr(None)
    m.config = {"coins": {"HYPEUSDT": {"enabled": False}, "POLUSDT": {"enabled": True}}}
    assert m.coin_master_off("HYPEUSDT")
    assert not m.coin_master_off("POLUSDT")
    assert not m.coin_master_off("SOLUSDT")   # kein Eintrag -> Strategie-Coin-Configs gelten


def test_pipeline_skips_master_off_coin(monkeypatch):
    monkeypatch.setitem(state.autotrader.config, "coins", {"HYPEUSDT": {"enabled": False}})
    called = []

    async def _on_signal(*a, **k):
        called.append(1)
    monkeypatch.setattr(state.autotrader, "on_signal", _on_signal)
    sig = {"symbol": "HYPEUSDT", "strategy_id": "custom_23a30b65", "type": "SHORT", "data_collection": True}
    asyncio.run(pipeline.process_signal(sig, []))
    assert not called and "id" not in sig, "kein Signal-Doc, kein Trade, keine Datensammlung"
    assert state.ai_symbol_allowed("ai_trader", "HYPEUSDT") is False
    assert state.ai_symbol_allowed("ai_trader", "BTCUSDT") is True


def test_on_signal_impl_rejects_master_off_coin(monkeypatch):
    m = _mgr(None)
    m.config = {"coins": {"HYPEUSDT": {"enabled": False}},
                "strategy_coin_configs": {"ai_trader_HYPEUSDT": {"mode": "live", "enabled": True}}}
    monkeypatch.setitem(state.control_state, "trades_paused", False)
    sig = {"symbol": "HYPEUSDT", "strategy_id": "ai_trader", "type": "SHORT"}
    assert asyncio.run(m._on_signal_impl(sig, [])) is None
    assert "Coin-Einstellungen" in sig["_reject_reason"]


def test_master_mode_all_signals_shows_guard_reason():
    sig = {"symbol": "POLUSDT", "type": "SHORT", "strategy_id": "custom_x", "strategy_name": "X",
           "entry_price": 1, "stop_loss": 1.1, "take_profit_1": 0.9, "take_profit_full": 0.8}
    assert signal_notify.should_send(sig, None, False, True)       # Modus "Jedes Signal"
    assert not signal_notify.should_send(sig, None, True, True)    # Modus "Nur Trades"
    tn = TelegramNotifier.__new__(TelegramNotifier)
    tn.frontend_url = "https://x"
    msg = tn.format_signal_message({**sig, "_no_trade_reason": RB.format(lev="51.93")})
    assert "Kein Trade eröffnet" in msg and "Risikobudget" in msg
