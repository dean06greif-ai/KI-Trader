"""Smart-Skip für den KI-Trade-Manager (rein & testbar) – Token-Sparen ohne
Leistungsverlust.

Befund 06/2026: Der Trade-Manager rief alle `interval_min` (5) Minuten das LLM
mit dem vollen Review-Kontext (MasterPrompt, Makro, Liquidität, Lektionen,
Markt-Beobachter, News, alle offenen Trades) auf – ~290 Aufrufe/Tag, auch wenn
sich seit dem letzten Review NICHTS Relevantes bewegt hatte. Das war der
Token-Fresser, nicht der Bewegungs-Scanner (dessen Erkennung ist LLM-frei,
max. 12 Analysen/Tag).

Regel: Ein LLM-Review läuft nur, wenn
  * seit dem letzten Review mind. `min_llm_gap_min` vergangen sind UND
  * etwas MATERIELLES passiert ist:
      - Trade-Set geändert (neuer/geschlossener Trade, Teil-Close),
      - ein Trade hat sich seit dem letzten Review um >= `move_r` R bewegt,
      - ein Trade steht nahe an SL oder TP (< `near_r` R Abstand),
      - ein Trade hat TP1 erreicht / BE gesetzt (Flags geändert),
  * oder spätestens nach `max_llm_gap_min` (Sicherheitsnetz).
Nichts Relevantes -> Review übersprungen, kein LLM-Call.
"""
from typing import Dict, List, Optional, Tuple

DEFAULTS = {
    "smart_skip": True,
    "min_llm_gap_min": 10,     # nie öfter als alle 10 min ans LLM
    "max_llm_gap_min": 30,     # spätestens alle 30 min ein Review
    "move_r": 0.35,            # Bewegung seit letztem Review in R (SL-Distanz)
    "near_r": 0.3,             # Nähe zu SL/TP in R -> sofort reviewen
}


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def trade_r(trade: Dict, price: float) -> Optional[float]:
    """Aktueller Stand des Trades in R (Gewinn/Verlust relativ zur SL-Distanz)."""
    entry, sl = _f(trade.get("entry")), _f(trade.get("sl"))
    risk = abs(entry - sl)
    if entry <= 0 or risk <= 0 or price <= 0:
        return None
    sign = 1.0 if str(trade.get("side", "")).upper() == "LONG" else -1.0
    return (price - entry) * sign / risk


def fingerprint(trades: List[Dict], prices: Dict[str, float]) -> Dict[str, Dict]:
    """Kompakter Zustand je Trade: Menge, SL/TP, Flags und Stand in R."""
    out: Dict[str, Dict] = {}
    for t in trades:
        tid = str(t.get("id") or "")
        if not tid:
            continue
        price = _f(prices.get(t.get("symbol")), _f(t.get("entry")))
        out[tid] = {
            "qty": round(_f(t.get("qty_remaining", t.get("qty"))), 8),
            "sl": _f(t.get("sl")), "tp1": _f(t.get("tp1")), "tpf": _f(t.get("tpf")),
            "flags": (bool(t.get("tp1_hit")), bool(t.get("breakeven_moved")),
                      bool(t.get("profit_secured"))),
            "r": trade_r(t, price),
            "near": near_level(t, price),
        }
    return out


def near_level(trade: Dict, price: float, near_r: float = DEFAULTS["near_r"]) -> bool:
    """Steht der Kurs nahe (in R) an SL, TP1 oder TP-Full?"""
    entry, sl = _f(trade.get("entry")), _f(trade.get("sl"))
    risk = abs(entry - sl)
    if risk <= 0 or price <= 0:
        return False
    levels = [sl] + [_f(trade.get(k)) for k in ("tp1", "tpf") if _f(trade.get(k)) > 0]
    return any(abs(price - lv) / risk < near_r for lv in levels if lv > 0)


def should_review(prev: Optional[Dict[str, Dict]], cur: Dict[str, Dict],
                  minutes_since_llm: Optional[float], settings: Dict) -> Tuple[bool, str]:
    """Entscheidung (rein): (review_ja, Grund)."""
    if not settings.get("smart_skip", DEFAULTS["smart_skip"]):
        return True, "Smart-Skip aus"
    if prev is None or minutes_since_llm is None:
        return True, "erstes Review"
    min_gap = _f(settings.get("min_llm_gap_min"), DEFAULTS["min_llm_gap_min"])
    max_gap = _f(settings.get("max_llm_gap_min"), DEFAULTS["max_llm_gap_min"])
    move_r = _f(settings.get("move_r"), DEFAULTS["move_r"])
    if minutes_since_llm >= max_gap:
        return True, f"Sicherheitsnetz: {int(minutes_since_llm)} min seit letztem Review"
    if set(prev) != set(cur):
        reason = "Trade-Set geändert (neu/geschlossen)"
    else:
        reason = ""
        for tid, c in cur.items():
            p = prev[tid]
            if c["qty"] != p["qty"] or c["sl"] != p["sl"] or c["tp1"] != p["tp1"] \
                    or c["tpf"] != p["tpf"] or c["flags"] != p["flags"]:
                reason = f"Trade {tid[:8]}: Menge/SL/TP/Flags geändert"
                break
            if c.get("near"):
                reason = f"Trade {tid[:8]}: nahe an SL/TP"
                break
            if c["r"] is not None and p["r"] is not None and abs(c["r"] - p["r"]) >= move_r:
                reason = f"Trade {tid[:8]}: Bewegung {c['r'] - p['r']:+.2f} R"
                break
    if not reason:
        return False, "nichts Relevantes seit letztem Review"
    if minutes_since_llm < min_gap:
        return False, f"{reason} – aber Mindestabstand {min_gap:g} min noch nicht erreicht"
    return True, reason
