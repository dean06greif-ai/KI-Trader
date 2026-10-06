"""Smart-Money-Zonen (Order-Blocks + Fair-Value-Gaps) auf 5m/15m/1h für den
Analyse-Prompt – rein lokal aus dem Scanner-Kerzenpuffer (kein Netzwerk, keine
LLM-Kosten). Datenbasis für die Playbook-Setups order_block / fvg_fill und die
Multi-Timeframe-Sicht (Zone auf 15m/1h, Trigger auf 5m).
"""
from typing import Dict, List, Optional

from services import liquidity_levels
from services.timeframes import aggregate_candles

TIMEFRAMES = ("5m", "15m", "1h")
MAX_ZONES_PER_TF = 2
MAX_DISTANCE_PCT = 3.0


def _zone_rows(candles: List[Dict], price: float) -> List[Dict]:
    rows: List[Dict] = []
    for ob in liquidity_levels.order_blocks(candles):
        rows.append({"kind": "OB", "side": "bull" if ob["type"] == "ob_bull" else "bear",
                     "low": float(ob["zone_low"]), "high": float(ob["zone_high"]),
                     "untested": bool(ob.get("untested"))})
    for g in liquidity_levels.fair_value_gaps(candles):
        rows.append({"kind": "FVG", "side": g["side"], "low": float(g["low"]),
                     "high": float(g["high"]), "untested": True})
    for r in rows:
        mid = (r["low"] + r["high"]) / 2
        r["dist_pct"] = (mid - price) / price * 100 if price else 0.0
    rows = [r for r in rows if abs(r["dist_pct"]) <= MAX_DISTANCE_PCT]
    rows.sort(key=lambda r: abs(r["dist_pct"]))
    return rows[:MAX_ZONES_PER_TF]


def zones(candles_1m: List[Dict], price: float) -> Dict[str, List[Dict]]:
    """Nächste OB/FVG-Zonen je Timeframe (rein & testbar)."""
    out: Dict[str, List[Dict]] = {}
    for tf in TIMEFRAMES:
        agg = aggregate_candles(candles_1m, tf, drop_partial=True)
        if len(agg) < 30:
            continue
        rows = _zone_rows(agg, price)
        if rows:
            out[tf] = rows
    return out


def zones_text(candles_1m: List[Dict], price: float) -> Optional[str]:
    """Kompakte Prompt-Zeile, z.B.
    'SMC-Zonen 15m: bull OB 0.851-0.853 (-0.4%, frisch) · bear FVG 0.866-0.868 (+1.1%)'."""
    z = zones(candles_1m, price)
    if not z:
        return None
    parts = []
    for tf, rows in z.items():
        items = []
        for r in rows:
            tag = ", frisch" if r["untested"] and r["kind"] == "OB" else ""
            items.append(f"{r['side']} {r['kind']} {r['low']:g}-{r['high']:g} "
                         f"({r['dist_pct']:+.2f}%{tag})")
        parts.append(f"{tf}: " + " · ".join(items))
    return "SMC-Zonen " + " | ".join(parts)
