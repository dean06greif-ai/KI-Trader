"""AP13 (Zielbild §4/§6): Assetgruppen-Pooling für Forschungskennzahlen.

Assetspezifische Auswertung nur bei Datenstärke – sonst Pooling über sachlich
sinnvolle Anlageklassen (setup_asset_class) MIT sichtbarer assetspezifischer
Abweichung vom Pool. Reine Funktionen, keine DB/IO.
"""
from typing import Dict, Iterable

from services.setup_asset_class import LABELS, asset_class_of

DEFAULT_METRICS = ("direction_pct", "holdout_direction_pct",
                   "inner_direction_pct", "trend_hit_pct")


def pool_rows(per_symbol: Dict[str, Dict],
              metrics: Iterable[str] = DEFAULT_METRICS,
              weight_key: str = "bars") -> Dict:
    """Kennzahlen je Symbol zu Anlageklassen-Pools zusammenfassen.

    Gewichtung nach `weight_key` (Default: Kerzenanzahl). Je Klasse zusätzlich
    die Abweichung jedes Symbols vom Pool (erste Metrik) – vorsichtige
    assetspezifische Abweichung bleibt sichtbar statt weggemittelt."""
    metrics = tuple(metrics)
    classes: Dict[str, Dict] = {}
    for sym, row in (per_symbol or {}).items():
        if not isinstance(row, dict):
            continue
        cls = asset_class_of(sym)
        c = classes.setdefault(cls, {"symbols": [], "weight": 0.0,
                                     "sums": {m: [0.0, 0.0] for m in metrics}})
        w = float(row.get(weight_key) or 0.0)
        if w <= 0:
            w = 1.0
        c["symbols"].append(sym)
        c["weight"] += w
        for m in metrics:
            v = row.get(m)
            if v is None:
                continue
            c["sums"][m][0] += float(v) * w
            c["sums"][m][1] += w
    out = {}
    for cls, c in classes.items():
        entry = {"label": LABELS.get(cls, cls), "symbols": sorted(c["symbols"]),
                 "n_symbols": len(c["symbols"]), "bars": int(round(c["weight"]))}
        for m in metrics:
            s, w = c["sums"][m]
            entry[m] = round(s / w, 1) if w > 0 else None
        lead = metrics[0]
        entry["deviation"] = {
            sym: round(float((per_symbol.get(sym) or {}).get(lead)) - entry[lead], 1)
            for sym in entry["symbols"]
            if (per_symbol.get(sym) or {}).get(lead) is not None
            and entry.get(lead) is not None}
        out[cls] = entry
    return {"basis": f"{weight_key}_weighted", "classes": out}
