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
    side = str(t.get("side") or "LONG")
    min_pct = SWING_NOISE_MIN_PCT if str(t.get("ai_horizon") or "") == "swing" else NOISE_MIN_PCT
    safe = noise_safe_sl(side, price, new_sl, cur_sl, atr(candles) if candles else 0.0, min_pct)
    if safe is None:
        return None
    lev = float(t.get("leverage") or 0)
    if lev > 0 and (t.get("profit_secured") or t.get("profit_margin_released")):
        safe = liq_safe_sl(side, float(t.get("entry") or 0), lev, price, safe,
                           float(t.get("profit_secure_sl_liq_buffer_pct") or 0.3))
    return safe
