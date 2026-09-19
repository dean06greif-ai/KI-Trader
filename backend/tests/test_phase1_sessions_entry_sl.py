"""Phase 1 (Audit F09/F03/F11): Scanner-Sessions über Mitternacht, SL-Verifikation
ohne positionId, Registry-Auflösung nach Notfall-Close. Ohne Netzwerk."""
import inspect

from services.strategy_scanner import StrategyScanner
from services.bitunix_trade import AutoTradeManager


def test_session_contains_handles_midnight_wrap():
    c = StrategyScanner.session_contains
    assert c((22, 0), (6, 0), (23, 30)) is True
    assert c((22, 0), (6, 0), (2, 15)) is True
    assert c((22, 0), (6, 0), (12, 0)) is False
    assert c((22, 0), (6, 0), (6, 0)) is False
    assert c((9, 0), (17, 30), (12, 0)) is True
    assert c((9, 0), (17, 30), (17, 30)) is False
    assert c((0, 0), (0, 0), (13, 7)) is True


def test_is_trading_session_uses_wrap(monkeypatch):
    sc = StrategyScanner()
    sc.settings = dict(sc.settings or {})
    sc.settings["custom_sessions"] = [{"name": "Nacht", "start": "22:00", "end": "06:00",
                                       "enabled": True}]
    monkeypatch.setattr(sc, "sessions_for", lambda sid=None: sc.settings["custom_sessions"])

    class _Now:
        def __init__(self, h, m):
            self.hour, self.minute = h, m
    monkeypatch.setattr(sc, "berlin_now", lambda: _Now(23, 45))
    assert sc.is_trading_session() is True
    assert sc.get_current_session() == "Nacht"
    monkeypatch.setattr(sc, "berlin_now", lambda: _Now(14, 0))
    assert sc.is_trading_session() is False
    assert sc.get_current_session() == "Closed"


def test_entry_without_position_id_marks_sl_unverified():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    i = src.index("Audit F03")
    block = src[i:i + 1500]
    assert "if not position_id:" in block
    assert "sl_missing = True" in block
    assert "_resolve_new_position_id(symbol, side, qty)" in block


def test_emergency_close_resolves_registry_entry():
    src = inspect.getsource(AutoTradeManager._on_signal_impl)
    i = src.index("Notfall-Close wegen fehlendem SL")
    block = src[i:i + 700]
    assert "entry_order_registry.resolve(self.db, order_id)" in block
    assert block.index("entry_order_registry.resolve") < block.index("return None")
