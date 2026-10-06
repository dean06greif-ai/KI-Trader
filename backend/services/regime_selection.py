"""Regime-Champion je Asset: welche gespeicherte Regime-Erkennung trägt für ein
Asset am besten – robust statt rückwärts-optimiert.

Problem: mehrere Erkennungen (Timeframes, Regime-Anzahl, kombiniert vs. je Coin,
Anlageklassen). Wer nur den besten Rückblick-Wert nimmt, wählt fast sicher eine
überangepasste Variante (viele Kandidaten = Glückstreffer, Modell je Coin auf
den eigenen Daten gefittet). Regeln hier (rein, testbar, keine DB im Kern):

  * Bewertet wird NUR außerhalb des Trainings: Holdout (OOS) und innere
    Validierung – Richtungs-Macro-F1 gegen die detektor-unabhängige Referenz
    (über 3/5/9 Regime vergleichbar, weil auf Richtung auf/seit/ab reduziert).
  * Mindest-OOS (Kerzen) und Kappa > 0 (besser als Zufall), sonst nicht wählbar.
  * Stabilität: halbe Gewichtung auf das SCHWÄCHERE Fenster (inner vs. Holdout).
  * Overfit-Lücke Training -> Holdout wird bestraft, kurze OOS-Zeiträume zum
    Zufallsniveau geschrumpft (Bayes-artig), Modell je Coin mit Abschlag.
  * Ein Herausforderer löst den Amtsinhaber nur ab, wenn er in JEDEM Fenster
    besser ist UND den Score um eine mit der Kandidatenzahl wachsende Marge
    schlägt (Mehrfachtest-Korrektur). Sonst bleibt alles wie es ist.
"""
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional

from services import setup_asset_class

MIN_HOLDOUT_BARS = 200
PRIOR_F1 = 33.3            # Zufallsniveau Macro-F1 bei 3 Richtungen
SHRINK_DAYS = 60.0         # so viele OOS-Tage = halbes Vertrauen
GAP_FREE_PP = 5.0          # Training -> Holdout bis 5 Pkt. Abfall ist normal
GAP_WEIGHT = 0.5
PER_COIN_PENALTY = 1.5     # eigenes Coin-Modell = auf genau diesen Daten gefittet
WF_FAIL_PENALTY = 3.0
WF_PASS_BONUS = 1.0
BASE_MARGIN = 2.0
MARGIN_PER_LOG_N = 1.0
MIN_SCORE = 45.0
DOC_ID = "regime_asset_champions"
MODES = ("off", "suggest", "auto")


def _f(v) -> Optional[float]:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def candidate_rows(doc: Dict, symbol: str, bars_per_day: float) -> List[Dict]:
    """Kandidaten einer Analyse für ein Symbol: kombiniertes Modell + Coin-Modell."""
    from services import regime_release, research_validation
    out = []
    sources = []
    comb = ((doc.get("combined") or {}).get("per_symbol") or {}).get(symbol)
    if comb:
        sources.append(("combined", comb, "combined"))
    coin = (doc.get("per_coin") or {}).get(symbol)
    if coin and not coin.get("error"):
        sources.append(("per_coin", coin, f"coin:{symbol}"))
    st = doc.get("settings") or {}
    for scope, entry, wf_key in sources:
        ref = entry.get("reference") or {}
        wf = research_validation.walkforward_status_for(doc, wf_key)["passed"]
        out.append({
            "aid": doc.get("id"), "name": doc.get("name"), "scope": scope, "symbol": symbol,
            "timeframe": doc.get("timeframe"), "band": regime_release.band_of_timeframe(doc.get("timeframe")),
            "regime_mode": st.get("regime_mode"),
            "detector": (st.get("engine_config") or {}).get("detector"),
            "pool_size": len(doc.get("symbols") or []) if scope == "combined" else 1,
            "holdout_f1": _f(ref.get("holdout_f1_pct")), "inner_f1": _f(ref.get("inner_f1_pct")),
            "train_f1": _f(ref.get("train_f1_pct")), "holdout_kappa": _f(ref.get("holdout_kappa_pct")),
            "holdout_bars": int(ref.get("holdout_bars") or 0), "bars_per_day": float(bars_per_day or 1.0),
            "walkforward_passed": wf, "stage": (doc.get("release") or {}).get("stage") or "none",
            "created_at": doc.get("created_at")})
    return out


def evaluate(row: Dict) -> Dict:
    """Robust-Score + Begründung eines Kandidaten (rein)."""
    h, i, t = row.get("holdout_f1"), row.get("inner_f1"), row.get("train_f1")
    hb = int(row.get("holdout_bars") or 0)
    why: List[str] = []
    if h is None or hb < MIN_HOLDOUT_BARS:
        return {**row, "eligible": False, "score": None,
                "why": [f"zu wenig Out-of-Sample ({hb} < {MIN_HOLDOUT_BARS} Kerzen)"]}
    kappa = row.get("holdout_kappa")
    if kappa is not None and kappa <= 0:
        return {**row, "eligible": False, "score": None,
                "why": [f"Holdout-Kappa {kappa:.1f} ≤ 0 – nicht besser als Zufall"]}
    wins = [w for w in (row.get("windows") or []) if w is not None]
    if row.get("fair") and len(wins) < len(row.get("windows") or []):
        return {**row, "eligible": False, "score": None,
                "why": ["fairer Vergleich: nicht in allen Teilfenstern messbar"]}
    worst = min(wins + [h]) if wins else (min(h, i) if i is not None else h)
    gap = max((t if t is not None else h) - h, 0.0)
    raw = 0.5 * h + 0.5 * worst - GAP_WEIGHT * max(gap - GAP_FREE_PP, 0.0)
    oos_days = hb / max(float(row.get("bars_per_day") or 1.0), 1e-9)
    shrink = oos_days / (oos_days + SHRINK_DAYS)
    score = PRIOR_F1 + (raw - PRIOR_F1) * shrink
    if gap > GAP_FREE_PP:
        why.append(f"Overfit-Lücke Training→Holdout {gap:.1f} Pkt.")
    if row.get("fair"):
        why.append(f"fair: gleicher Zeitraum, Teilfenster {'/'.join(f'{w:.0f}' for w in wins)}")
    elif i is None:
        why.append("keine innere Validierung – nur 1 Fenster")
    if row.get("scope") == "per_coin":
        score -= PER_COIN_PENALTY
        why.append(f"Coin-Modell (−{PER_COIN_PENALTY:g})")
    wf = row.get("walkforward_passed")
    if wf is False:
        score -= WF_FAIL_PENALTY
        why.append("Walk-Forward nicht bestanden")
    elif wf is True:
        score += WF_PASS_BONUS
    why.append(f"OOS {oos_days:.0f} Tage (Vertrauen {shrink * 100:.0f} %)")
    return {**row, "eligible": True, "score": round(score, 2), "oos_days": round(oos_days, 1),
            "overfit_gap": round(gap, 1), "worst_window_f1": round(worst, 1), "why": why}


def margin_for(n_candidates: int) -> float:
    return round(BASE_MARGIN + MARGIN_PER_LOG_N * math.log(max(n_candidates, 1)), 2)


def beats_every_window(ch: Dict, inc: Dict) -> bool:
    """Herausforderer in allen gemeinsam vorhandenen Fenstern besser (rein).
    Fairer Vergleich: dieselben Teilfenster (gleiche Zeitstücke) paarweise."""
    if ch.get("fair") and inc.get("fair") and len(ch.get("windows") or []) == len(inc.get("windows") or []):
        pairs = list(zip(ch["windows"], inc["windows"])) + [(ch.get("holdout_f1"), inc.get("holdout_f1"))]
        pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
        return bool(pairs) and all(a > b for a, b in pairs)
    pairs = [(ch.get(k), inc.get(k)) for k in ("holdout_f1", "inner_f1")]
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs or any(a <= b for a, b in pairs):
        return False
    return not (inc.get("walkforward_passed") is True and ch.get("walkforward_passed") is False)


def choose(rows: List[Dict], incumbent: Optional[Dict] = None) -> Dict:
    """Champion-Entscheidung je Symbol×Band (rein). incumbent = {aid, scope} der
    aktuell wirksamen Erkennung (Klassen-Freigabe bzw. bisheriger Champion)."""
    ranked = sorted([evaluate(r) for r in rows], key=lambda r: (r["score"] is None, -(r["score"] or 0)))
    eligible = [r for r in ranked if r["eligible"]]
    margin = margin_for(len(eligible))
    inc = next((r for r in ranked if incumbent and r["aid"] == incumbent.get("aid")
                and r["scope"] == (incumbent.get("scope") or r["scope"])), None)
    out = {"candidates": ranked, "margin": margin, "incumbent": inc, "champion": None,
           "decision": "none", "reason": ""}
    if not eligible:
        out["reason"] = "keine Erkennung mit belastbarem Out-of-Sample"
        return out
    best = eligible[0]
    if inc and inc.get("eligible"):
        if best is inc or best["aid"] == inc["aid"] and best["scope"] == inc["scope"]:
            out.update(champion=inc, decision="keep", reason="aktuelle Erkennung ist die robusteste")
            return out
        if best["score"] >= inc["score"] + margin and beats_every_window(best, inc):
            out.update(champion=best, decision="switch",
                       reason=(f"„{best['name']}“ ({best['timeframe']}, {best['scope']}) schlägt die aktuelle "
                               f"Erkennung in allen Fenstern und um {best['score'] - inc['score']:.1f} ≥ "
                               f"{margin:g} Pkt. (Mehrfachtest-Marge, {len(eligible)} Kandidaten)"))
            return out
        out.update(champion=inc, decision="keep",
                   reason=(f"Bester Herausforderer {best['score']:.1f} vs. {inc['score']:.1f} – nicht in allen "
                           f"Fenstern besser bzw. unter der Marge {margin:g} → kein Wechsel (Overfit-Schutz)"))
        return out
    if best["score"] < MIN_SCORE:
        out["reason"] = f"bester Kandidat {best['score']:.1f} < Mindest-Score {MIN_SCORE:g}"
        return out
    out.update(champion=best, decision="switch" if incumbent else "recommend",
               reason=(f"„{best['name']}“ robusteste Erkennung ({len(eligible)} Kandidaten)"
                       + ("; aktuelle Erkennung ohne belastbares OOS" if incumbent else "")))
    return out


def apply_fair(rows: List[Dict], fair_res: Dict) -> List[Dict]:
    """Gespeicherte Holdout-Werte durch den fairen Gleich-Zeitraum-Vergleich
    ersetzen (rein). Kandidaten ohne fairen Messwert fallen raus – sonst würden
    wieder unterschiedliche Zeiträume verglichen. Trainings-F1 (Overfit-Lücke)
    und Walk-Forward-Status bleiben aus der Analyse."""
    from services import regime as rg
    by = {(r.get("aid"), r.get("scope")): r for r in (fair_res.get("rows") or [])}
    out = []
    for r in rows:
        f = by.get((r.get("aid"), r.get("scope")))
        if not f:
            continue
        bpd = rg.bars_per_day(f.get("base_timeframe") or r.get("timeframe") or "1h")
        out.append({**r, "fair": True, "holdout_f1": f.get("f1"), "inner_f1": None,
                    "windows": list(f.get("windows") or []), "holdout_bars": int(f.get("bars") or 0),
                    "bars_per_day": bpd, "holdout_kappa": None,
                    "own_holdout_f1": r.get("holdout_f1")})
    return out


def assignment_key(symbol: str, band: str) -> str:
    return f"{symbol}|{band}"


async def load_state(db) -> Dict:
    doc = await db.settings.find_one({"_id": DOC_ID}, {"_id": 0}) if db is not None else None
    doc = doc or {}
    return {"mode": doc.get("mode") if doc.get("mode") in MODES else "off",
            "assign": dict(doc.get("assign") or {}), "updated_at": doc.get("updated_at")}


async def save_state(db, state: Dict) -> None:
    await db.settings.update_one(
        {"_id": DOC_ID}, {"$set": {"mode": state.get("mode", "off"), "assign": state.get("assign") or {},
                                   "updated_at": datetime.now(timezone.utc).isoformat()}}, upsert=True)


async def compute(db, symbols: List[str]) -> Dict:
    """Champion-Entscheidungen je Symbol × Band aus allen v2-Analysen (DB-Schicht)."""
    from services import regime as rg
    from services import regime_release
    docs = await db.regime_analyses.find(
        {"settings.engine": "v2"}, {"_id": 0, "chart": 0, "chart_emas": 0}).sort(
        "created_at", -1).limit(60).to_list(60)
    from services import regime_fair_compare as fair
    state = await load_state(db)
    fair_res = await fair.load(db)
    out: Dict[str, Dict] = {}
    for sym in symbols:
        cls = setup_asset_class.asset_class_of(sym)
        for band in regime_release.BANDS:
            rows = []
            for d in docs:
                if regime_release.band_of_timeframe(d.get("timeframe")) != band:
                    continue
                rows += candidate_rows(d, sym, rg.bars_per_day(d.get("timeframe") or "1h"))
            if not rows:
                continue
            fr = fair_res.get(assignment_key(sym, band))
            fair_used = fair.is_fresh(fr)
            if fair_used:
                rows = apply_fair(rows, fr)
            cur = state["assign"].get(assignment_key(sym, band))
            if not cur:
                rel = regime_release.pick_release(
                    [d for d in docs if (d.get("release") or {}).get("stage") in ("shadow", "active")
                     and cls in ((d.get("release") or {}).get("asset_classes") or [])], band)
                if rel and regime_release.release_band(rel) == band:
                    cur = {"aid": rel["id"], "scope": (rel.get("release") or {}).get("scope") or "combined"}
            res = choose(rows, cur)
            res["candidates"] = res["candidates"][:8]
            out[assignment_key(sym, band)] = {
                "symbol": sym, "band": band, "asset_class": cls, "current": cur, **res,
                "fair": ({"used": True, "window": fr.get("window"), "computed_at": fr.get("computed_at"),
                          "base_timeframe": (fr.get("rows") or [{}])[0].get("base_timeframe")}
                         if fair_used else {"used": False, "error": (fr or {}).get("error"),
                                            "computed_at": (fr or {}).get("computed_at")})}
    return {"mode": state["mode"], "assign": state["assign"], "results": out,
            "rules": {"min_holdout_bars": MIN_HOLDOUT_BARS, "shrink_days": SHRINK_DAYS,
                      "gap_free_pp": GAP_FREE_PP, "per_coin_penalty": PER_COIN_PENALTY,
                      "base_margin": BASE_MARGIN, "min_score": MIN_SCORE}}


async def apply(db, results: Dict[str, Dict], keys: Optional[List[str]] = None) -> Dict:
    """Champions übernehmen (nur Entscheidungen 'switch'/'recommend')."""
    state = await load_state(db)
    applied = []
    for key, res in results.items():
        if keys is not None and key not in keys:
            continue
        ch = res.get("champion")
        if res.get("decision") not in ("switch", "recommend") or not ch:
            continue
        state["assign"][key] = {"aid": ch["aid"], "scope": ch["scope"], "score": ch["score"],
                                "timeframe": ch.get("timeframe"), "name": ch.get("name"),
                                "at": datetime.now(timezone.utc).isoformat()}
        applied.append(key)
    if applied:
        await save_state(db, state)
    return {"applied": applied, "assign": state["assign"]}
