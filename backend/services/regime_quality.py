"""Erkennungs-Qualität einer gespeicherten Regime-Analyse – EINE Kennzahl-
Übersicht je Anlageklasse ("Wie gut ist die Regime-Erkennung für Krypto?").

Reine Funktionen auf dem Analyse-Dokument, keine DB/IO. Nutzt ausschließlich
Kennzahlen, die die Analyse bereits speichert (live_agreement, validation,
corrections) – funktioniert damit auch für Bestandsanalysen.

Hauptkennzahl: Live=Final im Holdout (Richtung). Sie sagt, wie oft die
kausale Live-Erkennung (ohne Zukunftswissen) im unangetasteten Testzeitraum
dasselbe Regime sieht wie die rückblickend korrigierte Final-Sicht.
"""
from typing import Dict, List, Optional

from services.setup_asset_class import LABELS, asset_class_of

GRADE_GOOD = 65.0   # ab hier: fürs Umschalten im Paper-/Live-Trading brauchbar
GRADE_OK = 50.0     # dazwischen: nur mit Bestätigung/Filter nutzen
MIN_HOLDOUT_BARS = 200


def _mean(vals: List[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def grade_of(holdout_pct: Optional[float], overall_pct: Optional[float],
             holdout_bars: int) -> Dict:
    """Note + Klartext. Ohne Holdout zählt der Gesamtwert, aber mit Hinweis."""
    basis = "holdout" if holdout_pct is not None and holdout_bars >= MIN_HOLDOUT_BARS else "overall"
    pct = holdout_pct if basis == "holdout" else overall_pct
    if pct is None:
        return {"grade": "unbewertet", "basis": basis, "pct": None,
                "text": "Keine Live=Final-Kennzahl vorhanden (nur Detektoren reactive/ema/kombi liefern sie)."}
    if pct >= GRADE_GOOD:
        g, txt = "gut", "Live-Erkennung trifft die finalen Phasen zuverlässig – fürs Umschalten geeignet."
    elif pct >= GRADE_OK:
        g, txt = "mittel", "Live-Erkennung trifft die Richtung mehrheitlich, aber mit spürbarer Verzögerung/Flackern – nur mit Bestätigung nutzen."
    else:
        g, txt = "schwach", "Live-Erkennung liegt zu oft daneben – Detektor/Einstellungen ändern (Kalibrierung, EMA-Vergleich) und neu analysieren."
    if basis == "overall":
        txt += " Achtung: kein belastbarer Holdout – Wert stammt aus dem Gesamtzeitraum (inkl. Training)."
    return {"grade": g, "basis": basis, "pct": pct, "text": txt}


def _symbol_row(sym: str, entry: Dict) -> Optional[Dict]:
    ag = (entry or {}).get("live_agreement") or {}
    val = (entry or {}).get("validation") or {}
    corr = (entry or {}).get("corrections") or {}
    if not ag and not val:
        return None
    return {"symbol": sym, "asset_class": asset_class_of(sym),
            "holdout_direction_pct": ag.get("holdout_direction_pct"),
            "direction_pct": ag.get("direction_pct"),
            "trend_hit_pct": ag.get("trend_hit_pct"),
            "holdout_bars": int(ag.get("holdout_bars") or 0),
            "violation_bars_pct": val.get("violation_bars_pct"),
            "avg_segment_days": val.get("avg_segment_days"),
            "validation_passed": val.get("passed"),
            "avg_delay_days": corr.get("avg_delay_days")}


def _aggregate(rows: List[Dict]) -> Dict:
    hb = sum(r["holdout_bars"] for r in rows)
    hold = _mean([r["holdout_direction_pct"] for r in rows])
    overall = _mean([r["direction_pct"] for r in rows])
    return {"n_symbols": len(rows),
            "holdout_direction_pct": hold, "direction_pct": overall,
            "trend_hit_pct": _mean([r["trend_hit_pct"] for r in rows]),
            "violation_bars_pct": _mean([r["violation_bars_pct"] for r in rows]),
            "avg_segment_days": _mean([r["avg_segment_days"] for r in rows]),
            "avg_delay_days": _mean([r["avg_delay_days"] for r in rows]),
            "validation_passed": all(r["validation_passed"] is not False for r in rows),
            "holdout_bars": hb, **grade_of(hold, overall, hb)}


def summarize_scope(per_symbol: Dict[str, Dict]) -> Optional[Dict]:
    """Qualität für einen Bereich (kombiniert oder je Coin): je Symbol, je
    Anlageklasse gepoolt und gesamt."""
    rows = [r for r in (_symbol_row(s, e) for s, e in (per_symbol or {}).items()) if r]
    if not rows:
        return None
    by_class: Dict[str, List[Dict]] = {}
    for r in rows:
        by_class.setdefault(r["asset_class"], []).append(r)
    classes = {cls: {"label": LABELS.get(cls, cls), **_aggregate(rs)}
               for cls, rs in by_class.items()}
    return {"overall": _aggregate(rows), "classes": classes, "symbols": rows,
            "thresholds": {"good": GRADE_GOOD, "ok": GRADE_OK,
                           "min_holdout_bars": MIN_HOLDOUT_BARS}}


def summarize(doc: Dict) -> Dict:
    """Qualität je Bereich der Analyse: 'combined' + 'per_coin:<SYMBOL>'."""
    out: Dict[str, Dict] = {}
    comb = (doc.get("combined") or {}).get("per_symbol")
    if comb:
        q = summarize_scope(comb)
        if q:
            out["combined"] = q
    for sym, pc in (doc.get("per_coin") or {}).items():
        if pc and not pc.get("error"):
            q = summarize_scope({sym: pc})
            if q:
                out[f"per_coin:{sym}"] = q
    return out
