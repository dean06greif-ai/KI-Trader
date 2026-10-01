"""Asset-Vorschlag je Regime für die Dynamik-Werkbank (vor dem Suchstart).

Bewertet jedes Asset einer Regime-Analyse je Regime aus drei Quellen:
  * Marktdaten der Analyse: wie oft/lange das Asset das Regime tatsächlich
    durchläuft (Segmente der gemeinsamen Erkennung) – ohne Präsenz keine Trades.
  * Gleichlauf: Übereinstimmung der Regime-Labels mit den anderen Assets
    (coin_similarity) – Ausreißer passen schlecht in eine gemeinsame Erkennung.
  * Bisherige Ergebnisse: geschlossene Trades dynamischer Strategien dieser
    Analyse je (Regime, Asset).
  * Kurs-Korrelation & Volatilität je Regime aus vorhandenen Kerzen
    (services/asset_market.py): Assets, die im Regime mitlaufen und eine
    ähnliche Schwankung haben, passen in eine gemeinsame Strategie; nahezu
    identische Kurse (Korrelation > 0,92) bringen kaum Mehrwert.
Reine Bewertung (`score_assets`) ist ohne DB testbar; `suggest` lädt die Daten.
"""
import math
from typing import Dict, List, Optional

MIN_SEGMENTS = 2          # weniger Regime-Phasen = kaum verwertbare Stichprobe
PICK_THRESHOLD = 0.5      # ab diesem Score gilt ein Asset als passend
PERF_FULL_TRADES = 10     # ab so vielen Trades zählt das Ergebnis voll
W_PRESENCE, W_CONSISTENCY, W_PERF = 0.45, 0.25, 0.30
W_CORR, W_VOL = 0.15, 0.10
REDUNDANT_CORR = 0.92


def regime_presence(per_symbol: Dict[str, Dict]) -> Dict[str, Dict[int, Dict]]:
    """{symbol: {regime: {bars, segments, share}}} aus den Segmenten (rein)."""
    out: Dict[str, Dict[int, Dict]] = {}
    for sym, info in (per_symbol or {}).items():
        segs = (info or {}).get("segments") or []
        total = sum(int(s.get("bars") or 0) for s in segs) or 1
        rows: Dict[int, Dict] = {}
        for s in segs:
            r = rows.setdefault(int(s.get("regime", -1)), {"bars": 0, "segments": 0})
            r["bars"] += int(s.get("bars") or 0)
            r["segments"] += 1
        for r in rows.values():
            r["share"] = round(r["bars"] / total * 100, 1)
        out[sym] = rows
    return out


def consistency(similarity: List[Dict], symbols: List[str]) -> Dict[str, float]:
    """Mittlere Label-Übereinstimmung je Asset mit den übrigen (0..1, rein)."""
    acc: Dict[str, List[float]] = {s: [] for s in symbols}
    for p in similarity or []:
        for a, b in ((p.get("a"), p.get("b")), (p.get("b"), p.get("a"))):
            if a in acc and b in acc:
                acc[a].append(float(p.get("agreement_pct") or 0) / 100.0)
    return {s: round(sum(v) / len(v), 3) if v else 0.5 for s, v in acc.items()}


def perf_score(st: Optional[Dict]) -> Optional[float]:
    """Ergebnis-Score -1..1, gewichtet nach Stichprobe (rein). None = keine Daten."""
    if not st or not st.get("trades"):
        return None
    n = int(st["trades"])
    avg = float(st.get("pnl") or 0) / n
    wr = float(st.get("wins") or 0) / n
    raw = 0.6 * math.tanh(avg / 10.0) + 0.4 * (wr - 0.5) * 2
    return round(max(-1.0, min(1.0, raw)) * min(1.0, n / PERF_FULL_TRADES), 3)


def corr_fit(corr: Optional[float], max_corr: Optional[float]) -> Optional[float]:
    """Mitlaufen im Regime: 0 bei Korrelation <= 0,1, voll ab 0,7; Dublette gedämpft (rein)."""
    if corr is None:
        return None
    fit = max(0.0, min(1.0, (corr - 0.1) / 0.6))
    return round(fit * 0.7, 3) if (max_corr or 0) > REDUNDANT_CORR else round(fit, 3)


def vol_fit(vol_rel: Optional[float]) -> Optional[float]:
    """Schwankung nahe am Gruppen-Median = 1, Faktor 3 daneben = 0 (rein)."""
    if not vol_rel or vol_rel <= 0:
        return None
    return round(max(0.0, 1.0 - abs(math.log(vol_rel)) / math.log(3)), 3)


def _combine(parts: List[tuple]) -> float:
    """Gewichtetes Mittel über vorhandene Teil-Scores (None = keine Daten)."""
    got = [(w, v) for w, v in parts if v is not None]
    return sum(w * v for w, v in got) / sum(w for w, _ in got) if got else 0.0


def score_assets(symbols: List[str], regimes: List[Dict], presence: Dict[str, Dict[int, Dict]],
                 cons: Dict[str, float], perf: Dict[str, Dict[str, Dict]],
                 market: Optional[Dict[str, Dict[str, Dict]]] = None) -> Dict:
    """Ranking je Regime + Gesamtvorschlag (rein & testbar)."""
    market = market or {}
    per_regime = []
    for reg in regimes:
        rid = int(reg.get("id"))
        avg_share = (sum((presence.get(s) or {}).get(rid, {}).get("share", 0) for s in symbols)
                     / max(len(symbols), 1)) or 1.0
        rows = []
        for sym in symbols:
            pr = (presence.get(sym) or {}).get(rid) or {"bars": 0, "segments": 0, "share": 0.0}
            p_pres = min(1.0, pr["share"] / avg_share) if pr["segments"] >= MIN_SEGMENTS else \
                min(0.3, pr["share"] / avg_share)
            p_perf = perf_score((perf.get(str(rid)) or {}).get(sym))
            c = cons.get(sym, 0.5)
            mk = (market.get(str(rid)) or {}).get(sym) or {}
            p_corr, p_vol = corr_fit(mk.get("corr"), mk.get("max_corr")), vol_fit(mk.get("vol_rel"))
            score = _combine([(W_PRESENCE, p_pres), (W_CONSISTENCY, c),
                              (W_PERF, None if p_perf is None else (p_perf + 1) / 2),
                              (W_CORR, p_corr), (W_VOL, p_vol)])
            reasons = [f"{pr['share']:.0f}% der Zeit in diesem Regime ({pr['segments']} Phasen)",
                       f"Gleichlauf {c * 100:.0f}%"]
            if mk.get("corr") is not None:
                reasons.append(f"Kurs-Korrelation Ø {mk['corr']:+.2f}"
                               + (" (fast identisch mit einem anderen Asset)" if (mk.get("max_corr") or 0) > REDUNDANT_CORR else ""))
            if mk.get("vol_rel") is not None:
                reasons.append(f"Volatilität {mk['vol_pct']:.2f}%/Balken ({mk['vol_rel']:.1f}× Median)")
            st = (perf.get(str(rid)) or {}).get(sym)
            if st:
                reasons.append(f"bisher {st['trades']} Trades, {st.get('pnl', 0):+.1f} USDT")
            rows.append({"symbol": sym, "score": round(score, 3), "pick": score >= PICK_THRESHOLD,
                         "share": pr["share"], "segments": pr["segments"],
                         "consistency": c, "perf": p_perf, "corr": mk.get("corr"),
                         "vol_rel": mk.get("vol_rel"), "reasons": reasons})
        rows.sort(key=lambda r: -r["score"])
        if rows and not any(r["pick"] for r in rows):
            rows[0]["pick"] = True  # mind. ein Asset je Regime
        per_regime.append({"regime_id": rid, "label": reg.get("label"), "assets": rows,
                           "picks": [r["symbol"] for r in rows if r["pick"]]})
    mean = {s: sum(next(r["score"] for r in pr["assets"] if r["symbol"] == s) for pr in per_regime)
            / max(len(per_regime), 1) for s in symbols}
    overall = sorted([s for s in symbols if mean[s] >= PICK_THRESHOLD], key=lambda s: -mean[s])
    for s in sorted(symbols, key=lambda s: -mean[s]):
        if len(overall) >= min(2, len(symbols)):
            break
        if s not in overall:
            overall.append(s)
    return {"per_regime": per_regime, "recommended": overall,
            "overall_scores": {s: round(v, 3) for s, v in mean.items()}}


def perf_from_trades(trades: List[Dict]) -> Dict[str, Dict[str, Dict]]:
    """{regime: {symbol: {trades, wins, pnl}}} aus geschlossenen Trades (rein)."""
    out: Dict[str, Dict[str, Dict]] = {}
    for t in trades or []:
        rid = (t.get("dynamic") or {}).get("regime")
        if rid is None or not t.get("symbol"):
            continue
        st = out.setdefault(str(rid), {}).setdefault(t["symbol"], {"trades": 0, "wins": 0, "pnl": 0.0})
        pnl = float(t.get("realized_pnl") or 0)
        st["trades"] += 1
        st["wins"] += 1 if pnl > 0 else 0
        st["pnl"] = round(st["pnl"] + pnl, 2)
    return out


async def suggest(db, analysis: Dict, regime_ids: Optional[List[int]] = None) -> Dict:
    combined = analysis.get("combined") or {}
    symbols = list(analysis.get("symbols") or [])
    regimes = list((combined.get("model") or {}).get("regimes") or analysis.get("regimes") or [])
    if regime_ids:
        wanted = {int(r) for r in regime_ids}
        regimes = [r for r in regimes if int(r.get("id")) in wanted]
    dyn_ids = [d["id"] async for d in db.dynamic_strategies.find(
        {"settings.analysis_id": analysis.get("id")}, {"_id": 0, "id": 1})]
    trades = await db.auto_trades.find(
        {"strategy_id": {"$in": dyn_ids}, "status": "closed"},
        {"_id": 0, "symbol": 1, "dynamic": 1, "realized_pnl": 1}).to_list(20000) if dyn_ids else []
    from services import asset_market
    mkt = await asset_market.market_stats(analysis, [int(r.get("id")) for r in regimes])
    res = score_assets(symbols, regimes, regime_presence(combined.get("per_symbol") or {}),
                       consistency(combined.get("coin_similarity") or [], symbols),
                       perf_from_trades(trades), mkt["stats"])
    res["sources"] = {"trades": len(trades), "dynamic_strategies": len(dyn_ids),
                      "has_segments": bool(combined.get("per_symbol")),
                      "candles": mkt["symbols_with_candles"], "market_days": mkt["days"]}
    return res
