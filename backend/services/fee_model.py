"""Gebührenmodell pro Anlageklasse – EINE Quelle der Wahrheit für Trade-Kosten.

Krypto (Bitunix-Futures): prozentuale Maker/Taker-Gebühr je Seite
(Haupteinstellungen futures_taker_fee_pct / futures_maker_fee_pct).

Forex (Interactive Brokers): Kommission je Seite in % des Notionals
(IBKR IdealPro ~0.2 Basispunkte = 0.002%) PLUS Mindestkommission in USD.
Beides ist in den Haupteinstellungen getrennt von den Krypto-Gebühren
einstellbar (forex_commission_pct / forex_min_commission_usd) und gilt
einheitlich für Live-, Paper- und Backtest-Berechnungen.
"""
import logging

logger = logging.getLogger(__name__)

DEFAULT_FOREX_COMMISSION_PCT = 0.002      # % je Seite (0.2 Basispunkte, IBKR)
DEFAULT_FOREX_MIN_COMMISSION_USD = 2.0    # Mindestkommission je Seite (USD)


def is_forex(symbol: str) -> bool:
    from core import instruments
    inst = instruments.get(symbol)
    return bool(inst and inst.group == instruments.GROUP_FOREX)


def forex_fee_settings() -> tuple:
    """(commission_pct, min_commission_usd) aus den Haupteinstellungen.
    Fällt außerhalb des Server-Prozesses (lokaler Worker) auf die IBKR-
    Standardwerte zurück."""
    pct, min_usd = DEFAULT_FOREX_COMMISSION_PCT, DEFAULT_FOREX_MIN_COMMISSION_USD
    try:
        from core import state
        s = getattr(state, "scanner", None)
        if s is not None:
            pct = float(s.settings.get("forex_commission_pct", pct) or pct)
            min_usd = float(s.settings.get("forex_min_commission_usd", min_usd) or 0)
    except Exception:
        pass
    return max(0.0, pct), max(0.0, min_usd)


def forex_fee_percent(notional_usd: float = 0.0, orders_per_side: int = 1) -> float:
    """Effektive Forex-Gebühr in % je Seite. Bei bekanntem Notional wird die
    Mindestkommission eingerechnet (kleine Orders zahlen effektiv mehr %).
    orders_per_side: IBKR berechnet die Mindestkommission JE ORDER – bei zwei
    OCA-Legs (TP1 + Runner) fällt sie je Seite bis zu zweimal an."""
    pct, min_usd = forex_fee_settings()
    try:
        n = float(notional_usd or 0)
    except (TypeError, ValueError):
        n = 0.0
    legs = max(1, int(orders_per_side or 1))
    if n > 0 and min_usd > 0:
        per_leg = n / legs
        return round(max(per_leg * pct / 100.0, min_usd) * legs / n * 100.0, 6)
    return pct


def fee_percent_for(symbol: str, default_pct: float, notional_usd: float = 0.0,
                    orders_per_side: int = 1) -> float:
    """Gebühr (%/Seite) für ein Symbol: Forex nutzt das IBKR-Kommissionsmodell,
    alle anderen Anlageklassen behalten die übergebene (Krypto-)Gebühr."""
    if is_forex(symbol):
        return forex_fee_percent(notional_usd, orders_per_side)
    return default_pct


def broker_label(symbol: str) -> str:
    """Anzeigename des Brokers für Log-/Fee-Wächter-Texte."""
    return "IBKR" if is_forex(symbol) else "Bitunix"
