"""Markt-Kalender: Wochenend-Schließzeiten der realen Märkte (UTC).

Krypto handelt 24/7. Forex, Rohstoffe (CME: Gold/Silber/Öl) und die
Index-Perps (QQQ/SPY) folgen den realen Börsenzeiten – am Wochenende liefern
die Kursquellen nur eingefrorene/indikative Preise, auf denen weder ein
sinnvoller Paper-Trade noch ein brauchbares ML-Label entsteht.

Fenster bewusst konservativ (Sommer-/Winterzeit-Verschiebung ~1h wird in
Kauf genommen): Schluss Freitag 21:00 UTC, Wiedereröffnung Sonntag
21:15 UTC (Forex/Sydney) bzw. 22:05 UTC (CME Globex).
"""
from datetime import datetime, timezone
from typing import Optional, Tuple

from core import instruments

FRIDAY_CLOSE_HOUR_UTC = 21
SUNDAY_OPEN_UTC = {  # Gruppe -> (Stunde, Minute) der Wiedereröffnung am Sonntag
    instruments.GROUP_FOREX: (21, 15),
    instruments.GROUP_RESOURCES: (22, 5),
    instruments.GROUP_INDICES: (22, 5),
}


def is_weekend_closed(symbol: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """(closed, grund) – True, wenn der reale Markt des Symbols am Wochenende
    geschlossen ist. Krypto und unbekannte Symbole gelten immer als offen."""
    inst = instruments.get(symbol)
    if inst is None or inst.group == instruments.GROUP_CRYPTO:
        return False, ""
    now = now or datetime.now(timezone.utc)
    open_h, open_m = SUNDAY_OPEN_UTC.get(inst.group, (22, 5))
    wd = now.weekday()  # Mo=0 … So=6
    closed = (wd == 5
              or (wd == 4 and now.hour >= FRIDAY_CLOSE_HOUR_UTC)
              or (wd == 6 and (now.hour, now.minute) < (open_h, open_m)))
    if not closed:
        return False, ""
    return True, (f"Markt geschlossen (Wochenende): {inst.name} öffnet erst wieder "
                  f"Sonntag ~{open_h:02d}:{open_m:02d} UTC – kein KI-Einstieg auf "
                  f"eingefrorenen Kursen")


# Tägliche Handelspause der realen Märkte (UTC): FX-Rollover (17:00 New York)
# bzw. CME-Globex-Wartungsfenster. In diesem Fenster liefern die Kursquellen
# keine neuen Kerzen – eine "alte" Kerze ist dort normal, kein Feed-Fehler.
DAILY_BREAK_UTC = {  # Gruppe -> ((Start h, m), (Ende h, m))
    instruments.GROUP_FOREX: ((21, 0), (22, 5)),
    instruments.GROUP_RESOURCES: ((21, 0), (22, 5)),
    instruments.GROUP_INDICES: ((21, 0), (22, 5)),
}


def is_market_closed(symbol: str, now: Optional[datetime] = None) -> Tuple[bool, str]:
    """(closed, grund) – Wochenende ODER tägliche Handelspause des realen
    Markts. Krypto und unbekannte Symbole gelten immer als offen. Für die
    Frische-Bewertung von Kursdaten gedacht (Diagnose/Feed-Wächter): nur bei
    OFFENEM Markt ist eine alte Kerze ein echter Datenfeed-Fehler."""
    closed, why = is_weekend_closed(symbol, now)
    if closed:
        return True, why
    inst = instruments.get(symbol)
    if inst is None or inst.group == instruments.GROUP_CRYPTO:
        return False, ""
    now = now or datetime.now(timezone.utc)
    start, end = DAILY_BREAK_UTC.get(inst.group, ((21, 0), (22, 5)))
    if start <= (now.hour, now.minute) < end:
        return True, (f"Markt geschlossen (tägliche Handelspause): {inst.name} "
                      f"{start[0]:02d}:{start[1]:02d}–{end[0]:02d}:{end[1]:02d} UTC")
    return False, ""
