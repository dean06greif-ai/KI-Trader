"""Signal-Meldungen (Telegram) erst NACH dem Trade-Versuch (rein & testbar).

Regel (Toggle `signals_only_traded`, Standard an): ein Signal wird nur gemeldet,
wenn daraus wirklich ein Trade eröffnet wurde. Die Meldung enthält dann den
Trade-Modus (LIVE / PAPER / DATENSAMMLUNG) und – beim KI-Trader – das Setup.
Datensammel-Trades werden nur gemeldet, wenn `signals_collection` an ist.
"""
from typing import Dict, Optional

MODE_LABELS = {
    "live": "🔴 LIVE (Echtgeld)",
    "paper": "📄 PAPER",
    "collection": "🧪 DATENSAMMLUNG (Paper, nur ML-Daten)",
}


def trade_mode(trade: Optional[Dict]) -> Optional[str]:
    """live | paper | collection – None ohne Trade."""
    if not trade:
        return None
    if trade.get("data_collection"):
        return "collection"
    return "live" if trade.get("mode") == "live" else "paper"


def trade_info(trade: Optional[Dict], signal: Optional[Dict] = None) -> Optional[Dict]:
    """Kompakte Trade-Infos für Signal-Doc und Meldung."""
    mode = trade_mode(trade)
    if mode is None:
        return None
    setup = trade.get("setup") or (signal or {}).get("ai_setup")
    return {"trade_id": trade.get("id"), "mode": mode, "mode_label": MODE_LABELS[mode],
            "setup": setup or None}


def should_send(signal: Dict, trade: Optional[Dict], only_traded: bool,
                collection_enabled: bool) -> bool:
    """Darf die Signal-Meldung raus? (Kanal-/Coin-Toggles prüft der Aufrufer)"""
    if signal.get("signal_class") == "PRE_SIGNAL":
        return not only_traded           # Vorwarnung kann nie ein Trade sein
    mode = trade_mode(trade)
    if mode == "collection" or (mode is None and signal.get("data_collection")):
        return mode == "collection" and collection_enabled
    if only_traded:
        return mode is not None
    return True
