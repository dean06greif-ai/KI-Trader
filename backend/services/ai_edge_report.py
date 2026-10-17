"""KI-Trader Edge-Bericht in R (deterministisch, kein LLM).

Beantwortet die Frage „Hat der KI-Trader überhaupt einen Vorteil – und wo
geht er verloren?“ in der Einheit, die über Gewinn/Verlust entscheidet:
R = PnL je riskiertem USDT (risk_usdt am Trade), netto nach Gebühren.

Befund Prod 10/2026 (1027 KI-Trades): netto −0,35 R, brutto −0,12 R, Gebühren
0,23 R je Trade; TP1-Quote 21 % bei nötigen ~37 %; LLM-Konfidenz 50/60/70 ohne
Unterschied; LLM-Einstiege schlechter als die Regel-Trigger. Diese Kennzahlen
waren nirgends sichtbar – die Diagnose zeigte nur Winrate/PnL in USDT.

Reine Funktionen oben (unit-testbar), dünne DB-Anbindung `build` unten.
"""
import logging
from datetime import datetime
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

FEE_R_CRIT = 0.12          # Gebühren > 0,12 R je Trade = Kostenproblem
MIN_BUCKET = 20            # Aussagen über Gruppen erst ab N Trades
PRIOR_TRADES = 10          # Shrinkage wie setup_lifecycle.EXP_PRIOR_TRADES
CONF_BUCKETS = ((0, 55, "< 55"), (55, 65, "55–64"), (65, 75, "65–74"), (75, 101, "≥ 75"))
HOLD_BUCKETS = ((0, 15, "< 15 min"), (15, 60, "15–60 min"), (60, 240, "1–4 h"), (240, 10 ** 9, "> 4 h"))


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def agg_r(rows: List[Dict]) -> Dict:
    """Kennzahlen in R (nur Trades mit riskiertem Kapital)."""
    rs = [r for r in rows if _f(r.get("risk_usdt")) > 0]
    n = len(rs)
    out = {"trades": len(rows), "with_risk": n}
    if not n:
        return out
    risk = sum(_f(r["risk_usdt"]) for r in rs)
    pnl = sum(_f(r.get("realized_pnl")) for r in rs)
    fees = sum(_f(r.get("fees_paid")) for r in rs)
    hit = [r for r in rs if r.get("tp1_hit")]
    miss = [r for r in rs if not r.get("tp1_hit")]

    def avg_r(xs):
        return sum(_f(x.get("realized_pnl")) / _f(x["risk_usdt"]) for x in xs) / len(xs) if xs else None
    win_r, loss_r = avg_r(hit), avg_r(miss)
    be = None
    if win_r is not None and loss_r is not None and win_r > 0 > loss_r:
        be = -loss_r / (win_r - loss_r)
    exp = pnl / risk
    out.update(exp_r=round(exp, 3), exp_r_shrunk=round(exp * n / (n + PRIOR_TRADES), 3),
               gross_r=round((pnl + fees) / risk, 3), fee_r=round(fees / risk, 3),
               tp1_rate=round(len(hit) / n * 100, 1),
               tp1_breakeven=None if be is None else round(be * 100, 1),
               avg_tp1_r=None if win_r is None else round(win_r, 2),
               avg_miss_r=None if loss_r is None else round(loss_r, 2),
               pnl=round(pnl, 2), fees=round(fees, 2))
    return out


def _bucket(v: float, buckets) -> Optional[str]:
    return next((lbl for lo, hi, lbl in buckets if lo <= v < hi), None)


def hold_minutes(t: Dict) -> Optional[float]:
    try:
        a = datetime.fromisoformat(str(t["opened_at"]).replace("Z", "+00:00"))
        b = datetime.fromisoformat(str(t["closed_at"]).replace("Z", "+00:00"))
        return (b - a).total_seconds() / 60.0
    except (KeyError, TypeError, ValueError):
        return None


def group_r(rows: List[Dict], keyf) -> Dict[str, Dict]:
    acc: Dict[str, List[Dict]] = {}
    for r in rows:
        k = keyf(r)
        if k is not None:
            acc.setdefault(str(k), []).append(r)
    return {k: agg_r(v) for k, v in sorted(acc.items())}


def conf_key(t: Dict) -> Optional[str]:
    c = t.get("ai_confidence")
    return None if c in (None, "") else _bucket(_f(c), CONF_BUCKETS)


def hold_key(t: Dict) -> Optional[str]:
    m = hold_minutes(t)
    return None if m is None else _bucket(m, HOLD_BUCKETS)


def _ok(st: Optional[Dict]) -> bool:
    return bool(st) and st.get("with_risk", 0) >= MIN_BUCKET and st.get("exp_r") is not None


def edge_findings(overall: Dict, by_setup: Dict, by_source: Dict, by_conf: Dict,
                  by_hold: Dict, cfg: Optional[Dict], analyst_model: Optional[str]) -> List[Dict]:
    """Deterministische Befunde (rein). frage='edge'."""
    f: List[Dict] = []
    cfg = cfg or {}

    def add(sev, text):
        f.append({"severity": sev, "frage": "edge", "text": text})

    if not overall.get("with_risk"):
        add("info", "Noch keine KI-Trades mit riskiertem Kapital im Zeitraum – kein Edge-Urteil möglich.")
        return f
    n, exp, gross, fee = overall["with_risk"], overall["exp_r"], overall["gross_r"], overall["fee_r"]
    add("kritisch" if exp < 0 else "info",
        f"Erwartungswert {exp:+.2f} R je Trade netto ({n} Trades), brutto {gross:+.2f} R, "
        f"Gebühren {fee:.2f} R je Trade.")
    if fee >= FEE_R_CRIT:
        mult = _f(cfg.get("fee_guard_mult")) or 2.5
        add("kritisch", f"Gebühren kosten {fee:.2f} R je Trade. Der Fee-Wächter (Faktor {mult:g}) lässt "
            f"bis zu {100 / mult:.0f} % des Risikos als Gebühren zu – Empfehlung: Faktor ≥ 6 (≤ 17 %), "
            "Maker-Einstiege bevorzugen, engste Scalps meiden.")
    if gross < 0:
        add("kritisch", "Auch OHNE Gebühren negativ: die Einstiege selbst haben im Schnitt keinen Vorteil – "
            "mehr Trades oder größere Positionen verschlimmern das Ergebnis.")
    tp1, be = overall.get("tp1_rate"), overall.get("tp1_breakeven")
    if tp1 is not None and be is not None and tp1 < be:
        add("warnung", f"TP1 wird in {tp1:.0f} % der Trades erreicht, nötig wären ~{be:.0f} % "
            f"(Ø {overall.get('avg_tp1_r'):+.2f} R mit TP1, {overall.get('avg_miss_r'):+.2f} R ohne).")
    good = sorted((k for k, st in by_setup.items() if _ok(st) and st["exp_r_shrunk"] >= 0.05),
                  key=lambda k: -by_setup[k]["exp_r_shrunk"])
    bad = [k for k, st in by_setup.items() if _ok(st) and st["exp_r_shrunk"] <= -0.15]
    if good:
        add("info", "Setups mit nachgewiesenem Vorteil: " + ", ".join(
            f"{k} ({by_setup[k]['exp_r']:+.2f} R, {by_setup[k]['with_risk']} Trades)" for k in good) + ".")
    if bad:
        add("warnung", f"Klar negative Setups (≤ −0,15 R nach Shrinkage, ≥ {MIN_BUCKET} Trades): "
            + ", ".join(sorted(bad)) + " – nur noch Paper/Datensammlung oder überarbeiten.")
    llm, rule = by_source.get("LLM"), by_source.get("Regel-Trigger")
    if _ok(llm) and _ok(rule) and llm["exp_r"] < rule["exp_r"] - 0.1:
        add("warnung", f"LLM-Einstiege {llm['exp_r']:+.2f} R vs. Regel-Trigger {rule['exp_r']:+.2f} R – "
            "das Sprachmodell verschlechtert die Auswahl.")
    confs = [st for lbl in ("< 55", "55–64", "65–74", "≥ 75") if _ok(st := by_conf.get(lbl))]
    if len(confs) >= 2 and confs[-1]["exp_r"] - confs[0]["exp_r"] < 0.1:
        add("warnung", "Die KI-Konfidenz trennt gute und schlechte Trades nicht (hohe Konfidenz ist nicht "
            "besser als niedrige) – Konfidenz-Schwellen und der Live-Bypass über hohe Konfidenz "
            "wirken damit zufällig.")
        if cfg.get("live_gate_bypass_enabled"):
            add("warnung", "Live-Bypass ist AN: nicht live-reife Setups gehen bei hoher Konfidenz trotzdem "
                "live – bei unkalibrierter Konfidenz Empfehlung: AUS.")
    short, rest = by_hold.get("< 15 min"), [by_hold.get(k) for k in ("15–60 min", "1–4 h")]
    rest = [r for r in rest if _ok(r)]
    if _ok(short) and rest and short["exp_r"] < min(r["exp_r"] for r in rest) - 0.15:
        add("info", f"Trades unter 15 min: {short['exp_r']:+.2f} R – Stops im Rauschen. "
            "ATR-Minimum des Fee-Wächters erhöhen oder Einstieg erst nach Bestätigungskerze.")
    if analyst_model and str(analyst_model).endswith(":free"):
        add("warnung", f"Die Trading-Entscheidungen trifft ein kostenloses Modell ({analyst_model}) – "
            "für Echtgeld ein stärkeres Hauptmodell wählen (z.B. Gemini Flash / DeepSeek), "
            "das freie Modell nur als Ausweich-Modell.")
    return f


def _ordered(groups: Dict[str, Dict], buckets) -> Dict[str, Dict]:
    return {lbl: groups[lbl] for _, _, lbl in buckets if lbl in groups}


def source_key(t: Dict, decisions: Dict[str, Dict]) -> str:
    d = decisions.get(str(t.get("decision_id") or ""))
    if not d:
        return "unbekannt"
    return "Regel-Trigger" if d.get("source") == "setup_trigger" else "LLM"


async def build(db, config: Dict, cutoff: str) -> Dict:
    """Edge-Bericht für alle geschlossenen KI-Trades seit `cutoff`."""
    rows = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "status": "closed", "opened_at": {"$gte": cutoff}},
        {"_id": 0, "setup": 1, "mode": 1, "data_collection": 1, "realized_pnl": 1, "fees_paid": 1,
         "risk_usdt": 1, "tp1_hit": 1, "ai_confidence": 1, "opened_at": 1, "closed_at": 1,
         "decision_id": 1}).to_list(8000)
    ids = [r["decision_id"] for r in rows if r.get("decision_id")]
    decisions: Dict[str, Dict] = {}
    if ids:
        async for d in db.ai_decisions.find({"id": {"$in": ids}}, {"_id": 0, "id": 1, "source": 1}):
            decisions[str(d["id"])] = d
    roles = await db.settings.find_one({"_id": "ai_roles_config"}, {"analyst.model": 1}) or {}
    analyst_model = (roles.get("analyst") or {}).get("model")
    live = [r for r in rows if r.get("mode") == "live" and not r.get("data_collection")]
    overall = agg_r(rows)
    by_setup = group_r(rows, lambda t: t.get("setup") or None)
    by_source = group_r(rows, lambda t: source_key(t, decisions))
    by_conf = _ordered(group_r(rows, conf_key), CONF_BUCKETS)
    by_hold = _ordered(group_r(rows, hold_key), HOLD_BUCKETS)
    return {"overall": overall, "live": agg_r(live), "by_setup": by_setup, "by_source": by_source,
            "by_confidence": by_conf, "by_hold": by_hold, "analyst_model": analyst_model,
            "findings": edge_findings(overall, by_setup, by_source, by_conf, by_hold, config, analyst_model)}
