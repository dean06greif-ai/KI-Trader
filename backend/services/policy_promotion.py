"""Promotion-Regel Champion vs. Kandidat (Audit 3.3) – reine Funktionen, unit-testbar.

Wechsel-Kriterien (ALLE müssen erfüllt sein, bewertet NUR auf Daten nach
Kandidaten-Erzeugung – das stellt der Aufrufer policy_lab sicher):
  1. n Kandidat-Trials >= min_trials (Default 40)
  2. Champion-Vergleichsbasis >= champ_min_trades (sonst kein fairer Vergleich)
  3. Netto-R-Mittel Kandidat − Champion > 0
  4. Bootstrap-Untergrenze (alpha-Quantil, Default 5%) der Differenz > 0
  5. Max-Drawdown (in R, auf der kumulierten Kurve) nicht schlechter als der Champion
     (+ Toleranz dd_tolerance)
Rollback: nach einer Promotion wird das Fenster überwacht – Verschlechterung
(negatives R-Mittel UND schlechter als vor der Promotion) => automatische Rücknahme.
Einheit beider Seiten = R-Multiple (Kandidat: net_pnl_pct / SL-Distanz-%,
Champion: realized_pnl / risk_usdt via ml_gate.money_r – Audit 2.3).
"""
import random
from typing import Dict, List, Optional

DEFAULTS = {
    "min_trials": 40,          # Kandidat braucht mindestens so viele entschiedene Trials
    "champ_min_trades": 10,    # Champion-Basis im selben Zeitraum
    "bootstrap_n": 500,
    "bootstrap_alpha": 0.05,   # Untergrenze = 5%-Quantil der Mittelwert-Differenz
    "bootstrap_block": 5,      # AP09/W02: Block-Bootstrap (korrelierte Trades)
    "dd_tolerance": 0.0,       # erlaubter Mehr-Drawdown des Kandidaten (in R)
    "rollback_window_trades": 20,  # so viele Trades nach Promotion werden überwacht
    "rollback_min_trades": 8,      # frühestens ab so vielen Trades wird zurückgerollt
}


def trial_r(trial: Dict) -> Optional[float]:
    """R-Multiple eines geschlossenen Trials: Netto-% / SL-Distanz-% (rein)."""
    if (trial or {}).get("status") != "closed":
        return None
    try:
        entry = float(trial.get("entry") or 0)
        sl = float(trial.get("sl") or 0)
        pnl = float(trial.get("net_pnl_pct") or 0)
        dist_pct = abs(entry - sl) / entry * 100 if entry else 0.0
        return round(pnl / dist_pct, 4) if dist_pct > 0 else None
    except (TypeError, ValueError):
        return None


def max_drawdown(rs: List[float]) -> float:
    """Max-Drawdown (>=0) auf der kumulierten R-Kurve, in R."""
    peak = equity = 0.0
    dd = 0.0
    for r in rs or []:
        equity += float(r)
        peak = max(peak, equity)
        dd = max(dd, peak - equity)
    return round(dd, 4)


def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def bootstrap_diff_lower(cand: List[float], champ: List[float], n_boot: int = 500,
                         alpha: float = 0.05, seed: int = 42,
                         block: int = 1) -> float:
    """Untergrenze (alpha-Quantil) der Bootstrap-Verteilung von mean(cand)-mean(champ).

    AP09/W02: block>1 = BLOCK-Bootstrap über zusammenhängende Trade-Blöcke
    (chronologische Reihenfolge) – Trades sind zeitlich/assetübergreifend
    korreliert, unabhängiges Einzeltrade-Resampling unterschätzt die
    Unsicherheit. block=1 = altes Verhalten."""
    if not cand or not champ:
        return float("-inf")
    rng = random.Random(seed)
    b = max(1, int(block))

    def _resample(xs: List[float]) -> List[float]:
        if b <= 1 or len(xs) <= b:
            return [xs[rng.randrange(len(xs))] for _ in range(len(xs))]
        out = []
        while len(out) < len(xs):
            s = rng.randrange(len(xs) - b + 1)
            out.extend(xs[s:s + b])
        return out[:len(xs)]

    diffs = []
    for _ in range(max(50, int(n_boot))):
        diffs.append(_mean(_resample(cand)) - _mean(_resample(champ)))
    diffs.sort()
    idx = min(len(diffs) - 1, max(0, int(alpha * len(diffs))))
    return round(diffs[idx], 4)


def promotion_check(cand_rs: List[float], champ_rs: List[float],
                    cfg: Optional[Dict] = None) -> Dict:
    """Alle Kriterien prüfen; promote nur wenn ALLE ok. Rein & deterministisch."""
    c = {**DEFAULTS, **(cfg or {})}
    cand = [float(x) for x in (cand_rs or [])]
    champ = [float(x) for x in (champ_rs or [])]
    mean_diff = round(_mean(cand) - _mean(champ), 4)
    boot_lower = (bootstrap_diff_lower(cand, champ, c["bootstrap_n"],
                                       c["bootstrap_alpha"],
                                       block=int(c.get("bootstrap_block", 1)))
                  if cand and champ else float("-inf"))
    cand_dd, champ_dd = max_drawdown(cand), max_drawdown(champ)
    checks = [
        {"name": "min_trials", "ok": len(cand) >= int(c["min_trials"]),
         "detail": f"{len(cand)}/{c['min_trials']} Kandidat-Trials"},
        {"name": "champ_basis", "ok": len(champ) >= int(c["champ_min_trades"]),
         "detail": f"{len(champ)}/{c['champ_min_trades']} Champion-Trades im Zeitraum"},
        {"name": "mean_diff_positive", "ok": mean_diff > 0,
         "detail": f"ΔR-Mittel = {mean_diff}"},
        {"name": "bootstrap_lower_positive", "ok": boot_lower > 0,
         "detail": f"Bootstrap-{int(c['bootstrap_alpha'] * 100)}%-Untergrenze = "
                   f"{boot_lower if boot_lower != float('-inf') else 'n/a'}"},
        {"name": "drawdown_not_worse",
         "ok": cand_dd <= champ_dd + float(c["dd_tolerance"]),
         "detail": f"DD Kandidat {cand_dd}R vs. Champion {champ_dd}R"},
    ]
    return {
        "promote": all(ch["ok"] for ch in checks),
        "checks": checks,
        "metrics": {
            "n_cand": len(cand), "n_champ": len(champ),
            "cand_mean_r": round(_mean(cand), 4), "champ_mean_r": round(_mean(champ), 4),
            "mean_diff": mean_diff,
            "bootstrap_lower": (boot_lower if boot_lower != float("-inf") else None),
            "cand_dd": cand_dd, "champ_dd": champ_dd,
        },
    }


def rollback_check(post_rs: List[float], pre_mean_r: float,
                   cfg: Optional[Dict] = None) -> Dict:
    """Rücknahme-Prüfung im Rollback-Fenster: Verschlechterung nach Promotion?"""
    c = {**DEFAULTS, **(cfg or {})}
    post = [float(x) for x in (post_rs or [])][: int(c["rollback_window_trades"])]
    if len(post) < int(c["rollback_min_trades"]):
        return {"rollback": False, "reason": f"erst {len(post)}/{c['rollback_min_trades']} "
                                             "Trades im Fenster", "post_mean_r": None}
    post_mean = round(_mean(post), 4)
    worse = post_mean < 0 and post_mean < float(pre_mean_r or 0)
    return {"rollback": worse, "post_mean_r": post_mean,
            "pre_mean_r": round(float(pre_mean_r or 0), 4),
            "reason": (f"R-Mittel nach Promotion {post_mean} < 0 und schlechter als "
                       f"vorher ({round(float(pre_mean_r or 0), 4)})" if worse
                       else "keine Verschlechterung")}
