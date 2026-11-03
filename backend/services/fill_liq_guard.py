"""Fill-/Liquidations-Abgleich für Live-Positionen (rein & testbar).

RCA POL-Trade 23.09.2026 (POLUSDT-1790173213520, SHORT):
  Signal/Entry lokal 0.10318, SL 0.106694 (+3.4 %), Hebel 22.7x -> lokale Liq
  0.107209 (hinter dem SL, alles korrekt geplant). Der MARKET-Fill kam aber in
  einem Flash-Wick bei 0.10144 (-1.7 %). Bitunix liefert in get_order_detail
  kein avgPrice -> der Bot behielt 0.10318 als Entry und rechnete die Liq
  weiter vom falschen Entry. Die echte Börsen-Liq (23x vom echten Fill) lag bei
  0.1052 – VOR dem SL. Folge: Liquidation (-1.64 USDT = gesamte Marge) statt
  SL (-1.28 USDT). Die knappe Rest-Marge (Risikobudget-Verkleinerung 19 -> 1.66
  USDT, dadurch hoher Hebel) machte den Abstand SL<->Liq so klein, dass die
  Fill-Abweichung ihn komplett aufgefressen hat.

Regel dieses Bausteins: Wahrheit ist die Börsen-Position (avgOpenPrice,
liqPrice, margin). Liegt der SL nicht mit Puffer VOR der echten Liquidation:
  1. Marge nachschießen (Liq wandert hinter den SL, Strategie-SL bleibt), wenn
     das freie Kapital reicht – sonst
  2. SL vor die echte Liq ziehen (kleinerer Verlust als Liquidation) – liegt der
     Kurs schon jenseits davon:
  3. Position schließen.
"""
from typing import Dict, Optional

ENTRY_TOL_PCT = 0.05     # Abweichung Entry lokal <-> Börse, ab der übernommen wird
DEFAULT_BUFFER_PCT = 0.3  # Mindest-Abstand SL -> Liq (in % vom Entry)
TOPUP_SAFETY = 1.08       # Aufschlag auf die berechnete Nachschuss-Marge


def entry_deviation_pct(local_entry, ex_entry) -> Optional[float]:
    try:
        le, xe = float(local_entry), float(ex_entry)
    except (TypeError, ValueError):
        return None
    if le <= 0 or xe <= 0:
        return None
    return round((xe - le) / le * 100.0, 4)


def sl_safe(side: str, sl: float, liq: float, entry: float, buffer_pct: float) -> bool:
    """SL liegt mit `buffer_pct` % (vom Entry) VOR der Liquidation."""
    if not sl or not liq or liq <= 0:
        return True
    buf = entry * buffer_pct / 100.0
    return sl >= liq + buf if side == "LONG" else sl <= liq - buf


def implied_mmr(side: str, entry: float, liq: float, margin: float, qty: float,
                fallback: float = 0.005) -> float:
    """MMR aus den Börsen-Zahlen zurückrechnen (Isolated):
    SHORT: liq = (margin/qty + entry) / (1 + mmr)
    LONG:  liq = (entry - margin/qty) / (1 - mmr)"""
    try:
        if liq > 0 and qty > 0 and margin > 0 and entry > 0:
            m = margin / qty
            mmr = (m + entry) / liq - 1.0 if side == "SHORT" else 1.0 - (entry - m) / liq
            if 0.0 <= mmr <= 0.05:
                return mmr
    except ZeroDivisionError:
        pass
    return fallback


def plan(side: str, sl: float, ex_entry: float, ex_liq: float, qty: float,
         margin: float, mark: Optional[float] = None, usable_free: Optional[float] = None,
         buffer_pct: float = DEFAULT_BUFFER_PCT, mmr_fallback: float = 0.005) -> Dict:
    """Maßnahme bestimmen. Rückgabe {"action": ok|add_margin|move_sl|close, ...}."""
    side = str(side).upper()
    try:
        sl, entry, liq = float(sl or 0), float(ex_entry or 0), float(ex_liq or 0)
        qty, margin = float(qty or 0), float(margin or 0)
    except (TypeError, ValueError):
        return {"action": "ok", "reason": "ungültige Eingaben"}
    if sl <= 0 or entry <= 0 or liq <= 0 or qty <= 0:
        return {"action": "ok", "reason": "keine Börsen-Liq/SL bekannt"}
    if sl_safe(side, sl, liq, entry, buffer_pct):
        return {"action": "ok", "reason": "SL liegt vor der Börsen-Liq"}
    buf = entry * buffer_pct / 100.0
    target_liq = sl - buf if side == "LONG" else sl + buf
    mmr = implied_mmr(side, entry, liq, margin, qty, mmr_fallback)
    if side == "SHORT":
        target_margin = qty * (target_liq * (1 + mmr) - entry)
    else:
        target_margin = qty * (entry - target_liq * (1 - mmr))
    add = round(max(0.0, (target_margin - margin) * TOPUP_SAFETY), 6)
    if add > 0 and usable_free is not None and add <= float(usable_free):
        return {"action": "add_margin", "amount": add, "target_liq": round(target_liq, 8),
                "mmr": round(mmr, 5),
                "reason": (f"SL {sl} liegt hinter der Börsen-Liq {liq} – Marge +{add:.4f} "
                           f"USDT schiebt die Liq hinter den SL")}
    new_sl = round(liq + buf if side == "LONG" else liq - buf, 10)
    if mark:
        beyond = mark <= new_sl if side == "LONG" else mark >= new_sl
        if beyond:
            return {"action": "close", "new_sl": new_sl,
                    "reason": (f"SL hinter der Börsen-Liq {liq}, Kurs {mark} bereits jenseits "
                               f"des sicheren SL {new_sl} -> schließen statt Liquidation")}
    return {"action": "move_sl", "new_sl": new_sl,
            "reason": (f"SL {sl} liegt hinter der Börsen-Liq {liq}; zu wenig freies Kapital "
                       f"für +{add:.4f} USDT Marge -> SL vor die Liq gezogen ({new_sl})")}
