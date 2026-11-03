"""Setup-Nutzung des KI-Traders: Warum wird ein Setup (nicht) gehandelt?

Trichter je Setup über die letzten N Tage (rein aggregierend, nur lesend):
  Entscheidungen (LONG/SHORT) -> Signal ausgelöst -> Trades je Welt
  (Echtgeld / Paper / Sammlung) + häufigste Blockgründe (`blocked_by`,
  `live_gate`, Konfidenz unter Sammel-Schwelle).
Setups aus der Bibliothek/KI-eigene Setups ohne jede Entscheidung werden als
"nie gewählt" ausgewiesen – typischer Hinweis auf fehlende Prompt-Daten.
"""
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Dict, List

_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def reason_key(text: str) -> str:
    """Blockgrund ohne Zahlen/Symbol-Listen gruppieren (rein)."""
    s = _NUM.sub("#", str(text or ""))
    s = re.sub(r"\([^)]*\)", "(…)", s)
    return s.split(" – ")[0].strip()[:90]


def summarize(decisions: List[Dict], trades: List[Dict], library: Dict[str, str],
              min_conf: float = 60) -> List[Dict]:
    """Trichter je Setup aus Entscheidungs- und Trade-Zeilen (rein & testbar)."""
    rows: Dict[str, Dict] = defaultdict(lambda: {"decisions": 0, "signaled": 0, "low_conf": 0,
                                                 "reasons": Counter()})
    for d in decisions:
        sid = d.get("setup") or "ohne Setup"
        r = rows[sid]
        r["decisions"] += 1
        if d.get("signaled"):
            r["signaled"] += 1
            continue
        if d.get("blocked_by"):
            r["reasons"][reason_key(d["blocked_by"])] += 1
        elif float(d.get("confidence") or 0) < min_conf:
            r["low_conf"] += 1
        else:
            r["reasons"]["Cooldown/Handelsfenster (ohne Protokoll)"] += 1
    worlds: Dict[str, Counter] = defaultdict(Counter)
    for t in trades:
        w = "collection" if t.get("data_collection") else ("real" if t.get("mode") == "live" else "paper")
        worlds[t.get("setup") or "ohne Setup"][w] += 1
    out = []
    for sid in sorted(set(library) | set(rows) | set(worlds)):
        r = rows.get(sid) or {"decisions": 0, "signaled": 0, "low_conf": 0, "reasons": Counter()}
        w = worlds.get(sid) or Counter()
        out.append({
            "setup": sid, "custom": str(library.get(sid, "")).startswith("[KI-Setup]"),
            "decisions": r["decisions"], "signaled": r["signaled"], "low_conf": r["low_conf"],
            "trades_real": w["real"], "trades_paper": w["paper"], "trades_collection": w["collection"],
            "top_reasons": [{"reason": k, "count": v} for k, v in r["reasons"].most_common(3)],
            "never_chosen": r["decisions"] == 0,
        })
    out.sort(key=lambda x: (x["never_chosen"], -x["decisions"]))
    return out


async def usage(db, days: int = 14) -> Dict:
    from services import ai_playbook
    days = max(1, min(90, int(days or 14)))
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    decisions = await db.ai_decisions.find(
        {"ts": {"$gte": since}, "action": {"$in": ["LONG", "SHORT"]}},
        {"_id": 0, "setup": 1, "signaled": 1, "blocked_by": 1, "confidence": 1}).to_list(20000)
    trades = await db.auto_trades.find(
        {"strategy_id": "ai_trader", "opened_at": {"$gte": since}},
        {"_id": 0, "setup": 1, "mode": 1, "data_collection": 1}).to_list(20000)
    cfg = await db.settings.find_one({"_id": "ai_trader_config"}, {"collection_min_confidence": 1}) or {}
    rows = summarize(decisions, trades, ai_playbook.all_setups(),
                     float(cfg.get("collection_min_confidence") or 60))
    return {"days": days, "rows": rows}
