"""Rest-Kapital-Trading (rein & testbar): reicht das freie Kapital nicht für die
gewünschte Marge, wird der Trade mit dem verfügbaren REST eröffnet statt
abgelehnt – für alle Broker (Bitunix + IBKR) gleich.

Regeln (Nutzer-Vorgabe 06/2026):
  * Marge > frei           -> Marge = frei (Live: abzüglich Gebühren-/Drift-Puffer,
                              sonst lehnt die Börse die Order wegen Fee ab).
  * Rest < MIN_MARGIN_USDT -> Trade überspringen (Untergrenze ~1 EUR).
  * Börsen-Minimum (min_qty): liegt die Menge darunter, wird die Marge auf das
    Minimum angehoben, sofern das freie Kapital reicht – sonst überspringen.

Eine Quelle der Wahrheit für: Market-Entries (bitunix_trade.on_signal),
Live-Limit-Orders (limit_live_sync.plan_size) und den Retry nach einer
'insufficient balance'-Ablehnung der Börse.
"""
from typing import Dict, Optional

MIN_MARGIN_USDT = 1.0          # Untergrenze (~1 EUR) – darunter kein Trade
LIVE_FEE_BUFFER_PCT = 0.03     # 3 % Puffer für Taker-Fee + Kursdrift bis zum Fill
INSUFFICIENT_HINTS = ("insufficient", "not enough", "balance", "margin insufficient",
                      "exceeds available", "available amount", "30011", "30012", "20003")


def usable_free(free: Optional[float], live: bool) -> Optional[float]:
    """Einsetzbarer Anteil des freien Kapitals (Live mit Gebühren-Puffer)."""
    if free is None:
        return None
    free = max(0.0, float(free))
    return round(free * (1.0 - LIVE_FEE_BUFFER_PCT), 6) if live else round(free, 6)


def fit_margin(capital: float, free: Optional[float], lev: float, entry: float,
               min_qty: float = 0.0, live: bool = False) -> Dict:
    """Marge an das freie Kapital anpassen.

    Rückgabe: {"margin": float, "note": str|None, "reject": str|None}.
    reject gesetzt = Trade überspringen (Rest unter Untergrenze bzw. Börsen-
    Minimum nicht finanzierbar)."""
    try:
        capital = float(capital or 0)
        lev = max(1.0, float(lev or 1))
        entry = float(entry or 0)
        min_qty = float(min_qty or 0)
    except (TypeError, ValueError):
        return {"margin": capital, "note": None, "reject": None}
    margin, note = capital, None
    usable = usable_free(free, live)
    if usable is not None and capital > usable:
        margin = usable
        note = (f"Kapital auf Rest {usable:.2f} USDT begrenzt "
                f"(gewünscht {capital:.2f}, frei {float(free):.2f})")
    if margin < MIN_MARGIN_USDT:
        return {"margin": margin, "note": note,
                "reject": (f"Rest-Kapital {margin:.2f} USDT unter Untergrenze "
                           f"{MIN_MARGIN_USDT:.2f} USDT -> kein Trade")}
    if min_qty > 0 and entry > 0:
        qty = margin * lev / entry
        if qty < min_qty:
            needed = round(min_qty * entry / lev * 1.005, 6)   # kleiner Rundungspuffer
            if usable is not None and needed > usable:
                return {"margin": margin, "note": note,
                        "reject": (f"Börsen-Minimum {min_qty:g} braucht ~{needed:.2f} USDT "
                                   f"Marge @ {lev:g}x, frei nur {float(free):.2f} -> kein Trade")}
            margin = needed
            note = ((note + "; ") if note else "") + \
                f"Marge auf Börsen-Minimum angehoben ({needed:.2f} USDT für {min_qty:g})"
    return {"margin": round(margin, 6), "note": note, "reject": None}


def looks_like_insufficient_balance(msg) -> bool:
    """Börsen-Ablehnung wegen fehlender Marge? (Basis für den Rest-Retry)"""
    s = str(msg or "").lower()
    return any(h in s for h in INSUFFICIENT_HINTS)


def retry_qty(free_avail: Optional[float], lev: float, entry: float, planned_qty: float,
              min_qty: float = 0.0, qty_step: float = 0.0) -> Optional[float]:
    """Neue Menge für den EINMALIGEN Retry nach 'insufficient balance': aus dem
    frischen Börsen-Guthaben (mit Puffer) – None, wenn kein sinnvoller Rest
    bleibt oder die Menge nicht kleiner würde (dann war Marge nicht die Ursache)."""
    fit = fit_margin(planned_qty * float(entry) / max(1.0, float(lev)), free_avail,
                     lev, entry, min_qty=min_qty, live=True)
    if fit["reject"] or fit["margin"] <= 0 or entry <= 0:
        return None
    qty = fit["margin"] * max(1.0, float(lev)) / float(entry)
    if qty_step and qty_step > 0:
        qty = int(qty / qty_step) * qty_step
    qty = round(qty, 6)
    if qty <= 0 or qty >= float(planned_qty) * 0.999:
        return None
    if min_qty and qty < min_qty:
        return None
    return qty
