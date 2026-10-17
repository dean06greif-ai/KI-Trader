"""Konfidenz-Kalibrierung an echten Ergebnissen (10/2026).

Befund: Die vom Sprachmodell geschätzte Konfidenz hatte keinen Bezug zur
Trefferquote (<55: −0,29 R · 55–64: −0,22 R · 65–74: −0,23 R · ≥75: −0,40 R).
Hier wird je Konfidenz-Stufe gemessen, was KI-Trades (ki_geprueft/ki_frei)
tatsächlich gebracht haben – geschrumpft zum Gesamtmittel, damit dünne Stufen
nicht springen. Ergebnis:
  * `confidence_cal` an jeder Entscheidung (empirische Gewinnquote der Stufe),
  * `informative`: steigt das Ergebnis mit der Konfidenz? Nur dann darf die
    Konfidenz Größe/Freigaben beeinflussen (Live-Bypass bleibt im Broker-Modus aus),
  * eine Rückmelde-Zeile für den Prompt („deine 75+ lag bei −0,40 R“).
"""
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from services.source_stats import r_of, source_of

BINS = ((0, 55, "<55"), (55, 65, "55-64"), (65, 75, "65-74"), (75, 85, "75-84"), (85, 101, "85+"))
SHRINK_K = 15
CACHE_S = 600
_cache: Dict = {"ts": 0.0, "data": None}


def bin_of(conf) -> Optional[str]:
    try:
        c = float(conf)
    except (TypeError, ValueError):
        return None
    return next((lbl for lo, hi, lbl in BINS if lo <= c < hi), None)


def build(trades: List[Dict]) -> Dict:
    """Kalibrier-Tabelle (rein)."""
    rows = [(bin_of(t.get("ai_confidence")), r_of(t), float(t.get("realized_pnl") or 0) > 0)
            for t in trades if source_of(t) in ("ki_geprueft", "ki_frei")]
    rows = [r for r in rows if r[0] and r[1] is not None]
    n_all = len(rows)
    g_r = sum(r[1] for r in rows) / n_all if n_all else 0.0
    g_w = sum(1 for r in rows if r[2]) / n_all if n_all else 0.0
    bins = []
    for _, _, lbl in BINS:
        sel = [r for r in rows if r[0] == lbl]
        n = len(sel)
        avg_r = sum(r[1] for r in sel) / n if n else None
        wr = sum(1 for r in sel if r[2]) / n if n else None
        cal_r = ((avg_r * n + g_r * SHRINK_K) / (n + SHRINK_K)) if n else g_r
        cal_w = ((wr * n + g_w * SHRINK_K) / (n + SHRINK_K)) if n else g_w
        bins.append({"bin": lbl, "n": n, "avg_r": round(avg_r, 3) if avg_r is not None else None,
                     "winrate": round(wr * 100) if wr is not None else None,
                     "cal_r": round(cal_r, 3), "cal_winrate": round(cal_w * 100)})
    filled = [b for b in bins if b["n"] >= 10]
    informative = len(filled) >= 3 and all(
        filled[i + 1]["cal_r"] >= filled[i]["cal_r"] for i in range(len(filled) - 1))
    return {"n": n_all, "global_r": round(g_r, 3), "global_winrate": round(g_w * 100),
            "bins": bins, "informative": informative}


def lookup(table: Optional[Dict], conf) -> Optional[Dict]:
    lbl = bin_of(conf)
    if not table or not lbl:
        return None
    return next((b for b in table.get("bins") or [] if b["bin"] == lbl), None)


def prompt_line(table: Optional[Dict]) -> str:
    if not table or not table.get("n"):
        return ""
    parts = [f"{b['bin']}: {b['avg_r']:+.2f} R ({b['n']})" for b in table["bins"] if b["n"]]
    tail = ("steigt mit der Konfidenz – gut" if table.get("informative")
            else "KEIN Zusammenhang mit dem Ergebnis – staffle ehrlicher, hohe Werte nur bei A-Setups")
    return f"KONFIDENZ-KALIBRIERUNG (echte Ergebnisse je Stufe): {' · '.join(parts)} -> {tail}."


async def load(db, days: int = 60, force: bool = False) -> Dict:
    if not force and _cache["data"] is not None and time.time() - _cache["ts"] < CACHE_S:
        return _cache["data"]
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": cutoff},
         "backtest": {"$ne": True}},
        {"_id": 0, "ai_confidence": 1, "realized_pnl": 1, "risk_usdt": 1, "signal_source": 1,
         "ai_reasoning": 1}).to_list(50000)
    data = build(rows)
    _cache.update(ts=time.time(), data=data)
    return data


def cached() -> Optional[Dict]:
    return _cache["data"]
