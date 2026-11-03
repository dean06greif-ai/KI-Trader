"""Anlageklassen der EVENT-Setup-Backtests (FOMC/CPI/NFP/PPI/PCE).

Erweitert die Event-Backtests von Krypto auf die übrigen Klassen der Plattform
(core/instruments.py / services/setup_asset_class.py). Je Klasse: eigene
Symbole, eigene Datenquelle, eigene KI-optimierbare Parameter und eigene
Validierung (fomc_event/econ_event.set_validation speichert je asset_class –
das Live-Gate ai_playbook.live_ready_for fragt bereits pro Klasse ab).

Datenquellen (5m-Kerzen rund um den Event-Zeitpunkt):
  crypto     Bitunix-Perps (BTC/ETH/SOL), ~2 Jahre Historie
  indices    Bitunix-Index-Perps (QQQ/SPY), nur ~110 Tage Historie
  resources  Bitunix-Rohstoff-Perps (Gold/Silber), ~150 Tage Historie
  forex      IBKR-Client-Portal-Historie (EURUSD/USDJPY, IDEALPRO), ~2 Jahre –
             benötigt ein eingeloggtes IBKR-Gateway (IBeam)
"""
import logging

from services import setup_asset_class as ac

logger = logging.getLogger(__name__)

CRYPTO, INDICES, RESOURCES, FOREX = ac.CRYPTO, ac.INDICES, ac.RESOURCES, ac.FOREX
CLASSES = list(ac.CLASSES)
LABELS = dict(ac.LABELS)

SYMBOLS: dict[str, list[str]] = {
    CRYPTO: ["BTCUSDT", "ETHUSDT", "SOLUSDT"],
    INDICES: ["QQQUSDT", "SPYUSDT"],
    RESOURCES: ["XAUUSDT", "XAGUSDT"],
    FOREX: ["EURUSD", "USDJPY"],
}

# Realistisch verfügbare Historie (core/instruments.max_hist_days) in Jahren.
YEARS_CAP: dict[str, float] = {CRYPTO: 2.5, INDICES: 110 / 365,
                               RESOURCES: 150 / 365, FOREX: 2.5}
HIST_NOTE: dict[str, str] = {
    CRYPTO: "~2 Jahre Bitunix-5m-Historie",
    INDICES: "nur ~110 Tage Bitunix-Historie – wenige Events, Validierung braucht Zeit",
    RESOURCES: "nur ~150 Tage Bitunix-Historie – wenige Events, Validierung braucht Zeit",
    FOREX: "~2 Jahre IBKR-5m-Historie (IBKR-Gateway muss eingeloggt sein)",
}

# Ex-ante-Anpassung je Klasse (dokumentiert, KEIN Tuning auf den Events):
# Forex-Ranges sind ~5-10x kleiner als Krypto – ohne abgesenktes min_range_pct
# überspringt der Pre-Range-Filter fast alle Events.
CLASS_PARAM_OVERRIDES: dict[str, dict] = {FOREX: {"min_range_pct": 0.05}}


def normalize(cls: str | None) -> str:
    return cls if cls in CLASSES else CRYPTO


def symbols_for(cls: str) -> list[str]:
    return list(SYMBOLS.get(normalize(cls)) or [])


def years_cap(cls: str, years: float) -> float:
    return round(min(float(years), YEARS_CAP.get(normalize(cls), 2.5)), 3)


def class_overrides(cls: str) -> dict:
    return dict(CLASS_PARAM_OVERRIDES.get(normalize(cls)) or {})


def result_suffix(cls: str) -> str:
    """Krypto behält die bisherigen settings-IDs (Abwärtskompatibilität)."""
    cls = normalize(cls)
    return "" if cls == CRYPTO else f"_{cls}"


async def fetch_event_candles(cls: str, symbol: str,
                              start_ms: int, end_ms: int) -> list[dict]:
    """5m-Kerzen im Bitunix-Format ({time s, open, high, low, close})."""
    cls = normalize(cls)
    if cls == FOREX:
        from services.ibkr_client import ibkr_client
        if not ibkr_client.configured():
            return []
        conid = await ibkr_client.forex_conid(symbol)
        if not conid:
            return []
        return await ibkr_client.history_bars(conid, start_ms, end_ms, bar="5min")
    from services.bitunix_client import fetch_klines_range
    return await fetch_klines_range(symbol, "5m", start_ms=start_ms,
                                    end_ms=end_ms, limit=200)
