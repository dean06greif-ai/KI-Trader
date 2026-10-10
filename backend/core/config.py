"""Zentrale Konstanten & Umgebungs-Setup (aus server.py verschoben).

Das Asset-Universum selbst liegt in ``core.instruments`` – hier werden nur die
etablierten Namen re-exportiert, damit bestehender Code unverändert läuft.
"""
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

from core.instruments import (  # noqa: F401  (öffentliches Re-Export-API)
    ALL_SYMBOLS,
    BACKTEST_SYMBOLS,
    OTHER_INSTRUMENTS,
    OTHER_YAHOO,
    TOP_10_COINS,
    TRADABLE_SYMBOLS,
)

load_dotenv()

BERLIN = ZoneInfo("Europe/Berlin")


def local_engine_disabled() -> bool:
    """Preview-/Dev-Guard (Env AI_TRADER_LOCAL_DISABLE=1): verhindert, dass eine
    zweite Instanz (z.B. lokale Entwicklung/Preview) parallel zur Render-Prod
    LLM-Analysen fährt oder Trades auslöst – Config/DB werden geteilt. Auf
    Render ist die Variable NICHT gesetzt -> keinerlei Verhaltensänderung."""
    import os
    return str(os.environ.get("AI_TRADER_LOCAL_DISABLE", "")).strip().lower() \
        in ("1", "true", "yes")


POLL_INTERVAL = 12
