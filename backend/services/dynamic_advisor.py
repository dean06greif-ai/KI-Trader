"""Empfehlungen für die Dynamik-Werkbank (rein & testbar, Laden in `load_*`).

  * analysis_suitability – eignet sich eine Regime-Erkennung (Analyse) für eine
    dynamische Strategie? Note der Erkennung (services/regime_quality), Anzahl
    Regime, Holdout (Out-of-Sample-Test möglich?), Ø Phasendauer, Stichprobe je
    Regime, bisheriger Walk-Forward, Ausreißer-Assets der Erkennung.
  * timeframe_advice – sinnvoller Strategie-Timeframe zur Erkennung + Regel-
    Filter auf höheren Timeframes (Multi-TF). Die Erkennung selbst bleibt auf
    ihrem Timeframe: das Regime-Modell ist darauf kalibriert.
  * edge_history / apply_edge_history – Coin-Vorschlag um bisherige Ergebnisse
    erweitern: Assets ohne Edge in früheren Regime-Suchen + Erkennungs-
    Ausreißer werden als „eher weglassen“ markiert.
"""
from typing import Dict, List, Optional

from services.timeframes import RULE_TIMEFRAMES, TIMEFRAMES

STRATEGY_TFS = ("5m", "15m", "30m", "1h", "2h", "4h")   # Daytrading: < 5m frisst Gebühren
GRADE_POINTS = {"sehr gut": 5, "gut": 0, "mittel": -15, "schwach": -40}
SHORT_PHASE_DAYS, LONG_PHASE_DAYS = 1.0, 30.0
MIN_REGIME_PHASES = 3
VERDICTS = ((75, "empfohlen"), (55, "geeignet"), (35, "bedingt"))
NO_EDGE_RUNS = 2   # so oft ohne Edge (und nie mit) -> Coin eher weglassen


def higher_tf_options(tf: Optional[str]) -> List[str]:
    """Zwei höhere Regel-Timeframes als Filter (z.B. 15m -> 1h, 4h)."""
    if tf not in RULE_TIMEFRAMES:
        return []
    i = RULE_TIMEFRAMES.index(tf)
    return [RULE_TIMEFRAMES[j] for j in (i + 2, i + 4) if j < len(RULE_TIMEFRAMES)]


def recommended_strategy_tf(analysis_tf: Optional[str]) -> Optional[str]:
    """Strategie etwa 4× feiner als die Erkennung, nie feiner als 5m, nie gröber
    als die Erkennung selbst."""
    mins = TIMEFRAMES.get(str(analysis_tf or ""), 0)
    if not mins:
        return None
    target = max(mins / 4.0, 5)
    ok = [t for t in STRATEGY_TFS if TIMEFRAMES[t] <= mins] or [STRATEGY_TFS[0]]
    return min(ok, key=lambda t: abs(TIMEFRAMES[t] - target))


def timeframe_advice(analysis_tf: Optional[str], strategy_tf: Optional[str] = None) -> Dict:
    rec = recommended_strategy_tf(analysis_tf)
    cur = strategy_tf or analysis_tf
    notes = []
    if rec and cur and cur != rec:
        cm, rm = TIMEFRAMES.get(cur, 0), TIMEFRAMES.get(rec, 0)
        if cm and rm and cm > rm:
            notes.append(f"Strategie auf {cur} handelt je Phase nur wenige Einstiege – {rec} liefert "
                         "mehr Trades für eine belastbare Statistik")
        elif cm and rm and cm < rm:
            notes.append(f"Strategie auf {cur} ist sehr fein (Gebühren/Rauschen) – {rec} ist meist robuster")
    if TIMEFRAMES.get(str(cur or ""), 0) and TIMEFRAMES.get(str(analysis_tf or ""), 0) \
            and TIMEFRAMES[cur] > TIMEFRAMES[analysis_tf]:
        notes.append("Strategie-Timeframe gröber als die Regime-Erkennung – Wechsel werden verpasst")
    return {"analysis_tf": analysis_tf, "strategy_tf": cur, "recommended": rec,
            "filter_tfs": higher_tf_options(rec or cur),
            "notes": notes,
            "why": "Die Regime-Erkennung bleibt auf ihrem Timeframe (das Modell ist darauf kalibriert). "
                   "Getestet werden der Strategie-Timeframe und optionale Regel-Filter auf höheren Timeframes."}


def _phases_per_regime(per_symbol: Dict[str, Dict]) -> Dict[int, int]:
    from services.asset_suggest import regime_presence
    out: Dict[int, int] = {}
    for regs in regime_presence(per_symbol or {}).values():
        for rid, st in regs.items():
            out[int(rid)] = out.get(int(rid), 0) + int(st.get("segments") or 0)
    return out


def analysis_suitability(doc: Dict, quality: Optional[Dict] = None,
                         walkforward_passed: Optional[bool] = None) -> Dict:
    """Bewertung 0–100 + Urteil + Gründe (rein). `quality` = regime_quality.summarize_scope."""
    comb = doc.get("combined") or {}
    regimes = (comb.get("model") or {}).get("regimes") or doc.get("regimes") or []
    reasons: List[Dict] = []
    blockers = 0
    score = 100

    def add(level, text, pts=0):
        nonlocal score
        score += pts
        reasons.append({"level": level, "text": text})

    if len(regimes) < 2:
        blockers += 1
        add("bad", f"Nur {len(regimes)} Regime – eine dynamische Strategie kann nicht umschalten", -60)
    else:
        add("ok", f"{len(regimes)} Regime erkannt")
    train_pct = float((doc.get("settings") or {}).get("train_pct") or 100)
    if train_pct >= 100:
        add("warn", "Kein Holdout – der finale Out-of-Sample-Test entfällt, Overfitting ist nicht prüfbar", -25)
    else:
        add("ok", f"Holdout {100 - train_pct:.0f}% – Out-of-Sample-Test möglich")
    overall = (quality or {}).get("overall") or {}
    grade = overall.get("grade")
    if grade in GRADE_POINTS:
        level = "ok" if GRADE_POINTS[grade] >= 0 else ("warn" if grade == "mittel" else "bad")
        add(level, f"Erkennungs-Note: {grade}", GRADE_POINTS[grade])
    else:
        add("warn", "Erkennungs-Note unbekannt (ältere Analyse)", -10)
    days = overall.get("avg_segment_days")
    if days is not None:
        if days < SHORT_PHASE_DAYS:
            add("bad", f"Phasen sehr kurz (Ø {days:.1f} Tage) – häufige Wechsel, Strategien haben kaum Zeit für Trades", -25)
        elif days > LONG_PHASE_DAYS:
            add("warn", f"Phasen sehr lang (Ø {days:.0f} Tage) – wenige Wechsel, kaum Vorteil ggü. einer Einzel-Strategie", -15)
        else:
            add("ok", f"Ø Phasendauer {days:.1f} Tage")
    phases = _phases_per_regime(comb.get("per_symbol") or {})
    thin = [r for r in regimes if phases and phases.get(int(r.get("id")), 0) < MIN_REGIME_PHASES]
    for r in thin[:3]:
        add("warn", f"Regime „{r.get('label')}“ kommt nur {phases.get(int(r.get('id')), 0)}× vor – "
                    "zu kleine Stichprobe, Ergebnis schnell überangepasst", -10)
    if walkforward_passed is False:
        add("warn", "Bisheriger Walk-Forward dieser Analyse nicht bestanden", -10)
    elif walkforward_passed:
        add("ok", "Bisheriger Walk-Forward bestanden")
    outl = (quality or {}).get("outlier_symbols") or {}
    if outl.get("symbols"):
        add("warn", "Erkennung trifft schlecht auf: "
                    + ", ".join(s.replace("USDT", "") for s in outl["symbols"]) + " – für die Strategie eher weglassen")
    score = max(0, min(100, score))
    verdict = "ungeeignet" if blockers else next((v for lim, v in VERDICTS if score >= lim), "ungeeignet")
    if grade == "schwach" and verdict in ("empfohlen", "geeignet"):
        verdict = "bedingt"   # schwache Erkennung: Phasen-Wechsel im Live-Handel unzuverlässig
    return {"id": doc.get("id"), "name": doc.get("name"), "score": score, "verdict": verdict,
            "suitable": verdict != "ungeeignet", "grade": grade, "avg_phase_days": days,
            "n_regimes": len(regimes), "reasons": reasons,
            "outlier_symbols": outl.get("symbols") or [],
            "timeframe_advice": timeframe_advice(doc.get("timeframe"))}


def rank_analyses(rows: List[Dict]) -> List[Dict]:
    """Beste zuerst; die Top 3 geeigneten bekommen recommended=True (rein)."""
    out = sorted(rows, key=lambda r: -r["score"])
    for i, r in enumerate(out):
        r["recommended"] = r["verdict"] in ("empfohlen", "geeignet") and i < 3
    return out


# ---------------- Coins: bisherige Edge-Ergebnisse ----------------
def edge_history(results: List[Dict]) -> Dict[str, Dict[str, Dict]]:
    """{regime: {symbol: {edge, no_edge}}} aus gespeicherten regime_opt-Ergebnissen."""
    out: Dict[str, Dict[str, Dict]] = {}
    for res in results or []:
        top = (res.get("top5") or [None])[0] or {}
        rep = top.get("coin_report") or {}
        rid = res.get("regime_id")
        if rid is None or not rep:
            continue
        reg = out.setdefault(str(rid), {})
        for s in rep.get("edge") or []:
            reg.setdefault(s, {"edge": 0, "no_edge": 0})["edge"] += 1
        for s in rep.get("no_edge") or []:
            reg.setdefault(s, {"edge": 0, "no_edge": 0})["no_edge"] += 1
    return out


def apply_edge_history(res: Dict, hist: Dict[str, Dict[str, Dict]],
                       detection_outliers: Optional[List[str]] = None) -> Dict:
    """Coin-Vorschlag erweitern (rein): Gründe + 'avoid'-Liste; Assets, die wiederholt
    keinen Edge hatten (und nie einen), fallen aus der Auswahl."""
    det = set(detection_outliers or [])
    avoid: Dict[str, List[str]] = {}
    for pr in res.get("per_regime") or []:
        h = hist.get(str(pr.get("regime_id"))) or {}
        for a in pr.get("assets") or []:
            st = h.get(a["symbol"]) or {}
            a["edge_runs"], a["no_edge_runs"] = st.get("edge", 0), st.get("no_edge", 0)
            if a["no_edge_runs"]:
                a["reasons"] = list(a.get("reasons") or []) + [
                    f"frühere Suchen: {a['no_edge_runs']}× kein Edge, {a['edge_runs']}× Edge"]
            if a["no_edge_runs"] >= NO_EDGE_RUNS and not a["edge_runs"]:
                a["pick"] = False
                avoid.setdefault(a["symbol"], []).append(
                    f"„{pr.get('label')}“: {a['no_edge_runs']}× ohne Edge")
        pr["picks"] = [a["symbol"] for a in pr.get("assets") or [] if a.get("pick")]
    for s in det:
        avoid.setdefault(s, []).append("Regime-Erkennung trifft hier klar schlechter (Ausreißer)")
    rec = [s for s in res.get("recommended") or [] if s not in avoid]
    if rec:   # nie alles streichen – dann bleibt der bisherige Vorschlag
        res["recommended"] = rec
    res["avoid"] = [{"symbol": s, "reasons": r} for s, r in sorted(avoid.items())]
    return res


# ---------------- Laden (DB) ----------------
async def load_quality(doc: Dict) -> Optional[Dict]:
    from services import regime_quality
    try:
        return regime_quality.summarize_scope((doc.get("combined") or {}).get("per_symbol") or {})
    except Exception:  # noqa: BLE001 – Empfehlung darf nie die Werkbank blockieren
        return None


async def load_edge_history(db, aid: str, limit: int = 80) -> Dict[str, Dict[str, Dict]]:
    rows = await db.regime_lab_runs.find(
        {"result.kind": "regime_opt", "result.analysis_id": aid},
        {"_id": 0, "result.regime_id": 1, "result.top5.coin_report": 1}) \
        .sort("created_at", -1).to_list(limit)
    return edge_history([r.get("result") or {} for r in rows])
