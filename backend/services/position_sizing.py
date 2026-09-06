"""Risiko-basierte Positionsgröße für den KI-Trader (rein & testbar).

Befund Prod 02.09.2026 (scripts/sizing_check_prod.py): Live-Marge Ø 11,6 USDT,
Median 8,2 USDT bei ~700 USDT Equity – Kette Coin-max_capital 30 × capital_pct
(LLM, Modus 20–30 %) × ml_risk_scale 0,5 = 1,5–4,5 USDT Marge bei 6–8x Hebel.
Risiko pro Trade damit ~0,1–0,5 USDT (0,02–0,07 % der Equity) – die Fees und
KI-/Render-Kosten können so nie verdient werden.

Neuer Modus "risk" (Standard nach Boot-Migration risk_sizing_v1):
  Risiko-Budget  = Equity × risk_per_trade_pct × Skalierung
  Notional       = Risiko-Budget / SL-Abstand
  Hebel          = Liquidation liegt `auto_lev_value` % hinter dem SL
                   (Deans Taktik: hoher Hebel, kleine Marge, SL vor Liq),
                   gedeckelt durch risk_max_leverage, Coin-Max und Swing-Cap
  Marge          = Notional / Hebel, gedeckelt durch risk_max_margin_pct × Equity

Skalierung = KEIN multiplikatives Stapeln mehr: aus Überzeugung (capital_pct
-> [conviction_floor, 1]) und ML-Risiko-Faktor wird das MINIMUM genommen
(risk_stack_reductions=False). Der alte Pfad ("legacy") bleibt unverändert
erhalten und ist per sizing_mode="legacy" wählbar.
"""
from typing import Dict, Optional, Tuple

DEFAULTS: Dict = {
    "sizing_mode": "risk",            # risk | legacy (altes Verhalten)
    "risk_per_trade_pct": 2.0,        # % der Equity pro Trade (Moderat: 2–3)
    "risk_max_margin_pct": 15.0,      # Marge-Deckel in % der Equity
    "risk_max_leverage": 15,          # Hebel-Deckel im Risiko-Modus (02.09.: 50 -> 15, Slippage × Hebel)
    "risk_conviction_floor": 0.5,     # capital_pct 10 -> mind. 50 % des Budgets
    "risk_stack_reductions": False,   # True = Überzeugung × ML (altes Stapeln)
    "risk_collection_scale": 1.0,     # Sammel-Trades (Paper) relativ zum Budget
}

CLAMPS = {
    "risk_per_trade_pct": (0.1, 10.0),
    "risk_max_margin_pct": (1.0, 100.0),
    "risk_max_leverage": (1, 200),
    "risk_conviction_floor": (0.1, 1.0),
    "risk_collection_scale": (0.1, 1.0),
}


def clamp_updates(updates: Dict, cfg: Dict) -> None:
    """update_config-Klemmen für die Sizing-Keys (mutiert cfg)."""
    if "sizing_mode" in updates and updates["sizing_mode"] in ("legacy", "risk"):
        cfg["sizing_mode"] = updates["sizing_mode"]
    if "risk_stack_reductions" in updates:
        cfg["risk_stack_reductions"] = bool(updates["risk_stack_reductions"])
    for key, (lo, hi) in CLAMPS.items():
        if key in updates:
            try:
                v = float(updates[key])
            except (TypeError, ValueError):
                continue
            v = max(lo, min(hi, v))
            cfg[key] = int(v) if key == "risk_max_leverage" else v


def conviction_scale(capital_pct, floor: float = 0.5) -> float:
    """capital_pct (10–100) -> Skalierung [floor, 1.0]. Fehlend/ungültig = 1.0."""
    try:
        p = float(capital_pct)
    except (TypeError, ValueError):
        return 1.0
    if p <= 0:
        return 1.0
    p = max(10.0, min(100.0, p))
    floor = max(0.1, min(1.0, float(floor)))
    return round(floor + (1.0 - floor) * (p - 10.0) / 90.0, 4)


def combined_scale(conv: float, ml_scale: float, stack: bool = False) -> Tuple[float, str]:
    """Überzeugung und ML-Faktor kombinieren – Standard: Minimum statt Produkt."""
    conv = max(0.1, min(1.0, float(conv or 1.0)))
    ml = float(ml_scale or 1.0)
    ml = max(0.1, min(1.0, ml)) if ml > 0 else 1.0
    if stack:
        return round(conv * ml, 4), f"Überzeugung ×{conv:g} × ML ×{ml:g}"
    if ml < conv:
        return round(ml, 4), f"ML-Risiko ×{ml:g} (Überzeugung ×{conv:g} nicht gestapelt)"
    return round(conv, 4), f"Überzeugung ×{conv:g}"


def risk_leverage(cfg: Dict, params: Dict, entry: float, sl: float,
                  coin_max_lev: float) -> float:
    """Hebel so, dass die Liquidation `auto_lev_value` % hinter dem SL liegt."""
    from services.backtester import effective_leverage
    forced = dict(cfg)
    forced["auto_leverage_enabled"] = True
    forced["auto_lev_max"] = float(params.get("max_leverage") or 50)
    forced.setdefault("auto_lev_value", 0.5)
    forced.setdefault("auto_lev_mode", "liq_pct")
    lev = effective_leverage(forced, entry, sl)
    swing_cap = float(params.get("swing_cap") or 0)
    if params.get("is_swing") and swing_cap > 0:
        lev = min(lev, swing_cap)
    if coin_max_lev and coin_max_lev > 0:
        lev = min(lev, float(coin_max_lev))
    return round(max(1.0, lev), 2)


def build_params(ai_cfg: Dict, dec: Dict, is_swing: bool, ml_scale: float = 1.0,
                 collection: bool = False, setup_scale: float = 1.0) -> Dict:
    """Sizing-Parameter fürs Signal (ai_engine -> bitunix_trade), rein.
    setup_scale = Kapital-Zuweisung je Setup × Asset (services/setup_capital.py)."""
    g = lambda k: ai_cfg.get(k, DEFAULTS[k])  # noqa: E731
    return {
        "mode": "risk",
        "risk_pct": float(g("risk_per_trade_pct")),
        "max_margin_pct": float(g("risk_max_margin_pct")),
        "max_leverage": float(g("risk_max_leverage")),
        "conviction_floor": float(g("risk_conviction_floor")),
        "stack": bool(g("risk_stack_reductions")),
        "capital_pct": dec.get("capital_pct"),
        "ml_scale": float(ml_scale or 1.0),
        "is_swing": bool(is_swing),
        "swing_cap": float(ai_cfg.get("swing_max_leverage", 8) or 8),
        "collection_scale": float(g("risk_collection_scale")) if collection else 1.0,
        "setup_scale": max(0.1, min(1.0, float(setup_scale or 1.0))),
    }


def compute(params: Dict, cfg: Dict, entry: float, sl: float, equity: Optional[float],
            coin_max_lev: float = 200.0) -> Optional[Dict]:
    """Marge/Hebel aus Risiko-Budget. None = nicht berechenbar -> Legacy-Pfad."""
    try:
        entry, sl = float(entry), float(sl)
        equity = float(equity) if equity is not None else 0.0
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or equity <= 0:
        return None
    sl_dist = abs(entry - sl) / entry
    if sl_dist <= 0:
        return None
    conv = conviction_scale(params.get("capital_pct"), params.get("conviction_floor", 0.5))
    scale, scale_note = combined_scale(conv, params.get("ml_scale", 1.0), params.get("stack", False))
    scale *= float(params.get("collection_scale") or 1.0)
    setup_scale = float(params.get("setup_scale") or 1.0)
    if 0 < setup_scale < 1.0:
        scale *= setup_scale
        scale_note += f" × Setup/Asset ×{setup_scale:g}"
    risk_usdt = equity * float(params.get("risk_pct") or 2.0) / 100.0 * scale
    lev = risk_leverage(cfg, params, entry, sl, coin_max_lev)
    notional = risk_usdt / sl_dist
    margin = notional / lev
    notes = [f"Risiko {risk_usdt:.2f} USDT ({float(params.get('risk_pct') or 2.0):g}% × {scale:g}), "
             f"SL {sl_dist * 100:.2f}%, Hebel {lev:g}x", scale_note]
    cap = equity * float(params.get("max_margin_pct") or 15.0) / 100.0
    capped = False
    if margin > cap:
        margin = cap
        notional = margin * lev
        risk_usdt = notional * sl_dist
        capped = True
        notes.append(f"Marge auf {cap:.2f} USDT gedeckelt (risk_max_margin_pct) -> "
                     f"Risiko {risk_usdt:.2f} USDT")
    return {"margin": round(margin, 6), "leverage": lev, "notional": round(notional, 4),
            "risk_usdt": round(risk_usdt, 4), "risk_pct_eff": round(risk_usdt / equity * 100, 4),
            "scale": round(scale, 4), "sl_dist_pct": round(sl_dist * 100, 4),
            "capped": capped, "equity": round(equity, 2), "note": " | ".join(notes)}
