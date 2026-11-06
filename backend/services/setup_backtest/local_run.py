"""KI-Trader-Lab auf dem lokalen Worker (10/2026).

Der Worker rechnet runner.run_job unverändert gegen eine In-Memory-Datenbank
(MemDB), die mit einem Schnappschuss des Servers gefüllt ist (Lab-Zustand,
Edges der gewählten Klassen, Playbook-Bibliothek). Alle Schreibzugriffe werden
protokolliert und vom Server nach dem Upload in derselben Reihenfolge auf
Mongo angewendet (replay) – die Ergebnis-Logik bleibt eine Quelle der Wahrheit.
KI-Revision (LLM) braucht die Cloud-Keys -> lokal nur ohne KI-Modi.
"""
import copy
import itertools
from typing import Any, Dict, List, Optional

from services.setup_backtest import edges

MIN_WORKER_VERSION = (1, 21)
SNAPSHOT_COLLECTIONS = (edges.COLLECTION,)
_ids = itertools.count(1)


def _match_val(v: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and any(str(k).startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$ne" and v == arg:
                return False
            if op == "$in" and v not in arg:
                return False
            if op == "$exists" and (v is not None) != bool(arg):
                return False
        return True
    return v == cond


def matches(doc: Dict, q: Optional[Dict]) -> bool:
    return all(_match_val(doc.get(k), c) for k, c in (q or {}).items())


def _project(doc: Dict, proj: Optional[Dict]) -> Dict:
    if not proj:
        return copy.deepcopy(doc)
    if any(v for k, v in proj.items() if k != "_id"):
        out = {k: copy.deepcopy(doc[k]) for k, v in proj.items() if v and k in doc}
        if proj.get("_id", 1):
            out["_id"] = doc.get("_id")
        return out
    return {k: copy.deepcopy(v) for k, v in doc.items() if proj.get(k, 1)}


def _apply(doc: Dict, ops: Dict) -> None:
    for k, v in (ops.get("$set") or {}).items():
        doc[k] = copy.deepcopy(v)
    for k in (ops.get("$unset") or {}):
        doc.pop(k, None)
    for k, v in (ops.get("$inc") or {}).items():
        doc[k] = (doc.get(k) or 0) + v


class _Res:
    def __init__(self, n=0, inserted_id=None):
        self.deleted_count = self.modified_count = n
        self.inserted_id = inserted_id


class _Cursor:
    def __init__(self, docs):
        self.docs = docs

    async def to_list(self, n=None):
        return self.docs[:n] if n else self.docs


class MemCollection:
    def __init__(self, name: str, log: List, docs: Optional[List[Dict]] = None):
        self.name, self.log, self.docs = name, log, [dict(d) for d in (docs or [])]

    def _rec(self, op, *args):
        self.log.append({"coll": self.name, "op": op, "args": copy.deepcopy(list(args))})

    async def find_one(self, q=None, proj=None):
        d = next((d for d in self.docs if matches(d, q)), None)
        return _project(d, proj) if d else None

    def find(self, q=None, proj=None):
        return _Cursor([_project(d, proj) for d in self.docs if matches(d, q)])

    async def insert_one(self, doc):
        doc.setdefault("_id", f"mem_{next(_ids)}")
        self.docs.append(copy.deepcopy(doc))
        self._rec("insert_one", doc)
        return _Res(inserted_id=doc["_id"])

    async def insert_many(self, docs):
        for d in docs:
            d.setdefault("_id", f"mem_{next(_ids)}")
        self.docs += copy.deepcopy(list(docs))
        self._rec("insert_many", list(docs))
        return _Res(len(docs))

    async def update_one(self, q, ops, upsert=False):
        d = next((d for d in self.docs if matches(d, q)), None)
        if d is None and upsert:
            d = {k: v for k, v in q.items() if not isinstance(v, dict)}
            self.docs.append(d)
        if d is not None:
            _apply(d, ops)
        self._rec("update_one", q, ops, upsert)
        return _Res(1 if d is not None else 0)

    async def update_many(self, q, ops):
        hit = [d for d in self.docs if matches(d, q)]
        for d in hit:
            _apply(d, ops)
        self._rec("update_many", q, ops)
        return _Res(len(hit))

    async def delete_many(self, q):
        keep = [d for d in self.docs if not matches(d, q)]
        n = len(self.docs) - len(keep)
        self.docs = keep
        self._rec("delete_many", q)
        return _Res(n)


class MemDB:
    """Mongo-Ersatz mit Schreib-Protokoll (nur die vom Lab genutzten Operatoren)."""

    def __init__(self, snapshot: Optional[Dict[str, List[Dict]]] = None):
        self.log: List[Dict] = []
        self._colls: Dict[str, MemCollection] = {}
        for name, docs in (snapshot or {}).items():
            self._colls[name] = MemCollection(name, self.log, docs)

    def __getitem__(self, name):
        if name not in self._colls:
            self._colls[name] = MemCollection(name, self.log)
        return self._colls[name]

    def __getattr__(self, name):
        if name.startswith("_") or name == "log":
            raise AttributeError(name)
        return self[name]


def _sid(v):
    return str(v) if v is not None and not isinstance(v, (str, int, float)) else v


async def build_snapshot(db, asset_classes: List[str]) -> Dict:
    """Server: Lab-Zustand + Edges der Klassen + Playbook-Bibliothek."""
    from services import ai_playbook
    from services.setup_backtest import runner
    pb = await ai_playbook.refresh(db)
    state = await runner.load_state(db)
    edge_docs = await db[edges.COLLECTION].find(
        {"asset_class": {"$in": list(asset_classes)}}, {"oos_trades": 0}).to_list(5000)
    for d in edge_docs:
        d["_id"] = _sid(d.get("_id"))
    state = {**state, "_id": runner.STATE_ID}
    import json
    snap = {"collections": {edges.COLLECTION: edge_docs, "settings": [state]},
            "playbook": pb, "library": ai_playbook.all_setups()}
    return json.loads(json.dumps(snap, default=str))  # JSON-sicher (ObjectId/datetime)


def _oid(v):
    from bson import ObjectId
    if isinstance(v, str) and len(v) == 24 and ObjectId.is_valid(v):
        return ObjectId(v)
    return v


def _fix_ids(q: Any, idmap: Dict) -> Any:
    """_id-Werte zurück in ObjectIds bzw. neu eingefügte Mem-IDs übersetzen."""
    if not isinstance(q, dict) or "_id" not in q:
        return q
    q = dict(q)
    cond = q["_id"]
    conv = lambda x: idmap.get(x, _oid(x))  # noqa: E731
    q["_id"] = {**cond, "$in": [conv(x) for x in cond.get("$in", [])]} if isinstance(cond, dict) else conv(cond)
    return q


async def replay(db, log: List[Dict]) -> int:
    """Server: protokollierte Schreibzugriffe des Workers auf Mongo anwenden."""
    idmap: Dict[str, Any] = {}
    n = 0
    for e in log or []:
        coll, op, args = db[e["coll"]], e["op"], e.get("args") or []
        if op == "insert_one":
            doc = dict(args[0])
            mem = doc.pop("_id", None)
            if not str(mem).startswith("mem_"):
                doc["_id"] = _oid(mem)
            res = await coll.insert_one(doc)
            if str(mem).startswith("mem_"):
                idmap[mem] = res.inserted_id
        elif op == "insert_many":
            docs = []
            for d in args[0]:
                d = dict(d)
                if str(d.get("_id", "")).startswith("mem_"):
                    d.pop("_id")
                docs.append(d)
            if docs:
                await coll.insert_many(docs)
        elif op == "update_one":
            await coll.update_one(_fix_ids(args[0], idmap), args[1], upsert=bool(args[2]) if len(args) > 2 else False)
        elif op == "update_many":
            await coll.update_many(_fix_ids(args[0], idmap), args[1])
        elif op == "delete_many":
            await coll.delete_many(_fix_ids(args[0], idmap))
        else:
            continue
        n += 1
    return n


async def run_on_worker(job_id: str, params: Dict, snapshot: Dict) -> Dict:
    """Worker: run_job gegen MemDB, Playbook aus dem Schnappschuss."""
    from services import ai_playbook
    from services.setup_backtest import runner
    db = MemDB(snapshot.get("collections"))
    runner.SYMBOL_DAYS_BUDGET = 10 ** 9  # eigener PC: kein Cloud-RAM-Deckel (Worker-RAM-Limit greift)
    pb, lib = snapshot.get("playbook") or {}, snapshot.get("library") or {}

    async def _refresh(_db, force=False):
        return pb
    orig = (ai_playbook.refresh, ai_playbook.all_setups, ai_playbook.invalidate_cache)
    ai_playbook.refresh = _refresh
    ai_playbook.all_setups = lambda: lib
    ai_playbook.invalidate_cache = lambda: None
    ai = {k: params.get(k) for k in ("ai_revise", "ai_rounds", "target_passed") if k in params}
    ai["ai_revise"] = False  # KI-Revision braucht die Cloud
    try:
        await runner.run_job(job_id, db, params["asset_classes"], params["days"], params["mode"],
                             params.get("setups"), trigger=params.get("trigger") or "manual", ai=ai)
    finally:
        ai_playbook.refresh, ai_playbook.all_setups, ai_playbook.invalidate_cache = orig
    return {"log": db.log}
