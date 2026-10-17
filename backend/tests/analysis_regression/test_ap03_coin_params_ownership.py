"""R02-Solltest: _apply_strategy_params entfernt Rückstände des vorherigen
Regimes über settings['coin_params_dynamic_keys'], manuelle Keys bleiben."""
import asyncio

import pytest

from services import dynamic_live

pytestmark = pytest.mark.unit


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_stale_dynamic_coin_params_removed(monkeypatch):
    from core import state
    from core.state import scanner
    monkeypatch.setattr(state, "db", None)  # nur In-Memory-Settings
    monkeypatch.setitem(scanner.settings, "coin_params",
                        {"nnfx": {"BTC": {"manual_thr": 7}}})
    monkeypatch.setitem(scanner.settings, "coin_params_dynamic_keys", {})

    _run(dynamic_live._apply_strategy_params("nnfx", "BTC", {"ema_fast": 9, "rsi": 14}))
    cur = scanner.settings["coin_params"]["nnfx"]["BTC"]
    assert cur == {"manual_thr": 7, "ema_fast": 9, "rsi": 14}

    _run(dynamic_live._apply_strategy_params("nnfx", "BTC", {"ema_fast": 21}))
    cur = scanner.settings["coin_params"]["nnfx"]["BTC"]
    assert cur == {"manual_thr": 7, "ema_fast": 21}  # rsi entfernt, manuell bleibt

    _run(dynamic_live._apply_strategy_params("nnfx", "BTC", {}))
    cur = scanner.settings["coin_params"]["nnfx"]["BTC"]
    assert cur == {"manual_thr": 7}
