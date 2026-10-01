"""Regressionstests: Coin-Einstellungen haben beim KI-Trader Vorrang
(Auto-Hebel pro Coin + Max. Kapital pro Coin als harte Grenze)."""
import sys

sys.path.insert(0, "/app/backend")
from dotenv import load_dotenv
load_dotenv("/app/backend/.env")


def main():
    from services.bitunix_trade import ai_leverage_override, ai_capital_base

    # ---------- 1) Coin-Auto-Hebel hat IMMER Vorrang vor dem KI-Hebel ----------
    auto_on = {"auto_leverage_enabled": True}
    auto_off = {"auto_leverage_enabled": False}
    # Coin-Auto-Hebel aktiv -> berechneter Hebel (12.5) bleibt, KI-Wunsch (25) ignoriert
    assert ai_leverage_override(auto_on, 12.5, 25.0) == 12.5
    # Coin-Auto-Hebel aus -> KI-Wunsch greift (gecappt 1..200)
    assert ai_leverage_override(auto_off, 10.0, 25.0) == 25.0
    assert ai_leverage_override(auto_off, 10.0, 500.0) == 200.0
    assert ai_leverage_override(auto_off, 10.0, 0.4) == 1.0 or \
        ai_leverage_override(auto_off, 10.0, 0.4) == 10.0  # ai_lev<=0-Grenzfall unten
    # Kein KI-Wunsch (0) -> Coin-Hebel bleibt
    assert ai_leverage_override(auto_off, 10.0, 0.0) == 10.0
    assert ai_leverage_override({}, 7.0, 0.0) == 7.0
    print("PASS 1: ai_leverage_override – Coin-Auto-Hebel schlägt KI-Hebel")

    # ---------- 2) Coin-Max-Kapital = harte Obergrenze ----------
    # Coin 50, global 100 -> Coin gewinnt (50)
    assert ai_capital_base(50.0, 100.0) == 50.0
    # Coin 200, global 100 -> global senkt (100)
    assert ai_capital_base(200.0, 100.0) == 100.0
    # Global aus (0) -> Coin-Setting gilt
    assert ai_capital_base(80.0, 0.0) == 80.0
    # Coin 0 (unkonfiguriert) -> global gilt
    assert ai_capital_base(0.0, 100.0) == 100.0
    print("PASS 2: ai_capital_base – min(Coin, Global), Coin bleibt harte Grenze")

    # ---------- 3) Wiring: open-Flow nutzt die beiden Helper ----------
    import inspect
    from services import bitunix_trade as bt
    src = inspect.getsource(bt.AutoTradeManager)
    assert "ai_leverage_override(" in src and "ai_capital_base(" in src
    print("PASS 3: AutoTradeManager verwendet ai_leverage_override + ai_capital_base")

    print("\nALLE TESTS BESTANDEN")


if __name__ == "__main__":
    main()
