"""Entry-Modus des KI-Traders: Limit am Level (aggressiv) vs. Market nach
Bestätigung (konservativ) – oder wie bisher die KI je Situation (Default).

Befund (Code-Prüfung 09/2026): Die Engine kann BEIDES bereits –
  * Limit-Entry am Key-Level (entry_type 'limit', services/key_level_limits.py,
    Live-Spiegelung als echte Limit-Order in limit_live_sync.py) und
  * Market-Entry nach Bestätigung (Sweep-Trigger: 1m-Wick-Sweep + Reclaim-Close
    -> gezielte Analyse -> 'market').
Was fehlte, war eine SETUP-BEWUSSTE Leitlinie: Level-Setups (Order-Block,
Range-Kante, Pullback, Liquidity-Sweep) profitieren vom Limit am Level
(besserer Preis, keine Slippage, aber Fill-Risiko), Momentum-Setups (Breakout,
Squeeze, News, TF2, Session-Open) brauchen den Impuls und damit Market nach
Bestätigung. Dieses Modul liefert die Leitlinie als Prompt-Zusatz und erzwingt
den Modus als Nachkontrolle der Entscheidung. Default 'ai' = bisheriges
Verhalten (kein Prompt-Zusatz, keine Nachkontrolle).
"""
from typing import Dict, Optional, Tuple

DEFAULTS: Dict = {"entry_mode": "ai"}
MODES = ("ai", "aggressive", "conservative")
MODE_LABELS = {"ai": "KI entscheidet (bisher)", "aggressive": "A – Aggressiv (Limit am Level)",
               "conservative": "B – Konservativ (Market nach Bestätigung)"}

# Entry-Stil je Setup: level = Limit am Level sinnvoll, confirm = Market nach
# Bestätigung, either = situationsabhängig (KI entscheidet in jedem Modus).
SETUP_STYLE: Dict[str, str] = {
    "order_block": "level", "fvg_fill": "level", "range_fade": "level", "htf_range": "level",
    "pullback": "level", "liquidity_sweep": "level", "mean_reversion": "level",
    "breakout": "confirm", "squeeze_breakout": "confirm", "momentum_news": "confirm",
    "trend_follow2": "confirm", "session_open": "confirm", "divergence": "confirm",
    "funding_fade": "confirm", "fomc_event": "confirm", "cpi_event": "confirm",
    "nfp_event": "confirm", "ppi_event": "confirm", "pce_event": "confirm",
    "trend_follow": "either", "swing_trend": "either", "hedge": "either",
}

_LEVEL = ", ".join(s for s, k in SETUP_STYLE.items() if k == "level")
_CONFIRM = ", ".join(s for s, k in SETUP_STYLE.items() if k == "confirm")

PROMPT_AGGRESSIVE = (
    "\nENTRY-MODUS A (AGGRESSIV, vom Trader gewählt): Bei Level-Setups (" + _LEVEL + ") "
    "setze entry_type 'limit' mit limit_price DIREKT am erwarteten Reversal-Level (Liquidity-"
    "Level/Order-Block/Range-Kante, LONG darunter, SHORT darüber) – ohne auf die Bestätigung zu "
    "warten; besserer Preis, keine Slippage, dafür Fill-Risiko. Bei Momentum-Setups (" + _CONFIRM
    + ") bleibt 'market' nach Bestätigung Pflicht."
)
PROMPT_CONSERVATIVE = (
    "\nENTRY-MODUS B (KONSERVATIV, vom Trader gewählt): IMMER entry_type 'market' und nur nach "
    "Bestätigung – z.B. Sweep -> Kerze schließt wieder innerhalb der Range/über dem Level -> "
    "Entry. Keine Limit-Orders im Voraus (Limit-Felder weglassen); bei fehlender Bestätigung HOLD."
)


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    if "entry_mode" in updates:
        m = str(updates.get("entry_mode") or "ai").strip().lower()
        cfg["entry_mode"] = m if m in MODES else "ai"


def mode_of(cfg: Optional[Dict]) -> str:
    m = str((cfg or {}).get("entry_mode") or "ai").lower()
    return m if m in MODES else "ai"


def prompt_addendum(cfg: Optional[Dict]) -> str:
    """Prompt-Zusatz je Modus (rein). 'ai' = leer -> Prompt wie bisher."""
    m = mode_of(cfg)
    if m == "aggressive":
        return PROMPT_AGGRESSIVE
    if m == "conservative":
        return PROMPT_CONSERVATIVE
    return ""


def apply(dec: Dict, cfg: Optional[Dict]) -> Tuple[str, Optional[str]]:
    """Nachkontrolle der KI-Entscheidung (rein). Rückgabe (entry_type, notiz).
    conservative: Limit -> Market (Bestätigungs-Entry). aggressive/ai: unverändert –
    ein fehlendes Level kann nicht erfunden werden, Market bleibt dann gültig."""
    et = "limit" if str(dec.get("entry_type") or "market").lower() == "limit" else "market"
    m = mode_of(cfg)
    if m == "conservative" and et == "limit":
        return "market", "Entry-Modus B (konservativ): Limit -> Market nach Bestätigung"
    if m == "aggressive" and et == "market" and SETUP_STYLE.get(str(dec.get("setup") or "")) == "level":
        return "market", "Entry-Modus A: Level-Setup ohne limit_price – Market beibehalten"
    return et, None
