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

from services import regime_reference
from services.setup_asset_class import LABELS, asset_class_of

GRADE_GOOD = 65.0   # ab hier: fürs Umschalten im Paper-/Live-Trading brauchbar
GRADE_OK = 50.0     # dazwischen: nur mit Bestätigung/Filter nutzen
MIN_HOLDOUT_BARS = 200
GRADE_ORDER = {"schwach": 0, "mittel": 1, "gut": 2, "sehr gut": 3}
# Benchmark „sehr gut“: ALLE Kriterien müssen erfüllt sein (transparent in der UI)
VG_REFERENCE_HOLDOUT = 72.0   # Referenz-Treffer im Holdout (detektor-unabhängig)
VG_LIVE_FINAL = 80.0          # Live=Final (Holdout) – Live-Sicht stabil
VG_LAG_SHARE = 1.0 / 3.0      # Referenz-Lag ≤ ⅓ der Ø Phasendauer
VG_MISSED_MAX = 15.0          # verpasste Referenz-Phasen ≤ 15 %
SWEET_SPOT_DAYS = (5.0, 15.0)  # Ø Phasendauer fürs Daytrading-Umschalten


def _mean(vals: List[Optional[float]]) -> Optional[float]:
    xs = [float(v) for v in vals if v is not None]
    return round(sum(xs) / len(xs), 1) if xs else None


def grade_of(holdout_pct: Optional[float], overall_pct: Optional[float],
             holdout_bars: int, reference_pct: Optional[float] = None,
             reference_basis: str = None) -> Dict:
    """Note + Klartext. Ohne Holdout zählt der Gesamtwert, aber mit Hinweis.
    `reference_pct` (Live vs. detektor-unabhängige Referenz) begrenzt die Note
    nach oben: Live=Final allein ist beim Detektor 'ema' fast immer ~98 %
    (Selbst-Übereinstimmung), sagt also nichts über verpasste Trends."""
    basis = "holdout" if holdout_pct is not None and holdout_bars >= MIN_HOLDOUT_BARS else "overall"
    pct = holdout_pct if basis == "holdout" else overall_pct
    if pct is None:
        return {"grade": "unbewertet", "basis": basis, "pct": None,
                "reference_pct": reference_pct, "reference_grade": None,
                "text": "Keine Live=Final-Kennzahl vorhanden (nur Detektoren reactive/ema/kombi liefern sie)."}
    if pct >= GRADE_GOOD:
        g, txt = "gut", "Live-Erkennung trifft die finalen Phasen zuverlässig – fürs Umschalten geeignet."
    elif pct >= GRADE_OK:
        g, txt = "mittel", "Live-Erkennung trifft die Richtung mehrheitlich, aber mit spürbarer Verzögerung/Flackern – nur mit Bestätigung nutzen."
    else:
        g, txt = "schwach", "Live-Erkennung liegt zu oft daneben – Detektor/Einstellungen ändern (Kalibrierung, EMA-Vergleich) und neu analysieren."
    ref_grade = regime_reference.grade(reference_pct)
    if ref_grade is not None and GRADE_ORDER[ref_grade] < GRADE_ORDER[g]:
        g = ref_grade
        txt = (f"Live=Final {pct:.0f} %, aber nur {reference_pct:.0f} % Richtungs-Treffer gegen die "
               f"detektor-unabhängige Referenz ({'Holdout' if reference_basis == 'holdout' else 'gesamt'}) – "
               "der Detektor ist mit sich selbst einig, verpasst aber echte Phasen (Lag/Seitwärts-Falle). "
               "Kalibrierung gegen die Referenz oder Detektor wechseln.")
    if basis == "overall":
        txt += " Achtung: kein belastbarer Holdout – Wert stammt aus dem Gesamtzeitraum (inkl. Training)."
    return {"grade": g, "basis": basis, "pct": pct,
            "reference_pct": reference_pct, "reference_grade": ref_grade, "text": txt}


def _symbol_row(sym: str, entry: Dict) -> Optional[Dict]:
    ag = (entry or {}).get("live_agreement") or {}
    val = (entry or {}).get("validation") or {}
    corr = (entry or {}).get("corrections") or {}
    ref = (entry or {}).get("reference") or {}
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
            "avg_delay_days": corr.get("avg_delay_days"),
            "reference_pct": ref.get("direction_pct"),
            "reference_holdout_pct": ref.get("holdout_direction_pct"),
            "reference_lag_days": ref.get("mean_lag_days"),
            "reference_missed_pct": ref.get("missed_pct")}


def benchmark_checks(agg: Dict) -> List[Dict]:
    """Benchmark „sehr gut“ (rein): eine Zeile je Kriterium mit Ist, Ziel, ok."""
    def row(key, label, value, target, ok):
        return {"key": key, "label": label, "value": value, "target": target, "ok": bool(ok)}
    ref_h = agg.get("reference_holdout_pct")
    lf = agg.get("holdout_direction_pct")
    phase = agg.get("avg_segment_days")
    lag = agg.get("reference_lag_days")
    missed = agg.get("reference_missed_pct")
    lo, hi = SWEET_SPOT_DAYS
    return [
        row("holdout_bars", "Belastbarer Holdout", agg.get("holdout_bars"),
            f"≥ {MIN_HOLDOUT_BARS} Kerzen", (agg.get("holdout_bars") or 0) >= MIN_HOLDOUT_BARS),
        row("reference_holdout", "Referenz-Treffer (Holdout)", ref_h, f"≥ {VG_REFERENCE_HOLDOUT:.0f} %",
            ref_h is not None and ref_h >= VG_REFERENCE_HOLDOUT),
        row("live_final", "Live=Final (Holdout)", lf, f"≥ {VG_LIVE_FINAL:.0f} %",
            lf is not None and lf >= VG_LIVE_FINAL),
        row("lag", "Referenz-Lag", lag, "≤ ⅓ Ø Phasendauer",
            lag is not None and phase is not None and lag <= phase * VG_LAG_SHARE),
        row("missed", "Verpasste Phasen", missed, f"≤ {VG_MISSED_MAX:.0f} %",
            missed is not None and missed <= VG_MISSED_MAX),
        row("sweet_spot", "Ø Phasendauer im Sweet Spot", phase, f"{lo:.0f}–{hi:.0f} Tage",
            phase is not None and lo <= phase <= hi),
        row("validation", "Plausibilitäts-Validierung", agg.get("validation_passed"), "bestanden",
            agg.get("validation_passed") is not False),
    ]


def apply_benchmark(agg: Dict) -> Dict:
    """„gut“ + alle Benchmark-Kriterien erfüllt -> „sehr gut“ (nie Abstufung)."""
    checks = benchmark_checks(agg)
    agg["benchmark"] = {"grade": "sehr gut", "checks": checks,
                        "passed": sum(c["ok"] for c in checks), "total": len(checks)}
    if agg.get("grade") == "gut" and all(c["ok"] for c in checks):
        agg["grade"] = "sehr gut"
        agg["text"] = ("Benchmark „sehr gut“ erfüllt: trifft die echten Phasen im Holdout zuverlässig, "
                       "reagiert schnell (Lag ≤ ⅓ Phase), verpasst kaum Phasen und die Phasendauer "
                       "liegt im handelbaren Sweet Spot – für Shadow/Wirksam die beste Grundlage.")
    return agg


def _aggregate(rows: List[Dict]) -> Dict:
    hb = sum(r["holdout_bars"] for r in rows)
    hold = _mean([r["holdout_direction_pct"] for r in rows])
    overall = _mean([r["direction_pct"] for r in rows])
    ref_hold = _mean([r.get("reference_holdout_pct") for r in rows])
    ref_all = _mean([r.get("reference_pct") for r in rows])
    ref_basis = "holdout" if ref_hold is not None and hb >= MIN_HOLDOUT_BARS else "overall"
    ref_pct = ref_hold if ref_basis == "holdout" else ref_all
    return apply_benchmark({"n_symbols": len(rows),
            "holdout_direction_pct": hold, "direction_pct": overall,
            "trend_hit_pct": _mean([r["trend_hit_pct"] for r in rows]),
            "violation_bars_pct": _mean([r["violation_bars_pct"] for r in rows]),
            "avg_segment_days": _mean([r["avg_segment_days"] for r in rows]),
            "avg_delay_days": _mean([r["avg_delay_days"] for r in rows]),
            "reference_holdout_pct": ref_hold, "reference_direction_pct": ref_all,
            "reference_lag_days": _mean([r.get("reference_lag_days") for r in rows]),
            "reference_missed_pct": _mean([r.get("reference_missed_pct") for r in rows]),
            "validation_passed": all(r["validation_passed"] is not False for r in rows),
            "holdout_bars": hb, **grade_of(hold, overall, hb, ref_pct, ref_basis)})


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
                           "reference_good": regime_reference.REF_GOOD,
                           "reference_ok": regime_reference.REF_OK,
                           "min_holdout_bars": MIN_HOLDOUT_BARS,
                           "very_good": {"reference_holdout": VG_REFERENCE_HOLDOUT,
                                         "live_final": VG_LIVE_FINAL,
                                         "lag_share": round(VG_LAG_SHARE, 3),
                                         "missed_max": VG_MISSED_MAX,
                                         "sweet_spot_days": list(SWEET_SPOT_DAYS)}}}


def grade_for_classes(doc: Dict, asset_classes: Optional[List[str]] = None) -> Optional[str]:
    """Schwächste Note der kombinierten Analyse über die angefragten Klassen
    (ohne Klassen: Gesamtnote). None, wenn nicht bewertbar."""
    per_symbol = (doc.get("combined") or {}).get("per_symbol") or \
        {s: pc for s, pc in (doc.get("per_coin") or {}).items() if pc and not pc.get("error")}
    q = summarize_scope(per_symbol)
    if not q:
        return None
    if asset_classes:
        grades = [q["classes"][c]["grade"] for c in asset_classes if c in q["classes"]]
    else:
        grades = [q["overall"]["grade"]]
    grades = [g for g in grades if g in GRADE_ORDER]
    return min(grades, key=lambda g: GRADE_ORDER[g]) if grades else None


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
