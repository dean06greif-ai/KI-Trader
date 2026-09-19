"""Lektions-Bilanz (PLAN_LEKTIONS_BILANZ, Baustein C) – quantitative Wirkung je Lektion.

Je Lektion: Trades MIT ihr (attribuiert über `applied_lessons`), Vergleichsgruppe
OHNE sie (gleiche Assetklasse, gleicher Zeitraum, ohne Sammel-Trades), verhinderte
Trades aus der HOLD-Gegenprobe (`ai_lesson_cf`) und daraus ein Urteil + Vorschlag.
Das Urteil ist reine Information für Trader, Lernlauf und Neubewertung – die
bestehenden Gates (`ai_validation`) bleiben die einzige Instanz, die Lektionen ändert.

Reine Funktionen oben (unit-testbar), dünner DB-Wrapper mit Cache unten.
"""
import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional

from services import setup_asset_class

logger = logging.getLogger(__name__)

# Urteilsregeln (über ai_validation-Settings überschreibbar: min_removal_results)
MIN_SAMPLE_DEFAULT = 12
EDGE_MIN_R = 0.5
HINDER_MAX_R = -1.0
HINDER_MIN_PREVENTED = 6
SOFT_MIN_WITH = 8
SOFT_WR_GAP_PP = 10.0
MIN_GROUP = 5            # unter n<5 keine Aussage je Gruppe ("—")
CACHE_TTL_S = 600
MAX_TRADES = 3000

VERDICT_LABELS = {"edge": "EDGE", "neutral": "neutral", "hinderlich": "hinderlich",
                  "zu_weich": "zu weich", "zu_wenig_daten": "sammelt"}


def _f(v, default: float = 0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def trade_r(t: Dict) -> Optional[float]:
    """R-Vielfaches eines geschlossenen Trades (wie ai_rewards: PnL / risk_usdt)."""
    risk = _f(t.get("risk_usdt"))
    if risk <= 0:
        return None
    return round(_f(t.get("realized_pnl")) / risk, 3)


def _group_stats(trades: List[Dict]) -> Dict:
    n = len(trades)
    wins = sum(1 for t in trades if t.get("result") == "win")
    rs = [r for r in (trade_r(t) for t in trades) if r is not None]
    return {"n": n,
            "wr": round(wins / n * 100, 1) if n >= MIN_GROUP else None,
            "avg_r": round(sum(rs) / len(rs), 3) if len(rs) >= MIN_GROUP else None,
            "sum_r": round(sum(rs), 3) if rs else 0.0}


def _prevented(cf_agg: Optional[Dict]) -> Dict:
    a = cf_agg or {}
    return {"n": int(a.get("n", 0)), "would_win": int(a.get("would_win", 0)),
            "would_loss": int(a.get("would_loss", 0)), "open": int(a.get("open", 0)),
            "avoided_loss_r": round(_f(a.get("avoided_loss_r")), 3),
            "missed_gain_r": round(_f(a.get("missed_gain_r")), 3),
            "net_r": round(_f(a.get("net_r")), 3)}


def verdict(with_: Dict, without: Dict, prevented: Dict, net_contribution: Optional[float],
            min_sample: int) -> Dict:
    """Urteil + Vorschlag nach den Regeln aus PLAN_LEKTIONS_BILANZ §5 C1 (rein)."""
    sample = int(with_["n"]) + int(prevented["n"])
    if sample < min_sample:
        return {"verdict": "zu_wenig_daten", "suggestion": "weiter_sammeln",
                "reason": f"erst {sample}/{min_sample} Beobachtungen (Trades + Gegenproben)"}
    if prevented["n"] >= HINDER_MIN_PREVENTED and prevented["net_r"] <= HINDER_MAX_R:
        return {"verdict": "hinderlich", "suggestion": "lockern",
                "reason": (f"verhinderte überwiegend Gewinner: {prevented['would_win']} von "
                           f"{prevented['n']} blockierten Trades wären Gewinner gewesen "
                           f"(netto {prevented['net_r']:+.2f} R)")}
    if (with_["n"] >= SOFT_MIN_WITH and with_["wr"] is not None and without["wr"] is not None
            and with_["wr"] < without["wr"] - SOFT_WR_GAP_PP):
        return {"verdict": "zu_weich", "suggestion": "verschaerfen",
                "reason": (f"angewendet, aber Trades verlieren trotzdem: WR {with_['wr']:.0f} % "
                           f"vs. {without['wr']:.0f} % ohne")}
    if (net_contribution is not None and net_contribution >= EDGE_MIN_R
            and prevented["missed_gain_r"] <= prevented["avoided_loss_r"]):
        return {"verdict": "edge", "suggestion": "behalten",
                "reason": f"messbarer Beitrag {net_contribution:+.2f} R netto"}
    return {"verdict": "neutral", "suggestion": "behalten",
            "reason": "kein signifikanter Unterschied zur Vergleichsgruppe"}


def impact_rows(lessons: List[Dict], trades: List[Dict], cf_agg: Dict[str, Dict],
                settings: Optional[Dict] = None) -> List[Dict]:
    """Bilanz je Lektion. `trades`: geschlossene ai_trader-Trades (Projektion:
    id, symbol, result, realized_pnl, risk_usdt, closed_at, applied_lessons,
    data_collection). `cf_agg`: Ergebnis von lesson_counterfactual.aggregate."""
    min_sample = int((settings or {}).get("min_removal_results") or MIN_SAMPLE_DEFAULT)
    normal = [t for t in trades if not t.get("data_collection")]
    collection = [t for t in trades if t.get("data_collection")]
    rows = []
    for l in lessons:
        lid = str(l.get("id") or "")
        if not lid:
            continue
        mine = [t for t in normal if lid in (t.get("applied_lessons") or [])]
        mine_coll = [t for t in collection if lid in (t.get("applied_lessons") or [])]
        classes = {setup_asset_class.asset_class_of(t.get("symbol")) for t in mine}
        tss = sorted(str(t.get("closed_at") or "") for t in mine)
        first_ts, last_ts = (tss[0], tss[-1]) if tss else ("", "")
        control = [t for t in normal
                   if lid not in (t.get("applied_lessons") or [])
                   and setup_asset_class.asset_class_of(t.get("symbol")) in classes
                   and first_ts <= str(t.get("closed_at") or "") <= last_ts] if mine else []
        with_ = _group_stats(mine)
        without = _group_stats(control)
        prevented = _prevented(cf_agg.get(lid))
        net_contribution = None
        if with_["n"] >= MIN_GROUP and without["avg_r"] is not None:
            net_contribution = round(with_["sum_r"] - with_["n"] * without["avg_r"] + prevented["net_r"], 3)
        elif with_["n"] == 0 and prevented["n"] > 0:
            net_contribution = prevented["net_r"]
        v = verdict(with_, without, prevented, net_contribution, min_sample)
        rows.append({"id": lid, "title": l.get("title"), "locked": bool(l.get("locked")),
                     "weight": l.get("weight"), "status": l.get("status"),
                     "with": with_, "without": without, "prevented": prevented,
                     "collection_n": len(mine_coll),
                     "net_contribution_r": net_contribution,
                     "sample": with_["n"] + prevented["n"], **v})
    rows.sort(key=lambda r: (-(r["sample"]), str(r["title"])))
    return rows


def totals(trades: List[Dict], cf_reviews: List[Dict], cf_pending: int) -> Dict:
    attributed = sum(1 for t in trades if t.get("applied_lessons"))
    return {"trades": len(trades), "attributed_trades": attributed,
            "unattributed_trades": len(trades) - attributed,
            "attribution_pct": round(attributed / len(trades) * 100, 1) if trades else None,
            "cf_done": sum(1 for r in cf_reviews if r.get("status") == "done"),
            "cf_no_data": sum(1 for r in cf_reviews if r.get("status") != "done"),
            "cf_pending": int(cf_pending)}


def _fmt_r(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.1f} R".replace(".", ",")


def prompt_block(rows: List[Dict], max_lines: int = 8) -> str:
    """Prompt-Text für Lernlauf/Neubewertung (Flag `lesson_impact_in_prompt`).
    Nur Lektionen mit ausreichender Stichprobe; max. `max_lines` Zeilen."""
    usable = [r for r in rows if r.get("verdict") != "zu_wenig_daten"][:max_lines]
    if not usable:
        return ""
    lines = ["=== LEKTIONS-BILANZ (gemessen, netto – Schätzung, kein Automatismus) ==="]
    for r in usable:
        w, wo, p = r["with"], r["without"], r["prevented"]
        wr = f"WR {w['wr']:.0f} %" if w["wr"] is not None else "WR —"
        wr += f" vs. {wo['wr']:.0f} % ohne" if wo["wr"] is not None else ""
        lines.append(f"„{r['title']}“ (id {r['id']}): {w['n']} Trades mit / {wr} · "
                     f"{p['n']} verhindert ({p['would_loss']} wären Verlierer) · "
                     f"Beitrag {_fmt_r(r.get('net_contribution_r'))} → "
                     f"{VERDICT_LABELS.get(r['verdict'], r['verdict']).upper()} ({r['suggestion']})")
    lines.append("Vorschläge nur umsetzen, wenn die Datenlage (Gate) es erlaubt; "
                 "LOCKED-Lektionen nie ändern.")
    return "\n".join(lines)


def evidence_for(rows: List[Dict], lesson_id: str) -> str:
    """Einzeiler als Evidenz für die Neubewertung einer Lektion ('' ohne Daten)."""
    r = next((x for x in rows if x.get("id") == lesson_id), None)
    if not r or r.get("verdict") == "zu_wenig_daten":
        return ""
    return (f"Bilanz: {r['with']['n']} Trades mit, {r['prevented']['n']} verhindert, "
            f"Beitrag {_fmt_r(r.get('net_contribution_r'))} → {r['verdict']} ({r['reason']})")


# --------------------------------------------------------------------------
# DB-Anbindung (dünn, Stale-while-revalidate-Cache)
# --------------------------------------------------------------------------
class LessonImpactService:
    def __init__(self):
        self.db = None
        self._cache: Dict[int, Dict] = {}
        self._cache_ts: Dict[int, float] = {}

    def setup(self, db):
        self.db = db

    def invalidate(self):
        self._cache_ts.clear()

    async def _trades(self, days: int) -> List[Dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        return await self.db.auto_trades.find(
            {"strategy_id": "ai_trader", "status": "closed", "closed_at": {"$gte": cutoff}},
            {"_id": 0, "id": 1, "symbol": 1, "result": 1, "realized_pnl": 1, "risk_usdt": 1,
             "closed_at": 1, "applied_lessons": 1, "data_collection": 1}
        ).sort("closed_at", -1).to_list(MAX_TRADES)

    async def _compute(self, days: int) -> Dict:
        from services.ai_lessons import lesson_store
        from services.ai_validation import validation_gate
        from services.lesson_counterfactual import counterfactual, aggregate
        lessons = await lesson_store.all()
        trades = await self._trades(days)
        cf = await counterfactual.reviews(days)
        rows = impact_rows(lessons, trades, aggregate(cf), validation_gate.settings)
        recent = [{"decision_id": r.get("decision_id"), "symbol": r.get("symbol"), "ts": r.get("ts"),
                   "action": r.get("action"), "r": r.get("r"), "exit_reason": r.get("exit_reason"),
                   "blocked_by": r.get("blocked_by") or []}
                  for r in cf if r.get("status") == "done"][:60]
        return {"days": days, "rows": rows,
                "totals": totals(trades, cf, await counterfactual.pending_count()),
                "recent_prevented": recent, "generated_at": datetime.now(timezone.utc).isoformat(),
                "rules": {"min_sample": int(validation_gate.settings.get("min_removal_results")
                                            or MIN_SAMPLE_DEFAULT),
                          "edge_min_r": EDGE_MIN_R, "hinder_max_r": HINDER_MAX_R,
                          "soft_wr_gap_pp": SOFT_WR_GAP_PP, "min_group": MIN_GROUP}}

    async def report(self, days: int = 90) -> Dict:
        if self.db is None:
            return {"days": days, "rows": [], "totals": {}, "recent_prevented": []}
        days = max(7, min(365, int(days)))
        now = time.time()
        cached = self._cache.get(days)
        if cached is not None and now - self._cache_ts.get(days, 0) < CACHE_TTL_S:
            return cached
        try:
            data = await self._compute(days)
            self._cache[days] = data
            self._cache_ts[days] = now
            return data
        except Exception as e:  # noqa: BLE001
            logger.warning(f"Lektions-Bilanz fehlgeschlagen: {e}")
            if cached is not None:
                return cached
            return {"days": days, "rows": [], "totals": {}, "recent_prevented": [],
                    "error": str(e)[:200]}

    async def prompt_block(self, config: Dict, days: int = 90) -> str:
        if not (config or {}).get("lesson_impact_in_prompt"):
            return ""
        return prompt_block((await self.report(days)).get("rows") or [])

    async def rows(self, config: Dict, days: int = 90) -> List[Dict]:
        if not (config or {}).get("lesson_impact_in_prompt"):
            return []
        return (await self.report(days)).get("rows") or []


lesson_impact = LessonImpactService()
