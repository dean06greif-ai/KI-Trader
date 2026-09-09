"""Runner-Policy: Wann darf eine Position „ins Unendliche“ laufen und wie wird
der nachgezogene SL gegen Rauschen und Liquidation abgesichert? (rein, testbar)

Runner (Rest nach TP1 ohne festes Ziel, Trailing übernimmt) war bisher nur für
Swing-Trades erlaubt. Neu: auch Scalp-/News-Trades dürfen Runner sein, wenn
 * `runner_scalp_enabled` (KI-Config, Default an) und
 * der Trade News-getrieben ist (news_impact != neutral) – oder
   `runner_scalp_news_only` aus ist (dann jeder Scalp mit runner=true).

Trailing-Schutz:
 * `noise_safe_sl`: ein nachgezogener SL muss mindestens max(ATR×Faktor,
   min_pct) vom aktuellen Kurs entfernt bleiben – sonst fliegt man durch
   Rauschen instant raus. Zu enger Vorschlag wird auf den Mindestabstand
   zurückgesetzt (wenn das noch eine Verbesserung ist), sonst verworfen.
 * `liq_safe_sl`: nach Margen-Freisetzung liegt die Liq oft ÜBER dem Entry –
   der SL muss mit Puffer vor der Liq bleiben (nutzt sl_liq_guard).
"""
from typing import Dict, List, Optional

SCALP_RUNNER_TPF_R = 8.0      # Runner-Endziel Scalp: 8R (Notausgang, Trailing steuert)
SCALP_RUNNER_TPF_CAP = 0.15   # max. 15 % vom Entry
NOISE_ATR_MULT = 1.0
NOISE_MIN_PCT = 0.10          # Mindestabstand SL<->Kurs in % (Scalp)
SWING_NOISE_MIN_PCT = 0.30


def atr(candles: List[Dict], period: int = 14) -> float:
    trs = []
    for i in range(max(1, len(candles) - period), len(candles)):
        h, l = float(candles[i]["high"]), float(candles[i]["low"])
        pc = float(candles[i - 1]["close"])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def runner_allowed(dec: Dict, config: Dict) -> bool:
    """Darf dieser KI-Entscheid ein Runner sein?"""
    if not bool(dec.get("runner")):
        return False
    if str(dec.get("horizon") or "scalp") == "swing":
        return True
    cfg = config or {}
    if not cfg.get("runner_scalp_enabled", True):
        return False
    if cfg.get("runner_scalp_news_only", True):
        return str(dec.get("news_impact") or "neutral") in ("positive", "negative")
    return True


def scalp_runner_tpf(sl_pct: float, tpf_pct: float) -> float:
    """Scalp-Runner: Endziel sehr weit (Notausgang) – Trailing übernimmt."""
    return max(tpf_pct, min(SCALP_RUNNER_TPF_CAP, sl_pct * SCALP_RUNNER_TPF_R))


def noise_safe_sl(side: str, price: float, new_sl: float, cur_sl: float,
                  atr_val: float, min_pct: float = NOISE_MIN_PCT,
                  atr_mult: float = NOISE_ATR_MULT) -> Optional[float]:
    """SL-Vorschlag gegen Rauschen prüfen. Rückgabe: sicherer SL oder None."""
    if price <= 0 or new_sl <= 0:
        return None
    min_dist = max(float(atr_val or 0) * atr_mult, price * min_pct / 100)
    long = str(side).upper() == "LONG"
    if long:
        limit = price - min_dist
        safe = min(new_sl, limit)
        return round(safe, 8) if safe > cur_sl else None
    limit = price + min_dist
    safe = max(new_sl, limit)
    return round(safe, 8) if safe < cur_sl else None


def liq_safe_sl(side: str, entry: float, leverage: float, price: float,
                new_sl: float, buffer_pct: float = 0.3) -> Optional[float]:
    """SL darf nicht hinter der (nach Margen-Freisetzung verschobenen) Liq liegen.
    Rückgabe: SL (ggf. auf Liq+Puffer angehoben) oder None = nicht ausführbar."""
    from services.bitunix_trade import sl_liq_guard
    needed, _liq, ok = sl_liq_guard(side, entry, leverage, price, new_sl,
                                    buffer_pct=buffer_pct)
    if needed is None:
        return round(new_sl, 8)
    return round(needed, 8) if ok else None


def trail_candidate(t: Dict, candles: List[Dict], price: float, new_sl: float,
                    cur_sl: float) -> Optional[float]:
    """Gesamtprüfung eines Trailing-Vorschlags: Rauschen + Liq (rein)."""
    return trail_decision(t, candles, price, new_sl, cur_sl)["sl"]


def trail_decision(t: Dict, candles: List[Dict], price: float, new_sl: float,
                   cur_sl: float) -> Dict:
    """Wie trail_candidate, aber mit nachvollziehbarer Begründung:
    {sl, reason, note} – reason in ok|adjusted_noise|adjusted_liq|rejected_noise|rejected_liq."""
    side = str(t.get("side") or "LONG")
    swing = str(t.get("ai_horizon") or "") == "swing"
    min_pct = SWING_NOISE_MIN_PCT if swing else NOISE_MIN_PCT
    a = atr(candles) if candles else 0.0
    min_dist = max(a * NOISE_ATR_MULT, price * min_pct / 100)
    safe = noise_safe_sl(side, price, new_sl, cur_sl, a, min_pct)
    dist_txt = (f"Mindestabstand {min_dist:.6g} = " + (f"{NOISE_ATR_MULT:g}×ATR" if a * NOISE_ATR_MULT >= price * min_pct / 100
                                                        else f"{min_pct:g}% vom Kurs"))
    if safe is None:
        return {"sl": None, "reason": "rejected_noise",
                "note": f"TRAIL-SKIP: Key-Level-SL {new_sl} abgelehnt – Rauschen ({dist_txt}, keine Verbesserung zu {cur_sl})"}
    reason, note = "ok", ""
    if abs(safe - new_sl) > 1e-12:
        reason, note = "adjusted_noise", f"Rausch-Schutz: {new_sl} -> {safe} ({dist_txt})"
    lev = float(t.get("leverage") or 0)
    if lev > 0 and (t.get("profit_secured") or t.get("profit_margin_released")):
        liq_ok = liq_safe_sl(side, float(t.get("entry") or 0), lev, price, safe,
                             float(t.get("profit_secure_sl_liq_buffer_pct") or 0.3))
        if liq_ok is None:
            return {"sl": None, "reason": "rejected_liq",
                    "note": f"TRAIL-SKIP: Key-Level-SL {new_sl} abgelehnt – Liq-Schutz (Hebel {lev:g}x: nötiger SL vor der Liq läge zu nah am Kurs {price})"}
        if abs(liq_ok - safe) > 1e-12:
            reason, note = "adjusted_liq", f"Liq-Schutz: {safe} -> {liq_ok} (SL bleibt mit Puffer vor der Liq, Hebel {lev:g}x)"
            safe = liq_ok
    return {"sl": safe, "reason": reason, "note": note}


def runner_stats(trades: List[Dict]) -> Dict:
    """News-Runner-Auswertung (rein): realisiertes R je Runner-Trade vs. 'voller TP'
    (= komplette Position bei TP1 geschlossen, sofern TP1 erreicht wurde)."""
    rows = []
    for t in trades:
        if not t.get("ai_runner"):
            continue
        try:
            entry, exit_p = float(t.get("entry") or 0), float(t.get("exit_price") or 0)
            sl0 = float(t.get("initial_sl") or t.get("sl") or 0)
            tp1 = float(t.get("tp1") or 0)
        except (TypeError, ValueError):
            continue
        risk = float(t.get("risk") or abs(entry - sl0) or 0)
        if entry <= 0 or exit_p <= 0 or risk <= 0 or tp1 <= 0:
            continue
        long = str(t.get("side")).upper() == "LONG"
        sign = 1 if long else -1
        real_r = (exit_p - entry) * sign / risk
        peak = float(t.get("peak_price") or 0)
        tp1_hit = peak > 0 and ((peak >= tp1) if long else (peak <= tp1))
        full_tp_r = (tp1 - entry) * sign / risk if tp1_hit else real_r
        rows.append({"id": t.get("id"), "symbol": t.get("symbol"), "horizon": t.get("ai_horizon") or "scalp",
                     "news": str(t.get("ai_news_impact") or "neutral") != "neutral",
                     "real_r": round(real_r, 2), "full_tp_r": round(full_tp_r, 2),
                     "delta_r": round(real_r - full_tp_r, 2), "tp1_hit": tp1_hit,
                     "realized_pnl": float(t.get("realized_pnl") or 0), "closed_at": t.get("closed_at")})

    def _agg(sub: List[Dict]) -> Dict:
        n = len(sub)
        if not n:
            return {"n": 0, "sum_delta_r": 0.0, "avg_delta_r": 0.0, "better": 0, "worse": 0,
                    "sum_real_r": 0.0, "sum_full_tp_r": 0.0, "realized_pnl": 0.0}
        return {"n": n, "sum_delta_r": round(sum(r["delta_r"] for r in sub), 2),
                "avg_delta_r": round(sum(r["delta_r"] for r in sub) / n, 2),
                "better": sum(1 for r in sub if r["delta_r"] > 0.05),
                "worse": sum(1 for r in sub if r["delta_r"] < -0.05),
                "sum_real_r": round(sum(r["real_r"] for r in sub), 2),
                "sum_full_tp_r": round(sum(r["full_tp_r"] for r in sub), 2),
                "realized_pnl": round(sum(r["realized_pnl"] for r in sub), 2)}

    return {"all": _agg(rows), "news": _agg([r for r in rows if r["news"]]),
            "scalp": _agg([r for r in rows if r["horizon"] != "swing"]),
            "swing": _agg([r for r in rows if r["horizon"] == "swing"]),
            "trades": sorted(rows, key=lambda r: str(r.get("closed_at") or ""), reverse=True)[:30]}
