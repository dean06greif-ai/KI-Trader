"""Aktiv-Filter für Performance-Kennzahlen (Paper-Badge, Strategie-Vergleich).

Vorgabe des Traders (07.09.2026): In die Kennzahlen fließen nur Trades von
Strategie×Asset-Kombinationen ein, die DERZEIT freigeschaltet sind (Trade-Modus
'paper' oder 'live' in `strategy_coin_configs`, _id = "<strategy_id>_<SYMBOL>").
Trades deaktivierter Kombinationen ('off' oder keine Config) bleiben in der DB,
werden aber nicht mehr eingerechnet.

Manuelle/externe Trades (Bitunix-App, Website-Manuell-Trade) gehören zu keiner
Strategie und sind von diesem Filter NICHT betroffen.

Reine Funktionen (`active_keys_from_docs`, `is_active`, `filter_active`) sind
ohne DB testbar; `load_active_keys` liest DB + In-Memory-Cache des Autotraders.
"""
from typing import Dict, Iterable, List, Optional, Set

ACTIVE_MODES = ("live", "paper")

MANUAL_IDS = ("external", "manual")


def pair_key(strategy_id: Optional[str], symbol: Optional[str]) -> str:
    return f"{strategy_id or ''}_{symbol or ''}"


def is_manual_trade(trade: Dict) -> bool:
    return (trade.get("strategy_id") or "") in MANUAL_IDS \
        or bool(trade.get("manual_trade")) or bool(trade.get("external_adopted"))


def active_keys_from_docs(docs: Iterable[Dict],
                          cached: Optional[Dict[str, Dict]] = None) -> Set[str]:
    """`strategy_coin_configs`-Dokumente (+ optional In-Memory-Cache
    {key: config}) -> Menge aktiver "<strategy>_<SYMBOL>"-Schlüssel.
    Der Cache gewinnt bei Konflikt (er spiegelt die letzte UI-Änderung)."""
    modes: Dict[str, str] = {}
    for d in docs or []:
        key = d.get("_id")
        if key:
            modes[str(key)] = str((d.get("config") or {}).get("mode") or "off")
    for key, cfg in (cached or {}).items():
        modes[str(key)] = str((cfg or {}).get("mode") or "off")
    return {k for k, m in modes.items() if m in ACTIVE_MODES}


def is_active(active_keys: Set[str], strategy_id: Optional[str],
              symbol: Optional[str]) -> bool:
    return pair_key(strategy_id, symbol) in active_keys


def filter_active(trades: Iterable[Dict], active_keys: Set[str],
                  keep_manual: bool = True) -> List[Dict]:
    """Nur Trades aktiver Strategie×Asset-Kombinationen (manuelle optional
    unverändert durchreichen)."""
    out: List[Dict] = []
    for t in trades:
        if keep_manual and is_manual_trade(t):
            out.append(t)
        elif is_active(active_keys, t.get("strategy_id"), t.get("symbol")):
            out.append(t)
    return out


async def load_active_keys(db, autotrader=None) -> Set[str]:
    """Aktive Kombinationen aus DB + In-Memory-Cache des Autotraders."""
    docs = await db.strategy_coin_configs.find(
        {}, {"_id": 1, "config.mode": 1}).to_list(5000)
    cached = None
    if autotrader is not None:
        cached = (getattr(autotrader, "config", None) or {}).get("strategy_coin_configs")
    return active_keys_from_docs(docs, cached)
