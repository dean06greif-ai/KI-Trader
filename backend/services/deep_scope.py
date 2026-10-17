"""Adaptives Volatilitäts-Radar der Tiefenanalyse (rein & testbar).

Nicht-Krypto-Assets (Indizes, Rohstoffe, Forex) bewegen sich in absoluten
Prozent deutlich weniger als Krypto – ein absoluter ATR-Vergleich mit BTC
lässt den Tiefen-Analysten diese Klassen systematisch ignorieren. Dieses
Modul misst die Volatilität jedes Assets RELATIV zur EIGENEN Baseline
(aktuelle 15m-ATR vs. Median der eigenen ATR-Historie) – rein lokal, ohne
LLM-Kosten – und liefert einen kompakten Prompt-Block (~1 Zeile pro Asset):
Nicht-Krypto-Assets mit gerade erhöhter Volatilität werden für die
Tiefenanalyse explizit markiert (adaptiver Vorfilter, User-Wunsch 06/2026).
"""
from statistics import median
from typing import Dict, List, Optional

from services import setup_asset_class as ac
from services.technical_indicators import TechnicalIndicators
from services.timeframes import aggregate_candles

ELEVATED_RATIO = 1.35   # ab dieser Ratio gilt die Volatilität als erhöht
RECENT_BARS = 3         # Mittel der letzten n ATR-Werte = "aktuell"
MIN_15M_BARS = 40       # Mindesthistorie für eine belastbare Baseline


def rel_vol(candles_1m: List[Dict]) -> Optional[Dict]:
    """15m-ATR aktuell vs. eigene Median-Baseline (rein). None bei zu wenig Daten."""
    agg = aggregate_candles(candles_1m or [], "15m", drop_partial=True)
    if len(agg) < MIN_15M_BARS:
        return None
    atr_series = [a for a in (TechnicalIndicators.calculate_atr(agg, 14) or []) if a]
    if len(atr_series) < RECENT_BARS + 10:
        return None
    recent = sum(atr_series[-RECENT_BARS:]) / RECENT_BARS
    base = median(atr_series)
    if not base:
        return None
    price = agg[-1].get("close") or 0
    return {"ratio": round(recent / base, 2),
            "atr_pct": round(recent / price * 100, 3) if price else 0.0}


def radar(buffers: Dict[str, List[Dict]], symbols: List[str]) -> List[Dict]:
    """Relative Volatilität aller Nicht-Krypto-Symbole im Scope (rein)."""
    rows = []
    for s in symbols:
        cls = ac.asset_class_of(s)
        if cls == ac.CRYPTO:
            continue
        rv = rel_vol(buffers.get(s) or [])
        if not rv:
            continue
        rows.append({"symbol": s, "cls": cls, **rv,
                     "elevated": rv["ratio"] >= ELEVATED_RATIO})
    rows.sort(key=lambda r: -r["ratio"])
    return rows


def block_text(rows: List[Dict]) -> str:
    """Kompakter Prompt-Block für die Tiefenanalyse (rein). '' ohne Daten."""
    if not rows:
        return ""
    lines = ["=== VOLATILITÄTS-RADAR NICHT-KRYPTO (15m-ATR relativ zur EIGENEN Baseline) ==="]
    for r in rows:
        label = ac.LABELS.get(r["cls"], r["cls"])
        flag = (" → ERHÖHT: dieses Asset in der Tiefe analysieren (konkrete Levels + Szenarien)"
                if r["elevated"] else " (normal)")
        lines.append(f"- {r['symbol']} [{label}]: ATR {r['atr_pct']:g}% "
                     f"= ×{r['ratio']:g} der eigenen Baseline{flag}")
    lines.append("Bewerte die Handelbarkeit jedes Assets an seiner EIGENEN Baseline und den "
                 "Klassen-Grenzen (z.B. Forex-SL 0.05-0.5%), NICHT am absoluten ATR-Vergleich mit BTC.")
    return "\n".join(lines)
