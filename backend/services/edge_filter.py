"""Edge-Filter (KI-Trader): Kosten-, Trend- und Anlageklassen-Filter.

Befund Prod 11/2026 (1676 KI-Trades, nur lesend ausgewertet):
- brutto ≈ −0,06 R je Trade, Gebühren ≈ 0,17 R → netto −0,23 R.
- Gebühren ≤ 10 % des Risikos: netto ≈ 0 R (2. Hälfte +0,18 R), 0,2–0,3: −0,34 R.
- Mit Tages-Bias (LONG bei up, SHORT bei down): −0,11 R vs. −0,28 R gegen/ohne Bias;
  ohne Bias-Daten −0,39 R.
- Krypto −0,14 R vs. Rohstoffe/FX/Indizes −0,49 R (Slippage/Feed).

Der Filter blockt standardmäßig nur LIVE-Einstiege (Geld schützen). Paper-
und Sammel-Trades werden NICHT verändert, sondern nur markiert
(`trade.edge_filter`), damit der Edge-Bericht „besteht / besteht nicht“
out-of-sample vergleichen kann. Reine Funktionen oben, keine DB.
"""
from typing import Dict, List, Optional

DEFAULTS = {
    "edge_filter_enabled": True,
    "edge_filter_max_fee_r": 0.10,       # Roundtrip-Gebühr ≤ 10 % des riskierten Betrags
    "edge_filter_require_bias": True,    # nur mit dem Tagestrend (LONG/up, SHORT/down)
    "edge_filter_crypto_only": True,     # Rohstoffe/FX/Indizes nicht live
    "edge_filter_apply_paper": False,    # True = auch normale Paper-Trades blocken
}


def settings(ai_cfg: Optional[Dict]) -> Dict:
    ai_cfg = ai_cfg or {}
    return {k: ai_cfg.get(k, v) for k, v in DEFAULTS.items()}


def fee_r(fee_pct_per_side: float, entry: float, sl: float) -> Optional[float]:
    """Geschätzte Roundtrip-Gebühr als Anteil des Risikos (R)."""
    try:
        sl_pct = abs(float(entry) - float(sl)) / float(entry) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if sl_pct <= 0:
        return None
    return round(2.0 * float(fee_pct_per_side or 0) / sl_pct, 4)


def bias_aligned(side: Optional[str], bias: Optional[str]) -> Optional[bool]:
    """True = mit Tagestrend, False = dagegen/flat, None = keine Bias-Daten."""
    if not bias:
        return None
    s = str(side or "").upper()
    return (bias == "up" and s == "LONG") or (bias == "down" and s == "SHORT")


def evaluate(ai_cfg: Optional[Dict], *, side: str, asset_class: str,
             fee_r_est: Optional[float], bias: Optional[str]) -> Dict:
    """Urteil (rein): {"ok", "reasons", "fee_r", "bias", "asset_class"}."""
    st = settings(ai_cfg)
    reasons: List[str] = []
    try:
        max_fee = float(st["edge_filter_max_fee_r"] or 0)
    except (TypeError, ValueError):
        max_fee = DEFAULTS["edge_filter_max_fee_r"]
    if max_fee > 0 and fee_r_est is not None and fee_r_est > max_fee + 1e-9:
        reasons.append(f"Gebühren {fee_r_est * 100:.0f} % des Risikos > {max_fee * 100:.0f} % "
                       "(SL zu eng für die Kosten)")
    if st["edge_filter_require_bias"]:
        al = bias_aligned(side, bias)
        if al is None:
            reasons.append("kein Tages-Bias verfügbar")
        elif not al:
            reasons.append(f"{side} gegen Tages-Bias '{bias}'")
    if st["edge_filter_crypto_only"] and asset_class != "crypto":
        reasons.append(f"Anlageklasse {asset_class} (nur Krypto live)")
    return {"ok": not reasons, "reasons": reasons, "fee_r": fee_r_est,
            "bias": bias, "asset_class": asset_class}


def applies(ai_cfg: Optional[Dict], mode: str, collection: bool) -> bool:
    """Blockt der Filter in diesem Modus? Sammel-Trades nie (nur Markierung)."""
    st = settings(ai_cfg)
    if not st["edge_filter_enabled"] or collection:
        return False
    return mode == "live" or bool(st["edge_filter_apply_paper"])


def reason_text(verdict: Dict) -> str:
    return "Edge-Filter: " + "; ".join(verdict.get("reasons") or [])


def bucket_of_trade(t: Dict, ai_cfg: Optional[Dict] = None) -> Optional[str]:
    """Rückwirkendes Urteil für geschlossene Trades (Edge-Bericht): nutzt die
    echten Gebühren statt der Schätzung. None = Trade ohne Risiko-Angabe."""
    try:
        risk = float(t.get("risk_usdt") or 0)
        fees = float(t.get("fees_paid") or 0)
    except (TypeError, ValueError):
        return None
    if risk <= 0:
        return None
    from services.setup_asset_class import asset_class_of
    feats = (t.get("entry_market_snapshot") or {}).get("features") or {}
    v = evaluate(ai_cfg, side=t.get("side"), asset_class=asset_class_of(t.get("symbol")),
                 fee_r_est=fees / risk, bias=feats.get("daily_bias"))
    return "besteht" if v["ok"] else "besteht nicht"


# ---- Live-Freigabe-Check (Rettungsplan Phase 1) ----
READY_MIN_TRADES = 60
READY_MIN_R = 0.10


def _r(t: Dict) -> Optional[float]:
    try:
        risk = float(t.get("risk_usdt") or 0)
        return float(t.get("realized_pnl") or 0) / risk if risk > 0 else None
    except (TypeError, ValueError):
        return None


def readiness(rows: List[Dict]) -> Dict:
    """Out-of-sample-Beweis (rein): nur Trades, die beim Einstieg als
    „besteht“ markiert wurden (`trade.edge_filter.ok`). Bereit für Live, wenn
    ≥ 60 Trades, netto ≥ +0,10 R und auch die jüngere Hälfte > 0 R."""
    xs = sorted((t for t in rows if (t.get("edge_filter") or {}).get("ok") and _r(t) is not None),
                key=lambda t: str(t.get("opened_at") or ""))
    rs = [_r(t) for t in xs]
    n = len(rs)
    out = {"trades": n, "min_trades": READY_MIN_TRADES, "min_r": READY_MIN_R,
           "exp_r": None, "recent_half_r": None, "ready": False,
           "since": xs[0].get("opened_at") if xs else None}
    if not n:
        out["text"] = "Noch keine markierten Trades – der Beweis startet ab jetzt automatisch."
        return out
    exp = sum(rs) / n
    half = rs[n // 2:]
    rec = sum(half) / len(half)
    out.update(exp_r=round(exp, 3), recent_half_r=round(rec, 3))
    out["ready"] = n >= READY_MIN_TRADES and exp >= READY_MIN_R and rec > 0
    if out["ready"]:
        out["text"] = (f"Bereit: {n} Trades mit {exp:+.2f} R (jüngere Hälfte {rec:+.2f} R) – "
                       "Live mit kleinem Risiko (0,5 %) vertretbar.")
    else:
        miss = []
        if n < READY_MIN_TRADES:
            miss.append(f"{READY_MIN_TRADES - n} Trades fehlen")
        if exp < READY_MIN_R:
            miss.append(f"Ø {exp:+.2f} R < {READY_MIN_R:+.2f} R")
        if rec <= 0:
            miss.append(f"jüngere Hälfte {rec:+.2f} R")
        out["text"] = "Noch nicht bereit: " + ", ".join(miss) + "."
    return out
