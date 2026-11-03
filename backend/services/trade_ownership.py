"""Eigentums-Regel für Börsen-Positionen (Trader-Vorgabe 06/2026).

Die Website darf über die Trade-API NUR Positionen verändern, die sie selbst
eröffnet hat. Manuell in Bitunix eröffnete Positionen werden vom Watchdog nur
zur Sichtbarkeit übernommen ('Manuell (Bitunix)', external_adopted=True) und
dürfen weder SL/TP, Marge, Hebel noch Close-Aufrufe erhalten.

Bug-Report: Manueller Trade mit SL – jeder gelöschte/verschobene SL wurde
binnen eines Watchdog-Zyklus wieder an dieselbe Stelle gesetzt. Ursache war
der Fill-/Liq-Guard (sync_position_state -> guard_fill_liq), der für die
übernommene Position den geschätzten Website-SL 'vor die Liquidation' zog und
per sync_live_levels an die Börse schrieb.

Ausnahme: position_watchdog.manage_external=True (explizites Opt-in).
"""
from typing import Dict, Optional

FOREIGN_DETAIL = ("Manuelle Bitunix-Position – wird von der Website nicht "
                  "angefasst (bitte direkt in Bitunix verwalten)")


def is_foreign(t: Optional[Dict]) -> bool:
    """Vom Watchdog übernommene Position, die nachweislich NICHT von der
    Website stammt (kein Bot-Rest, kein registrierter KI-Limit-Fill)."""
    if not t or not t.get("external_adopted"):
        return False
    return not (t.get("leftover") or t.get("adopted_from_limit"))


def exchange_write_allowed(t: Optional[Dict]) -> bool:
    """Darf die Website für diesen Trade schreibende Börsen-Calls ausführen?"""
    if not is_foreign(t):
        return True
    try:
        from services.position_watchdog import watchdog
        return bool(watchdog.settings.get("manage_external", False))
    except Exception:  # noqa: BLE001
        return False
