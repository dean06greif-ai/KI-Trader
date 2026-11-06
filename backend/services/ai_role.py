"""Rolle der KI im KI-Trader: frei (bisher) oder Prüfer (nur Detektor-Signale).

Befund Prod 11/2026 (Signal-Quelle): ki_geprueft +0,32 R (25 Trades),
ki_frei −0,13 R (73), regel −0,29 R (524). Im Prüfer-Modus
  * entfällt der periodische Voll-Analyse-Zyklus (spart LLM-Kosten),
  * prüft die KI nur noch Detektor-Treffer (Setup-/Sweep-/TF2-Trigger),
  * werden KI-Entscheidungen ohne offenes Detektor-Fenster (ki_frei) zu HOLD.
Wirkt nur mit aktivem Signal-Broker (sonst gibt es keine Fenster). Default
„frei“ = bisheriges Verhalten. Reine Funktionen.
"""
from typing import Dict, Optional

DEFAULTS = {"ai_role": "frei"}
ROLES = ("frei", "pruefer")


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    if updates.get("ai_role") in ROLES:
        cfg["ai_role"] = updates["ai_role"]


def is_pruefer(cfg: Optional[Dict]) -> bool:
    from services import signal_broker
    cfg = cfg or {}
    return cfg.get("ai_role") == "pruefer" and signal_broker.enabled(cfg)


def skip_cycle(cfg: Optional[Dict]) -> bool:
    return is_pruefer(cfg)


def veto_free(dec: Dict, cfg: Optional[Dict]) -> bool:
    return is_pruefer(cfg) and dec.get("signal_source") != "ki_geprueft"
