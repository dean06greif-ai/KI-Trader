"""ML-Befunde (ML-Labor: Modell-Kennzahlen, LLM-Erklärung, abgeleitete Regeln).

Bis 06.09.2026 landeten diese Befunde im KI-Gedächtnis (`ai_knowledge`, kind
`ml_finding`, 2 465 Einträge) und wurden dem Analysten als „Team-Wissen“ in den
Prompt gemischt – ohne Nutzen für Einzelentscheidungen, aber mit Prompt-Rauschen.
Seit dem liegen sie ausschließlich hier (`ml_findings`) und erscheinen nur im
ML-Labor-/ML-Gate-Report (GET /api/ai/ml/findings)."""
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

COLL = "ml_findings"
KINDS = ("model", "rule", "explanation")
KEEP = 500


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_finding(kind: str, title: str, content: str, source: str = "",
                 meta: Optional[Dict] = None) -> Dict:
    """Befund-Dokument bauen (rein, testbar)."""
    kind = kind if kind in KINDS else "model"
    return {"id": f"mlf_{uuid.uuid4().hex[:10]}", "kind": kind, "title": str(title or "")[:160],
            "content": str(content or "")[:2000], "source": str(source or "")[:80],
            "meta": dict(meta or {}), "ts": _now_iso()}


async def add(db, kind: str, title: str, content: str, source: str = "",
              meta: Optional[Dict] = None) -> Dict:
    doc = make_finding(kind, title, content, source, meta)
    await db[COLL].insert_one(dict(doc))
    await _prune(db)
    return doc


async def add_rules(db, rules: List[Dict], source: str = "") -> int:
    n = 0
    for r in rules or []:
        if isinstance(r, dict) and (r.get("title") or r.get("detail")):
            await db[COLL].insert_one(dict(make_finding("rule", r.get("title") or "Regel",
                                                        r.get("detail") or "", source)))
            n += 1
    if n:
        await _prune(db)
    return n


async def _prune(db) -> None:
    try:
        old = await (db[COLL].find({}, {"id": 1}).sort("ts", -1).skip(KEEP).to_list(2000))
        if old:
            await db[COLL].delete_many({"id": {"$in": [o["id"] for o in old]}})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"ML-Befunde Kappung: {e}")


async def recent(db, limit: int = 20, kind: Optional[str] = None) -> List[Dict]:
    q = {"kind": kind} if kind else {}
    return await (db[COLL].find(q, {"_id": 0}).sort("ts", -1)
                  .to_list(max(1, min(int(limit), 200))))


async def migrate_from_memory(db) -> int:
    """Einmalig: bestehende `ml_finding`-Einträge aus ai_knowledge hierher verschieben."""
    rows = await db.ai_knowledge.find({"kind": "ml_finding"}, {"_id": 0}).to_list(20000)
    if not rows:
        return 0
    docs = []
    for r in rows:
        tags = r.get("tags") or []
        kind = "rule" if "regel" in tags else ("explanation" if "erklärung" in tags else "model")
        d = make_finding(kind, r.get("title"), r.get("content"), r.get("source"), r.get("meta"))
        d["ts"] = r.get("ts") or d["ts"]
        docs.append(d)
    await db[COLL].insert_many(docs)
    await db.ai_knowledge.delete_many({"kind": "ml_finding"})
    await _prune(db)
    return len(docs)
